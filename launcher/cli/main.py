"""CLI for the R2-backed Nomad launcher.

Commands:
    nomad config                 show R2 settings status
    nomad worlds                 list worlds in the local registry
    nomad new <name>             create a world in the local registry
    nomad join <world-id>        add a friend's world by id
    nomad play <world-id>        host a world (lease -> pull -> boot -> push)
    nomad status <world-id>      show who hosts a world right now
"""

from __future__ import annotations

import argparse
import logging
import sys

from launcher.config import LauncherSettings, load_settings_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nomad", description="Distributed Minecraft hosting via R2")
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

    status = subparsers.add_parser("status", help="show who currently hosts a world")
    status.add_argument("world_id", help="world id")

    return parser


def _settings() -> LauncherSettings:
    return load_settings_file(LauncherSettings())


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
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
        parser.error("unknown command")
        return 2
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("interrupted")
        return 1
    except Exception as exc:
        logging.getLogger(__name__).error("command failed: %s", exc)
        return 1


def _configured(settings: LauncherSettings) -> bool:
    return bool(
        settings.r2_account_id
        and settings.r2_access_key
        and settings.r2_secret_key
        and settings.r2_bucket
    )


def _store(settings: LauncherSettings):
    from launcher.cloud import S3Client, WorldStore

    return WorldStore(
        S3Client(
            settings.endpoint_url,
            settings.r2_access_key,
            settings.r2_secret_key,
            settings.r2_bucket,
        ),
        player_name=settings.player_name,
    )


def _config() -> int:
    settings = _settings()
    configured = _configured(settings)
    print(f"R2 configured: {'yes' if configured else 'no'}")
    print(f"  account_id: {settings.r2_account_id or '-'}")
    print(f"  bucket:     {settings.r2_bucket or '-'}")
    print(f"  endpoint:   {settings.endpoint_url}")
    print(f"  player:     {settings.player_name}")
    print(f"  address:    {settings.public_address or '(not published)'}")
    if not configured:
        print()
        print("Set NOMAD_R2_ACCOUNT_ID / NOMAD_R2_ACCESS_KEY / NOMAD_R2_SECRET_KEY /")
        print("NOMAD_R2_BUCKET, or run the launcher GUI and fill in R2 Settings.")
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
    if not _configured(settings):
        print("R2 not configured. Run 'nomad config' to see what to set.")
        return 1

    registry = WorldRegistry(settings.registry_file)
    world = registry.get(args.world_id)
    if world is None:
        print(f"Unknown world {args.world_id}. Add it first with 'nomad join'.")
        return 1

    print(f"Hosting {world['name']}… press Ctrl-C to stop and push the world.")
    agent = HostAgent(settings, _store(settings), world)
    return agent.host()


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
