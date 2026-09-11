"""Generic server runtime: launch any command as the hosted server.

Used when a ``nomad.json`` manifest declares ``server.command``. It implements
the same surface as the vanilla Minecraft runtime (``install`` / ``validate`` /
``start`` / ``stop`` / ``is_running``) so the host agent does not branch, but it
knows nothing about Minecraft: it runs the argv from the manifest with the
synced directory as the working directory.

Graceful shutdown uses ``server.stop_command`` (written to the process's stdin)
when present; otherwise the process is terminated.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Iterator

from launcher.minecraft.process import ProcessHandle

logger = logging.getLogger(__name__)


class ProcessRuntime:
    """Runs an arbitrary server command as the hosted process."""

    def __init__(self, command: list[str], stop_command: str | None = None) -> None:
        if not command:
            raise ValueError("ProcessRuntime requires a non-empty command")
        self.command = list(command)
        self.stop_command = (stop_command or "").rstrip("\n") or None

    # --- MinecraftRuntime-compatible surface ----------------------------

    def install(self, version: str, dest: Path) -> Path:
        """No install step: the command is expected to already exist."""
        return dest

    def validate(self, install_dir: Path, java_path: str) -> tuple[bool, str]:
        """Check the command's executable can be resolved."""
        program = self.command[0]
        if shutil.which(program) is None and not Path(program).exists():
            return False, f"server command not found: {program}"
        return True, ""

    def start(
        self,
        install_dir: Path,
        world_dir: Path,
        properties,
        java_path: str,
        memory: str,
    ) -> ProcessHandle:
        """Start the server with cwd = the synced directory.

        ``install_dir`` / ``properties`` / ``java_path`` / ``memory`` are
        Minecraft-specific and intentionally ignored.
        """
        world_dir.mkdir(parents=True, exist_ok=True)
        logger.info("starting server: %s (cwd=%s)", " ".join(self.command), world_dir)
        process = subprocess.Popen(
            self.command,
            cwd=world_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return ProcessHandle(process, world_dir)

    def stop(self, handle: ProcessHandle) -> None:
        if not handle.is_running():
            return
        if self.stop_command:
            handle.send_command(self.stop_command)
        else:
            handle.terminate()

    def is_running(self, handle: ProcessHandle) -> bool:
        return handle.is_running()

    def get_logs(self, handle: ProcessHandle) -> Iterator[str]:
        yield from handle.logs()

    def get_version(self, install_dir: Path) -> str:
        return "command"