"""Server lifecycle orchestration for the bundled application.

Owns the ordering: start embedded PostgreSQL -> apply migrations -> start the
controller -> start the relay. Everything controller-side runs in-process so
the whole product really is one process from the user's point of view.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.request
from collections.abc import Callable

logger = logging.getLogger(__name__)

from nomad_app import paths
from nomad_app.embedded_pg import PostgresManager

_HEALTH_TIMEOUT_SECONDS = 60.0


class ServerManager:
    """Owns the local stack: state, subcomponents, graceful shutdown."""

    def __init__(self, on_log: Callable[[str], None] | None = None) -> None:
        self.on_log = on_log or (lambda message: logger.info(message))
        self._lock = threading.Lock()
        self._state = "idle"
        self._message = "not started"
        self._controller: object | None = None
        self._controller_thread: threading.Thread | None = None
        self._relay_thread: threading.Thread | None = None
        self._relay_server: object | None = None

        self.data_dir = paths.default_data_dir()
        self.logs_dir = self.data_dir / "logs"

        self.controller_host = os.environ.get("NOMAD_CONTROLLER_HOST", "127.0.0.1")
        self.controller_port = int(os.environ.get("NOMAD_CONTROLLER_PORT", "8000"))
        self.pg_port = int(os.environ.get("NOMAD_PG_PORT", "5433"))
        self.relay_enabled = os.environ.get("NOMAD_RELAY_ENABLED", "1") in ("1", "true", "True", "yes")
        self.relay_host = os.environ.get("NOMAD_RELAY_HOST", "0.0.0.0")
        self.relay_port = int(os.environ.get("NOMAD_RELAY_PORT", "9000"))

        self.pg = PostgresManager(paths.postgres_bin_dir(), self.data_dir, self.pg_port)
        self._compose_environment()

    def _compose_environment(self) -> None:
        """Give the controller and launcher portable defaults inside the bundle.

        ``setdefault`` everywhere: an explicit environment variable always wins,
        so power users can point a bundled build at their own cluster.
        """
        os.environ.setdefault("NOMAD_BLOB_DIR", str(self.data_dir / "blobs"))
        if paths.is_frozen():
            os.environ.setdefault("NOMAD_DATA_DIR", str(self.data_dir / "nomad"))
        if self.pg.available:
            os.environ.setdefault("NOMAD_DATABASE_URL", self.pg.database_url)

    # --- public state ---------------------------------------------------

    @property
    def database_url(self) -> str:
        return os.environ.get("NOMAD_DATABASE_URL", "")

    @property
    def controller_url(self) -> str:
        return f"http://{self.controller_host}:{self.controller_port}"

    def status(self) -> dict:
        with self._lock:
            components = {"postgres": "off", "controller": "off", "relay": "off"}
            if self._state in ("starting", "running", "stopping"):
                if self.pg.available and self.pg.initialized:
                    components["postgres"] = "on"
                if self._controller is not None:
                    components["controller"] = "on"
                if self._relay_thread is not None and self._relay_thread.is_alive():
                    components["relay"] = "on"
            return {
                "state": self._state,
                "message": self._message,
                "components": components,
            }

    # --- lifecycle ------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._state in ("starting", "running"):
                return
            self._state = "starting"
            self._message = "starting…"
        threading.Thread(target=self._bootstrap, daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            if self._state == "stopping":
                return
            was_running = self._state in ("starting", "running")
            self._state = "stopping"
            self._message = "stopping…"
        if was_running:
            self._log("stopping controller…")
            server = self._controller
            if server is not None and hasattr(server, "should_exit"):
                server.should_exit = True  # type: ignore[attr-defined]
            if self._controller_thread is not None:
                self._controller_thread.join(timeout=10)
            if self.pg.initialized:
                self._log("stopping bundled PostgreSQL…")
                try:
                    self.pg.stop()
                    self._log("PostgreSQL stopped.")
                except Exception as exc:  # pragma: no cover - defensive
                    self._log(f"error stopping PostgreSQL: {exc}")
        with self._lock:
            self._state = "stopped"
            self._message = "stopped"

    def _log(self, message: str) -> None:
        self.on_log(message)

    def _bootstrap(self) -> None:
        try:
            if not self.database_url:
                raise RuntimeError(
                    "no database available: bundled PostgreSQL missing and NOMAD_DATABASE_URL unset"
                )
            if self.pg.available:
                self._log("initialising bundled PostgreSQL (first run may take a few seconds)…")
                self.pg.initdb_if_needed()
                self.pg.start()
                self.pg.ensure_database()
                self._log(f"PostgreSQL listening on 127.0.0.1:{self.pg_port} — data in {self.pg.pgdata}")
            else:
                self._log(f"using external database: {self.database_url}")
            self._run_migrations()
            self._start_controller()
            if self.relay_enabled:
                self._start_relay()
            with self._lock:
                self._state = "running"
                self._message = "running"
            self._log("Nomad is up.")
        except Exception as exc:
            self._log(f"ERROR: {exc}")
            with self._lock:
                self._state = "error"
                self._message = str(exc)

    # --- steps ----------------------------------------------------------

    def _run_migrations(self) -> None:
        self._log("applying database migrations…")
        from alembic import command
        from alembic.config import Config

        config = Config()
        config.set_main_option(
            "script_location", str(paths.resource_dir("backend/db/migrations"))
        )
        config.set_main_option("sqlalchemy.url", self.database_url)
        command.upgrade(config, "head")
        self._log("migrations applied.")

    def _start_controller(self) -> None:
        import asyncio

        import uvicorn
        from backend.main import app

        self._log(f"starting controller on {self.controller_url}…")
        uvicorn_config = uvicorn.Config(
            app,
            host=self.controller_host,
            port=self.controller_port,
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(uvicorn_config)
        self._controller = server

        def runner() -> None:
            asyncio.run(server.serve())

        self._controller_thread = threading.Thread(target=runner, name="controller", daemon=True)
        self._controller_thread.start()
        self._wait_healthy(self.controller_url)

    def _wait_healthy(self, url: str) -> None:
        deadline = time.monotonic() + _HEALTH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if getattr(self._controller, "should_exit", False):
                raise RuntimeError("controller exited during startup")
            try:
                with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except Exception:
                pass
            time.sleep(0.3)
        raise RuntimeError(f"controller did not become ready at {url}")

    def _start_relay(self) -> None:
        import asyncio

        from relay.server import RelayServer

        self._log(f"starting relay on {self.relay_host}:{self.relay_port}…")
        server = RelayServer(self.relay_host, self.relay_port)
        self._relay_server = server

        def runner() -> None:
            asyncio.run(server.run())

        self._relay_thread = threading.Thread(target=runner, name="relay", daemon=True)
        self._relay_thread.start()
        time.sleep(0.5)
        if self._relay_thread.is_alive():
            self._log(f"relay listening on {self.relay_host}:{self.relay_port}")
        else:
            self._log(f"warning: relay is NOT listening on {self.relay_host}:{self.relay_port} (port in use?)")