"""Launcher configuration.

Storage and identity settings for both backends. Credential-bearing fields are
``SecretStr`` so they cannot be logged or repr'd by accident. Secrets live in
the OS keychain when one is available and otherwise in a ``0600`` settings file
inside a ``0700`` directory, whose permissions are checked (and tightened) on
load.

Precedence: OS environment variables > ``data/settings.json`` > ``.env`` >
built-in defaults.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from launcher import credentials
from launcher.credentials import SECRET_FIELDS
from launcher.secrets import register_secret, secret_value

logger = logging.getLogger(__name__)

_SETTINGS_FILE_NAME = "settings.json"


def _ensure_private_dir(directory: Path) -> None:
    """Create ``directory`` and, on POSIX, restrict it to the owner."""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(directory, 0o700)
        except OSError:
            logger.debug("could not tighten permissions on %s", directory)


def _enforce_private_file(path: Path) -> None:
    """Tighten a readable-by-others credentials file, or refuse to load it.

    The file can hold secrets in plaintext (when no keychain is available), so
    group/other access is not acceptable. If we own the file we fix it
    silently; if we cannot, loading stops with the exact command to run rather
    than proceeding insecurely.
    """
    if os.name != "posix":
        return
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if not mode & 0o077:
        return
    try:
        os.chmod(path, 0o600)
        logger.warning("tightened permissions on %s to 0600", path)
    except OSError as exc:
        raise PermissionError(
            f"{path} is readable by other users and could not be protected "
            f"({exc}). Fix it with: chmod 600 '{path}'"
        ) from exc


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
        validate_assignment=True,
    )

    # --- Cloudflare R2 ---------------------------------------------------
    # Get these from the Cloudflare dashboard (R2 -> your bucket -> Manage R2
    # API Tokens). The endpoint is derived from the account id unless set.
    r2_account_id: str = ""
    r2_access_key: SecretStr = SecretStr("")
    r2_secret_key: SecretStr = SecretStr("")
    r2_bucket: str = ""
    r2_endpoint_url: str = ""

    # --- my own server (self-hosted S3 / Garage) -------------------------
    server_endpoint_url: str = ""
    server_bucket: str = ""
    server_access_key: SecretStr = SecretStr("")
    server_secret_key: SecretStr = SecretStr("")

    # --- storage backend selection -------------------------------------
    # Which direction to use for the world lease + blob.
    #   "r2"     -> Cloudflare R2
    #   "server" -> your own S3-compatible server (Garage on a VPS/home box)
    storage_backend: str = "r2"

    # --- diagnostics -----------------------------------------------------
    # Write a non-secret audit trail of credential *use* to data/logs/audit.log.
    audit_log: bool = os.environ.get("NOMAD_AUDIT_LOG", "1") not in ("0", "false", "no")

    # --- Minecraft -------------------------------------------------------
    data_dir: Path = Field(default_factory=_default_data_dir)
    minecraft_version: str = "1.21.1"
    java_path: str = Field(default_factory=default_java_path)
    memory: str = "2G"
    eula_accepted: bool = False
    server_port: int = 25565

    # --- lease -----------------------------------------------------------
    # The lease is a JSON object in the bucket. ``lease_duration_seconds`` is
    # how long a host owns the world before needing to renew; a crashed host
    # frees the world automatically once this passes without a heartbeat.
    lease_duration_seconds: int = 300
    # How often the host re-asserts the lease while running.
    heartbeat_interval_seconds: int = 20
    # How long to wait for a graceful server stop before giving up.
    stop_timeout_seconds: int = 60

    # --- local identity --------------------------------------------------
    # A short display name identifying this machine in the lease ("the host").
    player_name: str = os.environ.get("NOMAD_PLAYER_NAME") or os.environ.get("USERNAME") or "me"
    # Address published in the lease so friends know where to connect.
    public_address: str = ""

    @property
    def endpoint_url(self) -> str:
        if self.r2_endpoint_url:
            return self.r2_endpoint_url.rstrip("/")
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    @property
    def server_endpoint(self) -> str:
        """Self-hosted endpoint, normalised with a scheme."""
        endpoint = self.server_endpoint_url.strip().rstrip("/")
        if endpoint and not endpoint.startswith(("http://", "https://")):
            endpoint = "https://" + endpoint
        return endpoint

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

    @property
    def usage_file(self) -> Path:
        """Approximate local usage counters (uploads, downloads, requests)."""
        return self.data_dir / "usage.json"

    def server_properties(self):
        from launcher.server_properties import ServerProperties

        return ServerProperties(server_port=self.server_port)


# Non-secret settings persisted to data/settings.json. Kept separate from the
# secret fields so the file never has to hold credentials when a keychain is
# available.
_FILE_FIELDS: tuple[str, ...] = (
    "storage_backend",
    "r2_account_id",
    "r2_bucket",
    "r2_endpoint_url",
    "server_endpoint_url",
    "server_bucket",
    "player_name",
    "public_address",
    "audit_log",
)


def _env(var_suffix: str) -> bool:
    return bool(os.environ.get("NOMAD_" + var_suffix.upper()))


def _read_secrets(settings: LauncherSettings, data: dict) -> None:
    """Restore secret fields from the keychain first, then the settings file."""
    use_keychain = credentials.keychain_enabled() and credentials.keychain_available()
    for field in SECRET_FIELDS:
        if _env(field):
            continue  # an explicit env var always wins
        value = data.get(field) or ""
        if not value and use_keychain:
            value = credentials.get_secret(field) or ""
        setattr(settings, field, value)


def load_settings_file(settings: LauncherSettings) -> LauncherSettings:
    """Overlay persisted data-dir settings on top of env/.env defaults.

    Precedence (highest first): explicit OS environment variables, then the
    values stored by the wizard in ``data/settings.json`` (and the keychain),
    then ``.env``, then built-in defaults. We only overwrite a field when the
    user has not set the corresponding environment variable, so a CI/token
    injected at deploy time always wins over a stale cached value.
    """
    path = settings.settings_file
    if not path.exists():
        _register_configured_secrets(settings)
        return settings
    _enforce_private_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _register_configured_secrets(settings)
        return settings

    for field in _FILE_FIELDS:
        if _env(field):
            continue
        if field in data:
            setattr(settings, field, data[field])
    _read_secrets(settings, data)
    _register_configured_secrets(settings)
    return settings


def _register_configured_secrets(settings: LauncherSettings) -> None:
    """Teach the redaction layer the exact secret values now in play."""
    for field in SECRET_FIELDS:
        register_secret(getattr(settings, field))


def _persist_secrets(settings: LauncherSettings, payload: dict) -> None:
    """Write secrets to the keychain when possible, else into the payload.

    Only if *every* keychain write succeeds do we leave the secrets out of the
    file; a partial failure falls back to the file so a credential is never
    silently dropped.
    """
    values = {field: secret_value(getattr(settings, field)) for field in SECRET_FIELDS}
    if credentials.keychain_enabled() and credentials.keychain_available():
        stored = True
        for field, value in values.items():
            if not value:
                credentials.delete_secret(field)
                continue
            if not credentials.set_secret(field, value):
                stored = False
                break
        if stored:
            for field in SECRET_FIELDS:
                payload[field] = ""
            return
        logger.warning("could not write every secret to the keychain; using the settings file")
    for field, value in values.items():
        payload[field] = value


def save_settings_file(settings: LauncherSettings) -> None:
    """Persist settings to the data dir, owner-only where POSIX permissions apply.

    Secrets go to the keychain when available and otherwise into the file, so
    the file is created ``0600`` inside a ``0700`` directory and swapped into
    place atomically. On Windows the file is protected by the ACL on the data
    directory instead.
    """
    payload: dict = {field: getattr(settings, field) for field in _FILE_FIELDS}
    _persist_secrets(settings, payload)

    path = settings.settings_file
    _ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
