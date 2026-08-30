from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_java_path() -> str:
    """Resolve the Java used to run the Minecraft server.

    Precedence: NOMAD_JAVA_PATH, a Java runtime bundled next to the app
    (``java\\bin\\java.exe`` beside the executable — the shipped bundle
    carries its own JRE so nothing needs installing), then ``java`` on PATH.
    """
    override = os.environ.get("NOMAD_JAVA_PATH")
    if override:
        return override
    exe_dir = Path(sys.executable).resolve().parent
    for candidate in (exe_dir / "java" / "bin" / "java.exe", exe_dir / "java" / "bin" / "java"):
        if candidate.exists():
            return str(candidate)
    return "java"


def _default_data_dir() -> Path:
    """Platform-standard user data directory:
    %APPDATA%\\nomad on Windows, XDG_DATA_HOME on Linux/macOS."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "nomad"
        return Path.home() / "AppData" / "Roaming" / "nomad"
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "nomad"
    return Path.home() / ".local" / "share" / "nomad"


class LauncherSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOMAD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default_factory=_default_data_dir)
    minecraft_version: str = "1.21.1"
    java_path: str = Field(default_factory=default_java_path)
    memory: str = "2G"
    eula_accepted: bool = False
    heartbeat_interval_seconds: int = 20
    lease_duration_seconds: int = 300
    stop_timeout_seconds: int = 60
    storage_backend: str = "local"
    storage_repo: Path | None = None
    storage_remote: str | None = None
    controller_url: str = "http://localhost:8000"
    controller_token: str = ""
    server_port: int = 25565
    relay_enabled: bool = False
    relay_host: str = "localhost"
    relay_port: int = 9000

    @property
    def install_dir(self) -> Path:
        return self.data_dir / "runtime" / self.minecraft_version

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "storage"

    @property
    def git_repo_dir(self) -> Path:
        return self.storage_repo or (self.data_dir / "repo")

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state" / "launcher.json"

    @property
    def worlds_dir(self) -> Path:
        return self.data_dir / "worlds"

    def server_properties(self):
        from shared.protocol.models import ServerProperties

        return ServerProperties(server_port=self.server_port)
