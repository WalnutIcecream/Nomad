from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from threading import Lock, Thread
from typing import Iterator

logger = logging.getLogger(__name__)


class ProcessHandle:
    """A thin, restart-friendly wrapper around a long-lived Minecraft process.

    The server process holds an open stdin pipe so callers can send the
    vanilla ``stop`` command for a graceful, world-flushing shutdown.
    """

    def __init__(self, process: subprocess.Popen[bytes], world_dir: Path) -> None:
        self._process = process
        self.world_dir = world_dir
        self._log_lines: list[str] = []
        self._lock = Lock()
        self._reader_thread: Thread | None = None
        if process.stdout is not None:
            self._reader_thread = Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()

    def _read_output(self) -> None:
        assert self._process.stdout is not None
        for raw_line in self._process.stdout:
            text = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            with self._lock:
                self._log_lines.append(text)

    def send_command(self, command: str) -> None:
        if self._process.stdin is not None:
            self._process.stdin.write((command + "\n").encode("utf-8"))
            self._process.stdin.flush()

    def is_running(self) -> bool:
        return self._process.poll() is None

    def terminate(self) -> None:
        """Force-stop the process (used when no graceful stop command exists)."""
        if self._process.poll() is None:
            self._process.terminate()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def logs(self, limit: int = 50) -> list[str]:
        with self._lock:
            return list(self._log_lines[-limit:])

    def tail_logs(self) -> Iterator[str]:
        """Yield all captured log lines, then continue until process exit."""
        position = 0
        while True:
            with self._lock:
                new_lines = self._log_lines[position:]
                position = len(self._log_lines)
            for line in new_lines:
                yield line
            if not self.is_running() and position >= len(self._log_lines):
                break
            time.sleep(0.1)
