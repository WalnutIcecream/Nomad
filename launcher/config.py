"""Launcher configuration for the R2-backed "no backend" design.

A world lives as two objects in a Cloudflare R2 bucket: ``lease.json`` (who
holds the host lease and until when) and ``world.tar.gz`` (the single shared
version). Every setting here is a local preference; nothing requires an account
or a server you run yourself.

The R2 credentials are edited in the launcher UI and persisted to a small JSON
file in the data dir (``settings.json``) — not to ``.env``, which the bundled
app may not be able to write next to itself.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_SETTINGS_FILE_NAME = "settings.json"


def default_java_path() -> str:
    """Resolve the Java used to run the Minecraft server.

    Precedence: NOMAD_JAVA_PATH, a Java runtime bundled next to the app
    (``java\\bin\\java.exe`` beside the executable), then ``java`` on PATH.
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

    # --- R2 / object storage -------------------------------------------
    # Endpoint + credentials for the shared bucket. Get these from the
    # Cloudflare dashboard (R2 -> your bucket -> Manage R2 API Tokens).
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = ""
    # Optional custom S3 endpoint override (defaults to the standard
    # https://<account_id>.r2.cloudflarestorage.com).
    r2_endpoint_url: str = ""

    # --- Minecraft -------------------------------------------------------
    data_dir: Path = Field(default_factory=_default_data_dir)
    minecraft_version: str = "1.21.1"
    java_path: str = Field(default_factory=default_java_path)
    memory: str = "2G"
    eula_accepted: bool = False
    server_port: int = 25565

    # --- lease -----------------------------------------------------------
    # The lease is a JSON object in R2. ``lease_duration_seconds`` is how long
    # a host owns the world before needing to renew; a crashed host frees the
    # world automatically once this passes without a heartbeat.
    lease_duration_seconds: int = 300
    # How often the host re-asserts the lease while running.
    heartbeat_interval_seconds: int = 20
    # How long to wait for a graceful server stop before giving up.
    stop_timeout_seconds: int = 60

    # --- local identity --------------------------------------------------
    # A short display name identifying this machine in the lease ("the host").
    player_name: str = os.environ.get("NOMAD_PLAYER_NAME") or os.environ.get("USERNAME") or "me"
    # LAN/port-forwarded address published in the lease so friends know where
    # to connect. Empty = not published yet (discovery is a later step).
    public_address: str = ""

    @property
    def endpoint_url(self) -> str:
        if self.r2_endpoint_url:
            return self.r2_endpoint_url.rstrip("/")
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    @property
    def install_dir(self) -> Path:
        return self.data_dir / "runtime" / self.minecraft_version

    @property
    def worlds_dir(self) -> Path:
        return self.data_dir / "worlds"

    @property
    def registry_file(self) -> Path:
        """Local list of known worlds (id, name, minecraft version)."""
        return self.data_dir / "registry.json"

    @property
    def settings_file(self) -> Path:
        return self.data_dir / _SETTINGS_FILE_NAME

    def server_properties(self):
        from launcher.server_properties import ServerProperties

        return ServerProperties(server_port=self.server_port)


def load_settings_file(settings: LauncherSettings) -> LauncherSettings:
    """Overlay persisted UI settings (R2 creds, player name, address) on top of
    env/default settings. Mutates and returns the given settings object."""
    path = settings.settings_file
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        settings.r2_account_id = data.get("r2_account_id", settings.r2_account_id)
        settings.r2_access_key = data.get("r2_access_key", settings.r2_access_key)
        settings.r2_secret_key = data.get("r2_secret_key", settings.r2_secret_key)
        settings.r2_bucket = data.get("r2_bucket", settings.r2_bucket)
        settings.player_name = data.get("player_name", settings.player_name)
        settings.public_address = data.get("public_address", settings.public_address)
    return settings


def save_settings_file(settings: LauncherSettings) -> None:
    """Persist the UI-editable settings to the data dir."""
    payload = {
        "r2_account_id": settings.r2_account_id,
        "r2_access_key": settings.r2_access_key,
        "r2_secret_key": settings.r2_secret_key,
        "r2_bucket": settings.r2_bucket,
        "player_name": settings.player_name,
        "public_address": settings.public_address,
    }
    settings.settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings.settings_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
