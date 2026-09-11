"""CLI for the Nomad launcher.

Commands:
    nomad config                 show current settings
    nomad storage set|status     choose/inspect the storage backend
    nomad manifest init|check    author/preview the inclusion manifest
    nomad worlds                 list worlds in the local registry
    nomad new <name>             create a world in the local registry
    nomad join <world-id>        add a friend's world by id
    nomad play <world-id>        host a world (lease -> pull -> boot -> push)
    nomad status <world-id>      show who hosts a world right now
    nomad usage                  approximate local usage counters
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from launcher.config import LauncherSettings, load_settings_file


def build_parser() -> argparse.ArgumentParser:
    from launcher import __version__

    parser = argparse.ArgumentParser(prog="nomad", description="Distributed Minecraft hosting via pluggable storage")
    parser.add_argument("--version", action="version", version=f"nomad {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config", help="show current R2 settings")

    subparsers.add_parser("worlds", help="list worlds in the local registry")

    new = subparsers.add_parser("new", help="create a world in the local registry")
    new.add_argument("name", help="world name")
    new.add_argument("--version", default=None, help="Minecraft version (default: launcher default)")

    join = subparsers.add_parser("join", help="add a friend's world by its id")
    join.add_argument("world_id", help="world id (a UUID)")
    join.add_argument("--name", default=None, help="display name")

    play = subparsers.add_parser("play", help="host a world until stopped (Ctrl-C to stop)")
    play.add_argument("world_id", help="world id")
    play.add_argument("--accept-eula", action="store_true", help="agree to the Minecraft EULA")
    play.add_argument(
        "--tunnel",
        action="store_true",
        help="publish the game port through NOMAD_SSH_TARGET (no router config)",
    )

    status = subparsers.add_parser("status", help="show who currently hosts a world")
    status.add_argument("world_id", help="world id")

    subparsers.add_parser("usage", help="show approximate local R2 usage counters")

    manifest = subparsers.add_parser("manifest", help="inclusion manifest (nomad.json) tools")
    manifest_sub = manifest.add_subparsers(dest="manifest_command", required=True)
    manifest_init = manifest_sub.add_parser("init", help="write a starter nomad.json")
    manifest_init.add_argument("directory", nargs="?", default=".", help="server directory (default: .)")
    manifest_init.add_argument("--name", default=None, help="manifest name")
    manifest_check = manifest_sub.add_parser("check", help="preview what the manifest syncs")
    manifest_check.add_argument("directory", nargs="?", default=".", help="server directory (default: .)")

    storage = subparsers.add_parser("storage", help="choose and inspect the storage backend")
    storage_sub = storage.add_subparsers(dest="storage_command", required=True)
    storage_set = storage_sub.add_parser("set", help="select a backend direction")
    storage_set.add_argument(
        "backend", choices=["r2", "git", "vps", "ssh"], help="which storage direction to use"
    )
    storage_sub.add_parser("status", help="show the active backend and its settings")

    ssh = subparsers.add_parser("ssh", help="manage the Nomad SSH identity")
    ssh_sub = ssh.add_subparsers(dest="ssh_command", required=True)
    ssh_setup = ssh_sub.add_parser(
        "setup", help="set up a machine end to end: key, folder, verify, save"
    )
    ssh_setup.add_argument("target", help="user@host or host")
    ssh_setup.add_argument("--port", type=int, default=None, help="ssh port (default 22)")
    ssh_setup.add_argument(
        "--folder", default="nomad-worlds", help="remote folder for world data"
    )
    ssh_init = ssh_sub.add_parser("init", help="generate a Nomad key and print provisioning steps")
    ssh_init.add_argument("--user", default=None, help="remote user (default: from NOMAD_SSH_TARGET)")
    ssh_sub.add_parser("show", help="show the identity, target and key fingerprint")

    return parser


def _settings() -> LauncherSettings:
    from launcher import audit

    settings = load_settings_file(LauncherSettings())
    audit.configure(settings)
    return settings


def main(argv: list[str] | None = None) -> int:
    from launcher.secrets import install_log_redaction

    # Scrub credential-shaped text from every log record, regardless of source.
    install_log_redaction()
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    install_log_redaction()  # basicConfig may add a handler after the first call
    try:
        if args.command == "config":
            return _config()
        if args.command == "worlds":
            return _worlds()
        if args.command == "new":
            return _new(args)
        if args.command == "join":
            return _join(args)
        if args.command == "ssh":
            if args.ssh_command == "setup":
                return _ssh_setup(args)
            if args.ssh_command == "init":
                return _ssh_init(args)
            if args.ssh_command == "show":
                return _ssh_show()
        if args.command == "play":
            return _play(args)
        if args.command == "status":
            return _status(args)
        if args.command == "usage":
            return _usage(args)
        if args.command == "manifest":
            if args.manifest_command == "init":
                return _manifest_init(args)
            if args.manifest_command == "check":
                return _manifest_check(args)
        if args.command == "storage":
            if args.storage_command == "set":
                return _storage_set(args)
            if args.storage_command == "status":
                return _storage_status()
        parser.error("unknown command")
        return 2
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("interrupted")
        return 1
    except Exception as exc:
        logging.getLogger(__name__).error("command failed: %s", exc)
        return 1


def _configured(settings: LauncherSettings) -> bool:
    """True when the selected backend can be constructed from current settings."""
    try:
        _store(settings)
        return True
    except Exception:
        return False


def _store(settings: LauncherSettings):
    from launcher.cloud import UsageCounter
    from launcher.storage import build_store

    return build_store(settings, usage=UsageCounter(settings.usage_file))


def _config() -> int:
    settings = _settings()
    configured = _configured(settings)
    print(f"backend:  {settings.storage_backend}")
    print(f"ready:    {'yes' if configured else 'no'}")
    print(f"player:   {settings.player_name}")
    print(f"address:  {settings.public_address or '(not published)'}")
    print("Use 'nomad storage status' for backend-specific settings.")
    return 0


def _worlds() -> int:
    from launcher.registry import WorldRegistry

    settings = _settings()
    registry = WorldRegistry(settings.registry_file)
    store = _store(settings) if _configured(settings) else None

    worlds = registry.list_worlds()
    if not worlds:
        print("No worlds in the registry. Create one with 'nomad new <name>'.")
        return 0
    for world in worlds:
        hosted = ""
        if store is not None:
            try:
                status = store.status(world["id"])
                hosted = f"hosted by {status['holder']}" if status.get("hosted") else "sleeping"
            except Exception:
                hosted = "unreachable"
        print(
            f"{world['id']}  {world['name']:24s} "
            f"mc={world.get('minecraft_version', '?'):8s} {hosted}"
        )
    return 0


def _new(args: argparse.Namespace) -> int:
    from launcher.registry import WorldRegistry

    settings = _settings()
    world = WorldRegistry(settings.registry_file).add(
        args.name, args.version or settings.minecraft_version
    )
    print(f"Created {world['name']} ({world['id']})")
    print("Share this id with friends so they can 'nomad join' it.")
    return 0


def _join(args: argparse.Namespace) -> int:
    from launcher.registry import WorldRegistry

    settings = _settings()
    registry = WorldRegistry(settings.registry_file)
    world_id = args.world_id.strip()
    if registry.get(world_id) is not None:
        print(f"World {world_id} is already in the registry.")
        return 0
    world = registry.add_with_id(
        world_id,
        name=args.name or world_id[:8],
        minecraft_version=settings.minecraft_version,
    )
    print(f"Added {world['name']} ({world_id}). Host it with 'nomad play' or the launcher.")
    return 0


def _play(args: argparse.Namespace) -> int:
    from launcher.agent import HostAgent
    from launcher.registry import WorldRegistry

    settings = _settings()
    if not args.accept_eula and not settings.eula_accepted:
        print("Minecraft EULA not accepted. Pass --accept-eula or set NOMAD_EULA_ACCEPTED=true.")
        return 1

    registry = WorldRegistry(settings.registry_file)
    world = registry.get(args.world_id)
    if world is None:
        print(f"Unknown world {args.world_id}. Add it first with 'nomad join'.")
        return 1

    try:
        store = _store(settings)
    except Exception as exc:
        print(f"Storage not configured ({settings.storage_backend}): {exc}")
        print("Run 'nomad storage status' to see what to set.")
        return 1

    if args.tunnel:
        settings.ssh_reverse_tunnel = True

    print(f"Hosting {world['name']}... press Ctrl-C to stop and push the world.")
    try:
        return HostAgent(settings, store, world).host()
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()


def _manifest_init(args: argparse.Namespace) -> int:
    from launcher.manifest import DEFAULT_MANIFEST_NAME, ServerManifest, save_manifest

    directory = Path(args.directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / DEFAULT_MANIFEST_NAME
    if path.exists():
        print(f"{path} already exists — edit it directly.")
        return 1

    manifest = ServerManifest(
        name=args.name or directory.name or "server",
        include=[],  # empty = sync everything
        exclude=["*.log", "logs/", "*.tmp"],
    )
    save_manifest(directory, manifest)
    print(f"wrote {path}")
    print("include is empty: syncs everything except exclude patterns.")
    print("For a non-Minecraft game, add a server.command block:")
    print('  "server": {"command": ["./server", "--arg"], "stop_command": "stop", "port": 7777}')
    return 0


def _manifest_check(args: argparse.Namespace) -> int:
    from launcher.manifest import DEFAULT_MANIFEST_NAME, describe, load_manifest

    directory = Path(args.directory).expanduser().resolve()
    manifest = load_manifest(directory)
    if manifest is None:
        print(f"no {DEFAULT_MANIFEST_NAME} in {directory}")
        return 1

    summary = describe(directory, manifest)
    print(f"manifest: {manifest.name}")
    print(f"server:   {'custom command' if manifest.is_generic else 'Minecraft (default)'}")
    if manifest.include:
        print(f"include:  {', '.join(manifest.include)}")
    else:
        print("include:  (everything)")
    if manifest.exclude:
        print(f"exclude:  {', '.join(manifest.exclude)}")
    print(f"would sync: {summary.files} file(s), {summary.bytes / 1_000_000:.2f} MB")
    print(f"excluded:   {summary.excluded} file(s)")
    for rel in summary.sample:
        print(f"  {rel}")
    if summary.files > len(summary.sample):
        print(f"  … and {summary.files - len(summary.sample)} more")
    return 0


def _storage_set(args: argparse.Namespace) -> int:
    from launcher.config import save_settings_file

    settings = _settings()
    settings.storage_backend = args.backend
    save_settings_file(settings)
    print(f"storage backend set to: {args.backend}")
    return 0


def _ssh_setup(args: argparse.Namespace) -> int:
    import getpass
    import sys

    from launcher import ssh_provision
    from launcher.config import save_settings_file

    settings = _settings()
    target = args.target.strip()
    user, _, host = target.rpartition("@")
    if not host:
        host, user = target, ""

    def step(message: str) -> None:
        print(f"  {message}")

    kwargs = {"folder": args.folder, "port": args.port, "on_step": step}
    try:
        try:
            result = ssh_provision.run_setup(settings, host, user, **kwargs)
        except ssh_provision.NeedPassword as exc:
            if not sys.stdin.isatty():
                print("This machine needs a password, but there is no terminal to ask in.")
                print(f"ssh said: {exc.detail}")
                print("Set up key access first, or run this command in a terminal.")
                return 1
            password = getpass.getpass(f"password for {target}: ")
            result = ssh_provision.run_setup(settings, host, user, password=password, **kwargs)
    except ssh_provision.ProvisionError as exc:
        print(f"setup failed: {exc.detail}")
        return 1

    settings.ssh_target = result.target
    settings.ssh_path = result.base
    settings.ssh_port = result.port or 0
    settings.storage_backend = "ssh"
    save_settings_file(settings)

    print()
    print(f"target:      {result.target}")
    print(f"remote base: {result.base}")
    print(f"port:        {result.port or 22}")
    print(f"fingerprint: {result.fingerprint or '(unknown)'}")
    print(f"backend:     ssh (saved)")
    print()
    print("Next: 'nomad storage status' to confirm, then 'nomad play <world>'.")
    return 0


def _ssh_init(args: argparse.Namespace) -> int:
    from launcher.ssh_identity import (
        ensure_keypair,
        fingerprint,
        nomad_key_path,
        provision_commands,
        public_key,
    )

    settings = _settings()
    key = ensure_keypair(
        nomad_key_path(settings), comment=f"nomad@{settings.player_name or 'launcher'}"
    )
    pub = public_key(key)
    user = args.user or (settings.ssh_target.split("@", 1)[0] if "@" in settings.ssh_target else "nomad")

    print(f"key:         {key}")
    print(f"fingerprint: {fingerprint(key) or '(ssh-keygen unavailable)'}")
    print()
    print("public key (install this on the box):")
    print(f"  {pub}")
    print()
    print(f"--- run these on the box as an administrator (creates the '{user}' user) ---")
    for line in provision_commands(user, pub, f"/home/{user}/{settings.ssh_path.strip('/')}"):
        print(f"  {line}")
    print()
    print("Most users do not need any of that. 'nomad ssh setup <user@host>' installs")
    print("this key for your existing account and creates the folder by itself.")
    print("The steps above are only for the isolated dedicated-user layout.")
    print()
    print("Then point Nomad at it:")
    print("  nomad ssh setup <user>@<box-address>")
    return 0


def _ssh_show() -> int:
    from launcher.ssh_identity import fingerprint, nomad_key_path, resolve_key

    settings = _settings()
    resolved = resolve_key(settings)
    print(f"target:      {settings.ssh_target or '(unset; set NOMAD_SSH_TARGET)'}")
    if settings.ssh_key:
        print(f"identity:    {settings.ssh_key} (explicit NOMAD_SSH_KEY)")
    elif resolved:
        print("identity:    Nomad key")
        print(f"path:        {resolved}")
        print(f"fingerprint: {fingerprint(nomad_key_path(settings)) or '(unknown)'}")
    else:
        print("identity:    system ssh default (no Nomad key yet; run 'nomad ssh init')")
    print(f"remote base: {settings.ssh_path}")
    print(f"tunnel:      {'on' if settings.ssh_reverse_tunnel else 'off'}")
    return 0


def _storage_status() -> int:
    from launcher.secrets import redact_url
    from launcher.storage import build_store

    settings = _settings()
    print(f"backend: {settings.storage_backend}")
    if settings.storage_backend == "ssh":
        from launcher.ssh_identity import fingerprint, nomad_key_path, resolve_key

        print(f"  target:   {settings.ssh_target or '(unset; set NOMAD_SSH_TARGET)'}")
        print(f"  path:     {settings.ssh_path}")
        resolved = resolve_key(settings)
        if settings.ssh_key:
            print(f"  key:      {settings.ssh_key} (explicit NOMAD_SSH_KEY)")
        elif resolved:
            print(f"  key:      Nomad key ({fingerprint(nomad_key_path(settings)) or 'fingerprint unavailable'})")
        else:
            print("  key:      (system ssh identity)")
    elif settings.storage_backend == "git":
        print(f"  repo:     {settings.git_repo_dir}")
        # A remote URL often carries a token as userinfo; show host only.
        print(f"  remote:   {redact_url(settings.git_remote_url) or '(local only)'}")
    else:
        endpoint = settings.vps_endpoint_url or settings.r2_endpoint_url
        bucket = settings.vps_bucket or settings.r2_bucket
        print(f"  endpoint: {endpoint or '-'}")
        print(f"  bucket:   {bucket or '-'}")
    try:
        store = build_store(settings)
        print(f"  ready:    yes ({store.name})")
    except Exception as exc:
        print(f"  ready:    no - {exc}")
    return 0


def _usage(args: argparse.Namespace) -> int:
    from launcher.cloud import UsageCounter

    settings = _settings()
    usage = UsageCounter(settings.usage_file).snapshot()
    print("Approximate local R2 usage (free limits: 1M Class A / 10M Class B / 10 GB-month):")
    print(f"  worlds tracked:    {usage['world_count']}")
    print(f"  uploaded bytes:    {usage['uploaded_bytes']:,} ({usage['uploaded_bytes']/1_000_000:.2f} MB)")
    print(f"  uploads:           {usage['upload_count']}")
    print(f"  downloads:         {usage['download_count']}")
    print(f"  api requests:      {usage['api_request_count']}")
    print("Authoritative numbers live in the Cloudflare R2 dashboard.")
    return 0


def _status(args: argparse.Namespace) -> int:
    settings = _settings()
    if not _configured(settings):
        print("R2 not configured. Run 'nomad config'.")
        return 1
    try:
        status = _store(settings).status(args.world_id)
    except Exception as exc:
        print(f"Could not reach world storage: {exc}")
        return 1
    if status.get("hosted"):
        print(f"Hosted by {status['holder']} (expires {status.get('expires_at')})")
        if status.get("address"):
            print(f"Address: {status['address']}")
    else:
        print("Not hosted right now — press play to become the host.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
