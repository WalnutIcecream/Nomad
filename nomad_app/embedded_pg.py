"""Embedded PostgreSQL lifecycle for the bundled application.

A private, single-user instance is initialised on first run and kept inside
the application's data directory. It binds to ``127.0.0.1`` on a dedicated
port (default 5433) so it never collides with a developer or CI cluster on
the standard 5432.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class PostgresManager:
    """Thin wrapper around initdb / pg_ctl / pg_isready / createdb."""

    def __init__(
        self,
        bin_dir: Path,
        data_root: Path,
        port: int,
        host: str = "127.0.0.1",
    ) -> None:
        self.bin_dir = Path(bin_dir)
        self.data_root = Path(data_root)
        self.pgdata = self.data_root / "pgdata"
        self.logs_dir = self.data_root / "logs"
        self.port = port
        self.host = host
        self.user = "postgres"
        self.database = "nomad"

    @property
    def available(self) -> bool:
        return (self.bin_dir / "initdb.exe").exists()

    @property
    def initialized(self) -> bool:
        return (self.pgdata / "PG_VERSION").exists()

    @property
    def database_url(self) -> str:
        return f"postgresql+psycopg://{self.user}@{self.host}:{self.port}/{self.database}"

    def _tool(self, name: str) -> str:
        executable = f"{name}.exe" if os.name == "nt" else name
        return str(self.bin_dir / executable)

    def _run(self, name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [self._tool(name), *args]
        logger.info("$ %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout.strip():
            logger.info("%s", proc.stdout.strip())
        if proc.stderr.strip():
            logger.info("%s", proc.stderr.strip())
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"{name} failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc

    def _run_detached(self, name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run a command that spawns a long-lived child (pg_ctl start/stop).

        ``pg_ctl`` launches ``postgres.exe``; if the parent's stdout/stderr
        pipes were inherited, the child keeps them open and ``subprocess.run``
        would block forever waiting for EOF. Pipes are avoided entirely here —
        the server's own output goes to the ``-l`` logfile.
        """
        cmd = [self._tool(name), *args]
        logger.info("$ %s", " ".join(cmd))
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if check and proc.returncode not in (0, 1):
            raise RuntimeError(f"{name} failed ({proc.returncode})")
        return proc

    def initdb_if_needed(self) -> None:
        if self.initialized:
            return
        self.pgdata.mkdir(parents=True, exist_ok=True)
        self._run(
            "initdb",
            "-D",
            str(self.pgdata),
            "-U",
            self.user,
            "-A",
            "trust",
            "-E",
            "UTF8",
            "--no-locale",
        )

    def is_running(self) -> bool:
        try:
            proc = self._run(
                "pg_isready",
                "-h",
                self.host,
                "-p",
                str(self.port),
                check=False,
            )
        except FileNotFoundError:
            return False
        return "accepting connections" in proc.stdout or proc.returncode == 0

    def start(self) -> None:
        if self.is_running():
            return
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        logfile = self.logs_dir / "postgres.log"
        opts = f"-p {self.port} -h {self.host}"
        self._run_detached(
            "pg_ctl",
            "-D",
            str(self.pgdata),
            "-l",
            str(logfile),
            "-o",
            opts,
            "-w",
            "-t",
            "60",
            "start",
        )

    def ensure_database(self) -> None:
        try:
            self._run(
                "createdb",
                "-h",
                self.host,
                "-p",
                str(self.port),
                "-U",
                self.user,
                self.database,
                check=False,
            )
        except FileNotFoundError:
            # createdb not shipped with some minimal distributions; harmless.
            pass

    def stop(self) -> None:
        if not self.initialized:
            return
        try:
            self._run_detached("pg_ctl", "-D", str(self.pgdata), "-m", "fast", "stop", check=False)
        except FileNotFoundError:
            pass