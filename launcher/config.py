"""Launcher configuration.

Storage and identity settings for every backend. Credential-bearing fields are
``SecretStr`` so they cannot be logged or repr'd by accident; the persisted
``settings.json`` is written ``0600`` in a ``0700`` directory and its permissions
are checked (and tightened) on load.

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

from launcher.secrets import register_secret, register_url_secrets

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

    The file holds secrets in plaintext, so group/other access is not
    acceptable. If we own the file we fix it silently; if we cannot, loading
    stops with the exact command to run rather than proceeding insecurely.
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

    # --- R2 / object storage -------------------------------------------
    # Endpoint + credentials for the shared bucket. Get these from the
    # Cloudflare dashboard (R2 -> your bucket -> Manage R2 API Tokens).
    r2_account_id: str = ""
    r2_access_key: SecretStr = SecretStr("")
    r2_secret_key: SecretStr = SecretStr("")
    r2_bucket: str = ""
    # Optional custom S3 endpoint override (defaults to the standard
    # https://<account_id>.r2.cloudflarestorage.com).
    r2_endpoint_url: str = ""

    # --- storage backend selection -------------------------------------
    # Which direction to use for the world lease + blob.
    #   "r2"   -> Cloudflare R2 (or any S3 endpoint)
    #   "git"  -> a git repo (free, unlimited storage, 100 MB per-file cap)
    #   "vps"  -> your own S3/MinIO server
    #   "ssh"  -> a home/bare-metal box you own, over ssh (no server software)
    storage_backend: str = "r2"

    # --- git backend ---------------------------------------------------
    git_repo_dir: Path = Path(os.environ.get("NOMAD_GIT_REPO", "~/.nomad/repo")).expanduser()
    git_remote_url: str = os.environ.get("NOMAD_GIT_REMOTE", "")

    # --- VPS backend ---------------------------------------------------
    # The VPS path reuses the R2 code pointed at your own S3-compatible
    # endpoint (MinIO/Garage). Set endpoint url + bucket; access/secret follow
    # the same NOMAD_R2_* env vars or data/settings.json.
    vps_endpoint_url: str = os.environ.get("NOMAD_VPS_ENDPOINT", "")
    vps_bucket: str = os.environ.get("NOMAD_VPS_BUCKET", "")

    # --- SSH backend ---------------------------------------------------
    # A home/bare-metal box used as storage. The world lives under
    # <ssh_path>/<world-id>/ on the remote; sshd is the only requirement.
    ssh_target: str = os.environ.get("NOMAD_SSH_TARGET", "")
    ssh_path: str = os.environ.get("NOMAD_SSH_PATH", "nomad-worlds")
    ssh_key: str = os.environ.get("NOMAD_SSH_KEY", "")
    ssh_port: int = int(os.environ.get("NOMAD_SSH_PORT", "0") or "0")
    # Optional reverse tunnel: publish the local game port through the ssh box
    # so a host behind NAT needs no router configuration.
    ssh_reverse_tunnel: bool = os.environ.get("NOMAD_SSH_REVERSE_TUNNEL", "") in ("1", "true", "yes")
    ssh_remote_port: int = int(os.environ.get("NOMAD_SSH_REMOTE_PORT", "0") or "0")
    # Address published in the lease when tunnelling (default: the ssh host).
    ssh_remote_host: str = os.environ.get("NOMAD_SSH_REMOTE_HOST", "")

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

    @property
    def usage_file(self) -> Path:
        """Approximate local R2 usage counters (CumulativeBytesSent etc.)."""
        return self.data_dir / "usage.json"

    def server_properties(self):
        from launcher.server_properties import ServerProperties

        return ServerProperties(server_port=self.server_port)


def load_settings_file(settings: LauncherSettings) -> LauncherSettings:
    """Overlay persisted data-dir settings on top of env/.env defaults.

    Precedence (highest first): explicit OS environment variables, then the
    values stored by the GUI in ``data/settings.json``, then ``.env``, then
    built-in defaults. We only overwrite a field from ``settings.json`` when
    the user has not set the corresponding environment variable, so a CI/token
    injected at deploy time always wins over a stale cached value.
    """
    path = settings.settings_file
    if not path.exists():
        return settings
    _enforce_private_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return settings

    def _env(varname: str) -> bool:
        return bool(os.environ.get(varname))

    if not _env("NOMAD_R2_ACCOUNT_ID"):
        settings.r2_account_id = data.get("r2_account_id", settings.r2_account_id)
    if not _env("NOMAD_R2_ACCESS_KEY"):
        settings.r2_access_key = data.get("r2_access_key", settings.r2_access_key)
    if not _env("NOMAD_R2_SECRET_KEY"):
        settings.r2_secret_key = data.get("r2_secret_key", settings.r2_secret_key)
    if not _env("NOMAD_R2_BUCKET"):
        settings.r2_bucket = data.get("r2_bucket", settings.r2_bucket)
    if not _env("NOMAD_PLAYER_NAME"):
        settings.player_name = data.get("player_name", settings.player_name)
    if not _env("NOMAD_PUBLIC_ADDRESS"):
        settings.public_address = data.get("public_address", settings.public_address)
    if not _env("NOMAD_STORAGE_BACKEND"):
        settings.storage_backend = data.get("storage_backend", settings.storage_backend)
    if not _env("NOMAD_VPS_ENDPOINT"):
        settings.vps_endpoint_url = data.get("vps_endpoint_url", settings.vps_endpoint_url)
    if not _env("NOMAD_VPS_BUCKET"):
        settings.vps_bucket = data.get("vps_bucket", settings.vps_bucket)
    if not _env("NOMAD_GIT_REPO"):
        settings.git_repo_dir = Path(data.get("git_repo_dir", str(settings.git_repo_dir)))
    if not _env("NOMAD_GIT_REMOTE"):
        settings.git_remote_url = data.get("git_remote_url", settings.git_remote_url)
    if not _env("NOMAD_SSH_TARGET"):
        settings.ssh_target = data.get("ssh_target", settings.ssh_target)
    if not _env("NOMAD_SSH_PATH"):
        settings.ssh_path = data.get("ssh_path", settings.ssh_path)
    if not _env("NOMAD_SSH_KEY"):
        settings.ssh_key = data.get("ssh_key", settings.ssh_key)
    if not _env("NOMAD_SSH_PORT"):
        settings.ssh_port = int(data.get("ssh_port", settings.ssh_port) or 0)
    if not _env("NOMAD_SSH_REVERSE_TUNNEL"):
        settings.ssh_reverse_tunnel = bool(
            data.get("ssh_reverse_tunnel", settings.ssh_reverse_tunnel)
        )
    if not _env("NOMAD_SSH_REMOTE_PORT"):
        settings.ssh_remote_port = int(data.get("ssh_remote_port", settings.ssh_remote_port) or 0)
    if not _env("NOMAD_SSH_REMOTE_HOST"):
        settings.ssh_remote_host = data.get("ssh_remote_host", settings.ssh_remote_host)
    if not _env("NOMAD_AUDIT_LOG"):
        settings.audit_log = bool(data.get("audit_log", settings.audit_log))
    _register_configured_secrets(settings)
    return settings


def _register_configured_secrets(settings: LauncherSettings) -> None:
    """Teach the redaction layer the exact secret values now in play."""
    register_secret(settings.r2_access_key)
    register_secret(settings.r2_secret_key)
    register_url_secrets(settings.git_remote_url)


def save_settings_file(settings: LauncherSettings) -> None:
    """Persist settings to the data dir, owner-only where POSIX permissions apply.

    The file holds credentials in plaintext, so it is created ``0600`` inside a
    ``0700`` directory and swapped into place atomically. On Windows the file is
    protected by the ACL on the data directory instead.
    """
    from launcher.secrets import secret_value

    payload = {
        "r2_account_id": settings.r2_account_id,
        "r2_access_key": secret_value(settings.r2_access_key),
        "r2_secret_key": secret_value(settings.r2_secret_key),
        "r2_bucket": settings.r2_bucket,
        "player_name": settings.player_name,
        "public_address": settings.public_address,
        "storage_backend": settings.storage_backend,
        "vps_endpoint_url": settings.vps_endpoint_url,
        "vps_bucket": settings.vps_bucket,
        "git_repo_dir": str(settings.git_repo_dir),
        "git_remote_url": settings.git_remote_url,
        "ssh_target": settings.ssh_target,
        "ssh_path": settings.ssh_path,
        "ssh_key": settings.ssh_key,
        "ssh_port": settings.ssh_port,
        "ssh_reverse_tunnel": settings.ssh_reverse_tunnel,
        "ssh_remote_port": settings.ssh_remote_port,
        "ssh_remote_host": settings.ssh_remote_host,
        "audit_log": settings.audit_log,
    }
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
