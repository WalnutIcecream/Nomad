from __future__ import annotations

from pathlib import Path
from typing import Iterator, Protocol

from launcher.server_properties import ServerProperties

from launcher.minecraft.process import ProcessHandle


class MinecraftRuntime(Protocol):
    def install(self, version: str, dest: Path) -> Path: ...

    def validate(self, install_dir: Path, java_path: str) -> tuple[bool, str]: ...

    def start(
        self,
        install_dir: Path,
        world_dir: Path,
        properties: ServerProperties,
        java_path: str,
        memory: str,
    ) -> ProcessHandle: ...

    def stop(self, handle: ProcessHandle) -> None: ...

    def is_running(self, handle: ProcessHandle) -> bool: ...

    def get_logs(self, handle: ProcessHandle) -> Iterator[str]: ...

    def get_version(self, install_dir: Path) -> str: ...
