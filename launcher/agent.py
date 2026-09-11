"""The R2 host lifecycle: lease -> pull -> boot -> renew -> push -> release.

This is the whole "first player to press Play becomes the host" flow:

1. Acquire the lease in R2 (atomic conditional write). If someone else holds an
   active lease, bail with HOST_EXISTS and the UI offers Join.
2. Pull the latest ``world.tar.gz`` and extract it into the run directory.
3. Install/validate the vanilla server jar and boot it from that directory.
4. Renew the lease on a heartbeat thread for as long as the server runs.
5. On stop: shut the server down (world flushed), tar the world directory,
   upload it as the new shared version, then release the lease — only after the
   upload lands, so the next host can never acquire a half-updated world.
"""

from __future__ import annotations

import logging
import shutil
import tarfile
import threading
import time
from pathlib import Path

from launcher.cloud import CloudError, Lease, LeaseError, WorldStore
from launcher.manifest import ServerManifest, create_archive, load_manifest
from launcher.minecraft.vanilla import VanillaMinecraftRuntime
from launcher.process_runtime import ProcessRuntime
from launcher.ssh_connection import SshConnection
from launcher.sync.worldfolder import ensure_world_level
from launcher.tunnel import ReverseTunnel, TunnelError

logger = logging.getLogger(__name__)


class HostAgent:
    def __init__(
        self,
        settings,
        store: WorldStore,
        world: dict,
        runtime: VanillaMinecraftRuntime | ProcessRuntime | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.world = world
        self.runtime = runtime

        self.world_id = str(world["id"])
        self.run_dir = self.settings.worlds_dir / self.world_id
        self.manifest: ServerManifest | None = None
        self.tunnel: ReverseTunnel | None = None
        self.lease: Lease | None = None
        self.stop_requested = threading.Event()
        self._renew_error: Exception | None = None
        self._renew_thread: threading.Thread | None = None
        self._released = False

    # --- entry ----------------------------------------------------------

    def host(self) -> int:
        """Run the full host session. Returns 0 on clean stop, 1 on failure."""
        minecraft_version = self.world.get("minecraft_version") or self.settings.minecraft_version

        # 1. Bring storage online first (one ssh handshake for the whole
        # session), then the tunnel, then the lease. Ordered so the address we
        # publish is one that actually exists.
        self._open_store()
        try:
            self.tunnel = self._start_tunnel_if_requested()
        except TunnelError as exc:
            logger.error("%s", exc)
            return 1
        published_address = self.tunnel.address if self.tunnel else (
            self.settings.public_address or None
        )

        # 2. Lease.
        logger.info("acquiring host lease for %s…", self.world.get("name"))
        try:
            self.lease = self.store.acquire(self.world_id, address=published_address)
        except LeaseError as exc:
            logger.info("host already exists: %s", exc.detail)
            self._stop_tunnel()
            return 1
        except CloudError as exc:
            logger.error("could not reach world storage: %s", exc.detail)
            self._stop_tunnel()
            return 1
        logger.info("lease acquired (holder=%s)", self.lease.holder)

        try:
            # 2. Pull the latest shared world. The archive lives next to the
            # run dir (not inside it) because extraction clears the run dir.
            self.run_dir.mkdir(parents=True, exist_ok=True)
            archive = self.run_dir.parent / f"{self.world_id}.tar.gz"
            if self.store.download_world(self.world_id, archive):
                self._extract(archive, self.run_dir)
                archive.unlink(missing_ok=True)
            else:
                logger.info("no shared world yet — starting fresh")

            # 3. Resolve how to run this world. A nomad.json manifest makes it
            # game-agnostic (its own command + include/exclude); without one we
            # fall back to the vanilla Minecraft layout/runtime.
            self.manifest = load_manifest(self.run_dir)
            runtime = self._select_runtime()
            self.runtime = runtime

            if self.manifest is None or not self.manifest.is_generic:
                ensure_world_level(self.run_dir)
                minecraft_version = (
                    self.world.get("minecraft_version") or self.settings.minecraft_version
                )
                runtime.install(minecraft_version, self.settings.install_dir)
                ok, reason = runtime.validate(
                    self.settings.install_dir, self.settings.java_path
                )
                if not ok:
                    logger.error("server validation failed: %s", reason)
                    return 1
            else:
                logger.info(
                    "using manifest '%s' (command: %s)",
                    self.manifest.name,
                    " ".join(self.manifest.server_command or []),
                )
                ok, reason = runtime.validate(self.run_dir, self.settings.java_path)
                if not ok:
                    logger.error("server validation failed: %s", reason)
                    return 1

            # 4. Boot.
            handle = runtime.start(
                self.settings.install_dir,
                self.run_dir,
                self.settings.server_properties(),
                self.settings.java_path,
                self.settings.memory,
            )

            # 5. Renew the lease while running.
            self._start_renewer()
            logger.info("hosting %s from %s", self.world.get("name"), self.run_dir)

            try:
                while not self.stop_requested.is_set():
                    if self._renew_error is not None:
                        logger.error("lease lost: %s", self._renew_error)
                        self._stop_server(handle)
                        return 1
                    if not handle.is_running():
                        logger.warning("server process exited unexpectedly")
                        self._stop_server(handle)
                        return 1
                    time.sleep(0.5)
            finally:
                self._stop_renewer()
                if handle.is_running():
                    self._stop_server(handle)

            # 6. Push + release.
            self._upload_and_release()
            return 0
        finally:
            # On any error/early-return path the lease must not stay held
            # forever. Release now if we still hold it (expiry frees it if the
            # release itself fails).
            if self.lease is not None and not self._released:
                try:
                    self.store.release(self.world_id, self.lease)
                except Exception:
                    logger.warning("could not release lease (expiry will free it)")
            self._stop_tunnel()
            self._close_store()

    # --- storage connection ----------------------------------------------

    def _open_store(self) -> None:
        """Establish the storage connection once for the whole session.

        Non-fatal: if the pre-open fails, individual operations still try (and
        report their own error), so a transient handshake problem does not
        abort the run before it starts.
        """
        opener = getattr(self.store, "open", None)
        if not callable(opener):
            return
        try:
            opener()
        except Exception as exc:
            logger.warning("could not pre-open storage connection: %s", exc)

    def _close_store(self) -> None:
        closer = getattr(self.store, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception as exc:
                logger.debug("storage close failed: %s", exc)

    # --- reverse tunnel --------------------------------------------------

    def _start_tunnel_if_requested(self) -> ReverseTunnel | None:
        """Open the reverse tunnel when enabled; None when not configured."""
        if not getattr(self.settings, "ssh_reverse_tunnel", False):
            return None
        if not getattr(self.settings, "ssh_target", ""):
            raise TunnelError("reverse tunnel needs NOMAD_SSH_TARGET (user@host)")

        connection = SshConnection.build(self.settings)
        local_port = self.settings.server_port
        remote_port = getattr(self.settings, "ssh_remote_port", 0) or local_port
        tunnel = ReverseTunnel(
            connection,
            remote_port=remote_port,
            local_port=local_port,
            remote_host=getattr(self.settings, "ssh_remote_host", ""),
        )
        tunnel.start()
        logger.info("reverse tunnel up: friends connect to %s", tunnel.address)
        return tunnel

    def _stop_tunnel(self) -> None:
        if self.tunnel is not None:
            self.tunnel.stop()
            logger.info("reverse tunnel closed")
            self.tunnel = None

    # --- server control -------------------------------------------------

    def _select_runtime(self):
        """Pick the server runtime: explicit override, manifest command, or MC."""
        if self.runtime is not None:
            return self.runtime
        if self.manifest is not None and self.manifest.is_generic:
            return ProcessRuntime(self.manifest.server_command, self.manifest.stop_command)
        return VanillaMinecraftRuntime()

    def _stop_server(self, handle) -> None:
        logger.info("stopping server…")
        try:
            self.runtime.stop(handle)
            handle.wait(timeout=self.settings.stop_timeout_seconds)
            logger.info("server stopped cleanly")
        except Exception:
            logger.warning("server did not stop cleanly within timeout")

    def _start_renewer(self) -> None:
        interval = self.settings.heartbeat_interval_seconds

        def renew() -> None:
            while not self.stop_requested.is_set():
                self.stop_requested.wait(interval)
                if self.stop_requested.is_set():
                    break
                try:
                    assert self.lease is not None
                    self.lease = self.store.renew(self.world_id, self.lease)
                    logger.debug("lease renewed until %s", self.lease.expires_at)
                except Exception as exc:
                    self._renew_error = exc
                    return

        self._renew_thread = threading.Thread(target=renew, daemon=True)
        self._renew_thread.start()

    def _stop_renewer(self) -> None:
        self.stop_requested.set()
        if self._renew_thread is not None:
            self._renew_thread.join(timeout=10)

    # --- world upload ----------------------------------------------------

    def _upload_and_release(self) -> None:
        assert self.lease is not None
        archive = self.run_dir / "upload.tar.gz"
        try:
            if self.manifest is not None:
                # Manifest-driven: sync exactly the paths the pointer file lists
                # (relative to the run dir), so any game's save layout works.
                count = create_archive(self.run_dir, self.manifest, archive)
                logger.info("manifest selected %d file(s) to upload", count)
            else:
                # Minecraft default: the level the server wrote lives in world/.
                source = self.run_dir / "world"
                if not source.is_dir():
                    source = self.run_dir
                with tarfile.open(archive, "w:gz") as tar:
                    tar.add(source, arcname=".")
            logger.info("uploading world (%d bytes)…", archive.stat().st_size)
            self.store.upload_world(self.world_id, archive)
            logger.info("world uploaded")
        finally:
            archive.unlink(missing_ok=True)

        try:
            self.store.release(self.world_id, self.lease)
            self._released = True
            logger.info("lease released — world is ready for the next host")
        except Exception as exc:
            logger.warning("could not release lease cleanly: %s", exc)

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _extract(archive: Path, dest: Path) -> None:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if member.issym() or member.islnk():
                    continue
                target = (dest / member.name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise RuntimeError(f"unsafe archive member: {member.name}")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    src = tar.extractfile(member)
                    if src is None:
                        continue
                    with src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
