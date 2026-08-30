from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from shared.protocol.enums import LauncherState

from launcher.controller import ControllerClient, ControllerError
from launcher.minecraft.vanilla import VanillaMinecraftRuntime
from launcher.state.machine import LauncherStateMachine
from launcher.stopfile import StopFileWatcher
from launcher.sync.snapshot import pull_latest_world, push_world_snapshot

logger = logging.getLogger(__name__)


@dataclass
class HostSession:
    """Everything the host agent needs while it is hosting a world."""

    world_id: UUID
    lease_id: UUID
    base_version: int
    world_dir: Path
    heartbeat_interval: float = 20.0
    stop_event: threading.Event = field(default_factory=threading.Event)
    heartbeat_error: Exception | None = None
    _heartbeat_thread: threading.Thread | None = None

    def start_heartbeats(self, client: ControllerClient) -> None:
        def run() -> None:
            while not self.stop_event.is_set():
                self.stop_event.wait(self.heartbeat_interval)
                if self.stop_event.is_set():
                    break
                try:
                    client.heartbeat(self.world_id, self.lease_id)
                    logger.debug("heartbeat ok")
                except ControllerError as exc:
                    logger.warning("heartbeat rejected: %s", exc)
                    self.heartbeat_error = exc
                    return

        self._heartbeat_thread = threading.Thread(target=run, daemon=True)
        self._heartbeat_thread.start()

    def stop_heartbeats(self) -> None:
        self.stop_event.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5)


class HostAgent:
    """Drives the launcher through the full host lifecycle against the controller.

    States: CHECK_WORLD -> ACQUIRE_HOST -> DOWNLOAD -> VALIDATE -> START_SERVER
    -> HOSTING (heartbeats) -> STOPPING -> SNAPSHOTTING -> UPLOADING ->
    RELEASE_LEASE -> IDLE. Any failure moves to ERROR/RECOVER without ever
    touching the cloud's last known-good version.
    """

    def __init__(
        self,
        settings,
        machine: LauncherStateMachine,
        client: ControllerClient,
        world_id: UUID,
        runtime: VanillaMinecraftRuntime,
    ) -> None:
        self.settings = settings
        self.machine = machine
        self.client = client
        self.world_id = world_id
        self.runtime = runtime
        self.world_dir = settings.worlds_dir / str(world_id)
        self.session: HostSession | None = None
        self.stop_requested = threading.Event()
        self.stop_watcher = StopFileWatcher(self.world_dir)

    # --- entry ----------------------------------------------------------

    def host(self) -> int:
        # Restart-safe: a previous crash may have left the machine mid-flow.
        # Starting a fresh session always begins from the top.
        self.machine.start_at(LauncherState.CHECK_AUTH)
        self.machine.transition(LauncherState.CHECK_WORLD)
        self.machine.transition(LauncherState.ACQUIRE_HOST)
        # acquire is atomic on the controller; the world's status is only a hint.
        acquired = self.client.acquire_host(self.world_id)
        if not acquired.get("acquired"):
            current = acquired.get("current_host_name") or acquired.get("current_host")
            logger.info("host already exists: %s", current)
            self.machine.transition(LauncherState.HOST_EXISTS)
            return 1

        lease_id = UUID(acquired["lease_id"])
        self.machine.update_context(world_id=str(self.world_id), lease_id=str(lease_id))
        logger.info("acquired host lease %s", lease_id)

        self.machine.transition(LauncherState.DOWNLOAD)
        base_version = pull_latest_world(self.client, self.world_id, self.world_dir)
        self.machine.update_context(base_version=base_version)

        self.machine.transition(LauncherState.VALIDATE)
        jar_path = self.runtime.install(self.settings.minecraft_version, self.settings.install_dir)
        ok, reason = self.runtime.validate(self.settings.install_dir, self.settings.java_path)
        if not ok:
            logger.error("server validation failed: %s", reason)
            self.machine.transition(LauncherState.ERROR)
            return 1

        self.machine.transition(LauncherState.START_SERVER)
        self.machine.update_context(local_world_dir=str(self.world_dir))
        handle = self.runtime.start(
            self.settings.install_dir,
            self.world_dir,
            self.settings.server_properties(),
            self.settings.java_path,
            self.settings.memory,
        )

        self.session = HostSession(
            world_id=self.world_id,
            lease_id=lease_id,
            base_version=base_version,
            world_dir=self.world_dir,
            heartbeat_interval=self.settings.heartbeat_interval_seconds,
        )
        self.machine.transition(LauncherState.HOSTING)
        self.session.start_heartbeats(self.client)
        self._publish_connection_info()
        self.stop_watcher.start()
        logger.info("hosting world %s (base v%d)", self.world_id, base_version)

        try:
            while (
                handle.is_running()
                and self.session.heartbeat_error is None
                and not self.stop_requested.is_set()
                and not self.stop_watcher.stop_event.is_set()
            ):
                time.sleep(0.5)
            if self.session.heartbeat_error is not None:
                logger.error("lease lost: %s", self.session.heartbeat_error)
                self.machine.transition(LauncherState.RECOVER)
                return 1
            if not handle.is_running():
                logger.warning("server process exited unexpectedly")
                self.machine.transition(LauncherState.RECOVER)
                return 1
            if self.stop_requested.is_set() or self.stop_watcher.stop_event.is_set():
                logger.info("stop requested")
        finally:
            self.session.stop_heartbeats()
            self.stop_watcher.cleanup()

        self._shutdown(handle)
        return 0

    # --- connection info --------------------------------------------------

    def _publish_connection_info(self) -> None:
        """Report how players can reach the local server.

        Tries direct first (LAN/port-forwarded address). If the relay is
        enabled, attach outbound and fall back to the relay token.
        """
        from launcher.networking import attach_relay, build_direct_info

        info = build_direct_info(self.settings.server_port)
        if info is not None and not self.settings.relay_enabled:
            logger.info("publishing direct connection %s", info.address)
            self.client.update_connection(self.world_id, info.to_dict())
            return

        if self.settings.relay_enabled:
            try:
                token = attach_relay(self.settings.relay_host, self.settings.relay_port, self.settings.server_port)
                self.client.update_connection(
                    self.world_id,
                    {
                        "mode": "relay",
                        "relay_token": token,
                        "relay_host": self.settings.relay_host,
                        "relay_port": self.settings.relay_port,
                    },
                )
                logger.info("publishing relay connection token=%s", token[:12])
                return
            except Exception as exc:
                logger.error("relay attachment failed: %s", exc)

        # No direct info and no relay: publish direct anyway so status shows
        # something, or fall back to whatever we can.
        if info is not None:
            self.client.update_connection(self.world_id, info.to_dict())

    # --- shutdown path ---------------------------------------------------

    def _shutdown(self, handle) -> None:
        self.machine.transition(LauncherState.STOPPING)
        logger.info("sending graceful stop to server")
        self.runtime.stop(handle)
        try:
            handle.wait(timeout=self.settings.stop_timeout_seconds)
            logger.info("server stopped cleanly")
        except Exception:
            logger.error("server did not stop within timeout")
            self.machine.transition(LauncherState.ERROR)
            return

        assert self.session is not None
        self.machine.transition(LauncherState.SNAPSHOTTING)
        self.machine.transition(LauncherState.UPLOADING)
        try:
            push_world_snapshot(
                self.client,
                self.world_id,
                self.session.lease_id,
                self.world_dir,
                self.session.base_version,
                self.settings.minecraft_version,
            )
        except ControllerError as exc:
            logger.error("snapshot upload rejected: %s", exc)
            self.machine.transition(LauncherState.ERROR)
            return

        self.machine.transition(LauncherState.RELEASE_LEASE)
        self.client.release_host(self.world_id, self.session.lease_id)
        self.machine.transition(LauncherState.IDLE)
        logger.info("host session complete; world returned to sleep")
