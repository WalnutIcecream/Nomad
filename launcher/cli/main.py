"""CLI for the Nomad launcher.

Commands:
    nomad config                 show current settings
    nomad storage set|status|test  choose/inspect/verify the storage backend
    nomad manifest init|check    author/preview the inclusion manifest
    nomad worlds                 list worlds in the local registry
    nomad new <name>             create a world in the local registry
    nomad join <world-id>        add a friend's world by id
    nomad play <world-id>        host a world (lease -> pull -> boot -> push)
    nomad status <world-id>      show who hosts a world right now
    nomad usage                  approximate local usage counters
    nomad gui                    launch the desktop window
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from launcher.config import LauncherSettings, load_settings_file
from launcher.storage import PROVIDERS, provider_label


def build_parser() -> argparse.ArgumentParser:
    from launcher import __version__

    parser = argparse.ArgumentParser(
        prog="nomad", description="Distributed Minecraft hosting over your own object storage"
    )
    parser.add_argument("--version", action="version", version=f"nomad {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config", help="show current storage settings")

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

    status = subparsers.add_parser("status", help="show who currently hosts a world")
    status.add_argument("world_id", help="world id")

    subparsers.add_parser("usage", help="show approximate local usage counters")

    subparsers.add_parser("gui", help="launch the desktop window")

    manifest = subparsers.add_parser("manifest", help="inclusion manifest (nomad.json) tools")
    manifest_sub = manifest.add_subparsers(dest="manifest_command", required=True)
    manifest_init = manifest_sub.add_parser("init", help="write a starter nomad.json")
    manifest_init.add_argument("directory", nargs="?", default=".", help="server directory (default: .)")
    manifest_init.add_argument("--name", default=None, help="manifest name")
    manifest_check = manifest_sub.add_parser("check", help="preview what the manifest syncs")
    manifest_check.add_argument("directory", nargs="?", default=".", help="server directory (default: .)")

    storage = subparsers.add_parser("storage", help="choose, inspect and verify the storage backend")
    storage_sub = storage.add_subparsers(dest="storage_command", required=True)
    storage_set = storage_sub.add_parser("set", help="select where worlds are stored")
    storage_set.add_argument(
        "backend",
        choices=sorted(PROVIDERS),
        help="r2 = Cloudflare R2; server = your own S3-compatible server (Garage)",
    )
    storage_sub.add_parser("status", help="show the active backend and its settings")
    storage_sub.add_parser("test", help="check that the configured storage works")

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
        if args.command == "play":
            return _play(args)
        if args.command == "status":
            return _status(args)
        if args.command == "usage":
            return _usage(args)
        if args.command == "gui":
            return _gui()
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
            if args.storage_command == "test":
                return _storage_test()
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
    print(f"storage:  {provider_label(settings.storage_backend)} ({settings.storage_backend})")
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
        print(f"Storage not configured ({provider_label(settings.storage_backend)}): {exc}")
        print("Run 'nomad storage status' to see what to set.")
        return 1

    print(f"Hosting {world['name']}... press Ctrl-C to stop and push the world.")
    return HostAgent(settings, store, world).host()


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
    print(f"storage set to: {provider_label(args.backend)}")
    if args.backend == "r2":
        print("Set the R2 account id, bucket and keys, or use the launcher's setup wizard.")
    else:
        print("Set NOMAD_SERVER_ENDPOINT, NOMAD_SERVER_BUCKET and the access/secret keys.")
    print("Then verify with 'nomad storage test'.")
    return 0


def _storage_status() -> int:
    settings = _settings()
    print(f"storage: {provider_label(settings.storage_backend)} ({settings.storage_backend})")
    if settings.storage_backend == "r2":
        endpoint = settings.endpoint_url if (settings.r2_account_id or settings.r2_endpoint_url) else ""
        print(f"  endpoint: {endpoint or '(not set)'}")
        print(f"  bucket:   {settings.r2_bucket or '-'}")
    else:
        print(f"  endpoint: {settings.server_endpoint or '-'}")
        print(f"  bucket:   {settings.server_bucket or '-'}")
    try:
        store = _store(settings)
        print(f"  ready:    yes ({store.name})")
    except Exception as exc:
        print(f"  ready:    no - {exc}")
    return 0


def _storage_test() -> int:
    """Verify the configured storage can actually read and write objects."""
    settings = _settings()
    try:
        store = _store(settings)
    except Exception as exc:
        print(f"Storage is not configured yet ({provider_label(settings.storage_backend)}): {exc}")
        return 1
    result = store.test_connection()
    print(result.message)
    if result.detail:
        print(f"technical details: {result.detail}")
    return 0 if result.ok else 1


def _usage(args: argparse.Namespace) -> int:
    from launcher.cloud import UsageCounter

    settings = _settings()
    usage = UsageCounter(settings.usage_file).snapshot()
    print("Approximate local storage usage:")
    print(f"  worlds tracked:    {usage['world_count']}")
    print(f"  uploaded bytes:    {usage['uploaded_bytes']:,} ({usage['uploaded_bytes']/1_000_000:.2f} MB)")
    print(f"  uploads:           {usage['upload_count']}")
    print(f"  downloads:         {usage['download_count']}")
    print(f"  api requests:      {usage['api_request_count']}")
    print("Authoritative numbers live in your storage provider's dashboard.")
    return 0


def _status(args: argparse.Namespace) -> int:
    settings = _settings()
    if not _configured(settings):
        print("Storage not configured. Run 'nomad storage status' to see what to set.")
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


def _gui() -> int:
    """Launch the desktop window (the same code path as ``python -m launcher.ui``)."""
    try:
        from launcher.ui.app import run
    except ImportError:
        print("The desktop window needs PySide6, which is missing or not working.")
        print('Install it with:  python -m pip install -e ".[ui]"')
        return 1
    return run([])


if __name__ == "__main__":
    sys.exit(main())
