from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path
from uuid import UUID

from shared.protocol.enums import LauncherState
from shared.protocol.models import ServerProperties

from launcher.config import LauncherSettings
from launcher.minecraft.process import ProcessHandle
from launcher.minecraft.vanilla import VanillaMinecraftRuntime
from launcher.state.machine import LauncherStateMachine
from launcher.state.persist import StateStore
from launcher.sync.snapshot import create_snapshot, restore_snapshot
from launcher.storage import build_storage
from launcher.storage.base import WorldStorage
from launcher.stopfile import StopFileWatcher

logger = logging.getLogger(__name__)

WORLD_UUID = UUID("00000000-0000-0000-0000-000000000001")

PID_FILE_NAME = "nomad.pid"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nomad")
    subparsers = parser.add_subparsers(dest="command", required=True)

    play = subparsers.add_parser("play", help="install, start, and host a Minecraft server")
    play.add_argument("--world-dir", type=Path, help="world directory (default: <data>/worlds/world)")
    play.add_argument("--accept-eula", action="store_true", help="agree to the Minecraft EULA")

    stop = subparsers.add_parser("stop", help="gracefully stop a running server")
    stop.add_argument("--world-dir", type=Path)
    stop.add_argument("--world", type=UUID, help="controller world id (stops the agent hosting it)")

    subparsers.add_parser("status", help="show current launcher state")

    snap = subparsers.add_parser("snapshot", help="snapshot management")
    snap_sub = snap.add_subparsers(dest="snap_command", required=True)

    snap_create = snap_sub.add_parser("create", help="create a new snapshot")
    snap_create.add_argument("--world-dir", type=Path)

    snap_sub.add_parser("list", help="list stored snapshots")

    snap_restore = snap_sub.add_parser("restore", help="restore a snapshot")
    snap_restore.add_argument("--version", type=int, required=True)
    snap_restore.add_argument("--world-dir", type=Path)

    host = subparsers.add_parser("host", help="host a world from the controller (Stage 5)")
    host.add_argument("--world", type=UUID, required=True, help="world id")
    host.add_argument("--accept-eula", action="store_true", help="agree to the Minecraft EULA")

    worlds = subparsers.add_parser("worlds", help="list worlds from the controller")

    join = subparsers.add_parser("join", help="join an active host's server")
    join.add_argument("--world", type=UUID, required=True, help="world id")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings = LauncherSettings()
    store = StateStore(settings.state_file)
    machine = LauncherStateMachine(store)
    runtime = VanillaMinecraftRuntime()
    world_dir = args.world_dir if getattr(args, "world_dir", None) else settings.worlds_dir / "world"

    try:
        if args.command == "play":
            return _play(settings, machine, runtime, world_dir, args)
        if args.command == "stop":
            if getattr(args, "world", None):
                stop_world_dir = settings.worlds_dir / str(args.world)
            else:
                stop_world_dir = world_dir
            return _stop(stop_world_dir)
        if args.command == "status":
            return _status(machine)
        if args.command == "snapshot":
            storage = build_storage(settings)
            if args.snap_command == "create":
                return _snapshot_create(settings, machine, storage, world_dir)
            if args.snap_command == "list":
                return _snapshot_list(storage)
            if args.snap_command == "restore":
                return _snapshot_restore(machine, storage, world_dir, args.version)
        if args.command == "host":
            return _host(settings, machine, runtime, args)
        if args.command == "worlds":
            return _worlds(settings)
        if args.command == "join":
            return _join(settings, args)
        parser.error("unknown command")
        return 2
    except KeyboardInterrupt:
        logger.info("interrupted")
        return 1
    except Exception as exc:
        logger.error("command failed: %s", exc)
        return 1


def _play(
    settings: LauncherSettings,
    machine: LauncherStateMachine,
    runtime: VanillaMinecraftRuntime,
    world_dir: Path,
    args: argparse.Namespace,
) -> int:
    if not args.accept_eula and not settings.eula_accepted:
        logger.error("Minecraft EULA not accepted. Pass --accept-eula or set NOMAD_EULA_ACCEPTED=true.")
        return 1

    world_dir.mkdir(parents=True, exist_ok=True)
    _write_pid_file(world_dir)

    try:
        machine.start_at(LauncherState.CHECK_AUTH)
        machine.transition(LauncherState.CHECK_WORLD)
        machine.transition(LauncherState.ACQUIRE_HOST)
        machine.transition(LauncherState.DOWNLOAD)

        jar_path = runtime.install(settings.minecraft_version, settings.install_dir)
        logger.info("server jar: %s", jar_path)
        ok, reason = runtime.validate(settings.install_dir, settings.java_path)
        if not ok:
            machine.transition(LauncherState.ERROR)
            logger.error("validation failed: %s", reason)
            return 1

        machine.transition(LauncherState.VALIDATE)
        machine.transition(LauncherState.START_SERVER)

        properties = ServerProperties()
        handle = runtime.start(
            settings.install_dir, world_dir, properties, settings.java_path, settings.memory
        )
        machine.update_context(world_id=str(WORLD_UUID), local_world_dir=str(world_dir))
        machine.transition(LauncherState.HOSTING)

        logger.info("server started; press Ctrl-C or run 'nomad stop' to stop")
        watcher = StopFileWatcher(world_dir).start()
        try:
            for line in handle.tail_logs():
                print(line, flush=True)
                if watcher.stop_event.is_set():
                    logger.info("stop requested via marker file")
                    break
                if not handle.is_running():
                    logger.warning("server process exited unexpectedly")
                    machine.transition(LauncherState.RECOVER)
                    return 1
        except KeyboardInterrupt:
            logger.info("stop signal received")
        finally:
            watcher.cleanup()

        _graceful_shutdown(machine, runtime, handle, settings.stop_timeout_seconds)
        return 0
    finally:
        _remove_pid_file(world_dir)


def _graceful_shutdown(
    machine: LauncherStateMachine,
    runtime: VanillaMinecraftRuntime,
    handle: ProcessHandle,
    stop_timeout_seconds: int,
) -> None:
    machine.transition(LauncherState.STOPPING)
    logger.info("sending graceful stop to server")
    runtime.stop(handle)
    try:
        handle.wait(timeout=stop_timeout_seconds)
        logger.info("server stopped cleanly")
    except Exception:
        logger.error("server did not stop within timeout")
        machine.transition(LauncherState.ERROR)
        return
    machine.transition(LauncherState.RELEASE_LEASE)
    machine.transition(LauncherState.IDLE)


def _stop(world_dir: Path) -> int:
    """Request a graceful stop via the marker file, with a POSIX SIGINT
    fallback for processes sharing this console."""
    watcher = StopFileWatcher(world_dir)
    watcher.request_stop()
    logger.info("stop requested via marker file")

    pid_file = world_dir / PID_FILE_NAME
    if pid_file.exists() and os.name == "posix":
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGINT)
            logger.info("also sent SIGINT to launcher pid %d", pid)
        except ProcessLookupError:
            logger.warning("stale pid file found; removing it")
            _remove_pid_file(world_dir)
        except (ValueError, OSError) as exc:
            logger.warning("could not signal launcher: %s (marker file still works)", exc)
    return 0


def _status(machine: LauncherStateMachine) -> int:
    context = machine.get_context()
    print(f"State: {context['state']}")
    print(f"World: {context.get('world_id') or '-'}")
    print(f"Base version: {context.get('base_version') or '-'}")
    return 0


def _host(
    settings: LauncherSettings,
    machine: LauncherStateMachine,
    runtime: VanillaMinecraftRuntime,
    args: argparse.Namespace,
) -> int:
    if not args.accept_eula and not settings.eula_accepted:
        logger.error("Minecraft EULA not accepted. Pass --accept-eula or set NOMAD_EULA_ACCEPTED=true.")
        return 1
    if not settings.controller_token:
        logger.error("no controller token. Set NOMAD_CONTROLLER_TOKEN or login first.")
        return 1

    from launcher.agent import HostAgent
    from launcher.controller import ControllerClient

    client = ControllerClient(settings.controller_url, settings.controller_token)
    try:
        agent = HostAgent(settings, machine, client, args.world, runtime)
        return agent.host()
    finally:
        client.close()


def _worlds(settings: LauncherSettings) -> int:
    if not settings.controller_token:
        logger.error("no controller token. Set NOMAD_CONTROLLER_TOKEN or login first.")
        return 1

    from launcher.controller import ControllerClient

    client = ControllerClient(settings.controller_url, settings.controller_token)
    try:
        worlds = client.list_worlds()
        if not worlds:
            print("No worlds.")
            return 0
        for world in worlds:
            host = world.get("current_host_name") or world.get("current_host") or "-"
            print(
                f"{world['id']}  {world['name']:20s}  {world['status']:10s}  "
                f"v{world.get('latest_version') or 0}  host={host}"
            )
        return 0
    finally:
        client.close()


def _join(settings: LauncherSettings, args: argparse.Namespace) -> int:
    if not settings.controller_token:
        logger.error("no controller token. Set NOMAD_CONTROLLER_TOKEN or login first.")
        return 1

    from launcher.controller import ControllerClient
    from launcher.networking import ConnectionInfo

    client = ControllerClient(settings.controller_url, settings.controller_token)
    try:
        world = client.get_world(args.world)
        if world.get("status") != "hosting":
            print(f"World is not currently hosted (status: {world.get('status')}).")
            return 1

        info = ConnectionInfo.from_dict(world.get("connection"))
        if info is None:
            print("Host has not published connection info yet.")
            return 1

        if info.mode == "relay" and info.relay_token:
            target = f"relay://{info.relay_host}:{info.relay_port} token={info.relay_token[:12]}..."
            print(f"Join via relay: {target}")
        else:
            print(f"Join directly: {info.address or 'unknown address'}")
        print(f"World: {world['name']}  Host: {world.get('current_host_name') or '-'}")

        # For the MVP, `join` prints the connection target. Launching the
        # Minecraft client and dialing the relay pipe is a GUI-era step.
        return 0
    finally:
        client.close()


def _snapshot_create(
    settings: LauncherSettings,
    machine: LauncherStateMachine,
    storage: WorldStorage,
    world_dir: Path,
) -> int:
    machine.start_at(LauncherState.SNAPSHOTTING)
    latest = storage.get_latest_version()
    metadata = create_snapshot(
        storage,
        world_dir,
        settings.minecraft_version,
        created_by=WORLD_UUID,
        base_version=latest.version if latest else None,
    )
    print(f"Created snapshot v{metadata.version} (sha {metadata.sha256[:12]})")
    machine.transition(LauncherState.IDLE)
    return 0


def _snapshot_list(storage: WorldStorage) -> int:
    versions = storage.list_versions()
    if not versions:
        print("No snapshots stored.")
        return 0
    for metadata in versions:
        sha = metadata.sha256[:12] if metadata.sha256 else "-"
        print(
            f"v{metadata.version}  {metadata.created_at.isoformat()}  "
            f"mc={metadata.minecraft_version}  sha={sha}"
        )
    return 0


def _snapshot_restore(
    machine: LauncherStateMachine,
    storage: WorldStorage,
    world_dir: Path,
    version: int,
) -> int:
    machine.start_at(LauncherState.DOWNLOAD)
    metadata = restore_snapshot(storage, world_dir, version)
    print(f"Restored v{metadata.version} into {world_dir}")
    machine.transition(LauncherState.IDLE)
    return 0


def _write_pid_file(world_dir: Path) -> None:
    (world_dir / PID_FILE_NAME).write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid_file(world_dir: Path) -> None:
    pid_file = world_dir / PID_FILE_NAME
    if pid_file.exists():
        pid_file.unlink()


if __name__ == "__main__":
    sys.exit(main())
