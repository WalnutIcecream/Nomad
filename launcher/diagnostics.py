"""Build a redacted diagnostics bundle for support.

One paste-able blob: versions, platform, the effective (non-secret)
configuration, and the tail of the log file. Every line passes through
:func:`launcher.secrets.redact`, and secret fields are reported only as
``set`` / ``-`` so the credential never enters the bundle in the first place.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from launcher import __version__, logs
from launcher.secrets import redact, secret_value


def _set(value: object) -> str:
    return "set" if secret_value(value) else "-"


def _tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-lines:]


def collect(settings, tail_lines: int = 100) -> str:
    """Return the diagnostics bundle as redacted text."""
    parts = [
        "== Nomad diagnostics ==",
        f"version: {__version__}",
        f"python: {platform.python_version()} ({sys.executable})",
        f"os: {platform.platform()}",
        "",
        "== configuration ==",
        f"storage_backend: {settings.storage_backend}",
        f"tunnel_provider: {settings.tunnel_provider}",
        f"player_name: {settings.player_name}",
        f"public_address: {settings.public_address or '-'}",
        f"data_dir: {settings.data_dir}",
        f"r2_account_id: {settings.r2_account_id or '-'}",
        f"r2_bucket: {settings.r2_bucket or '-'}",
        f"r2_endpoint_url: {settings.r2_endpoint_url or '-'}",
        f"r2_access_key: {_set(settings.r2_access_key)}",
        f"r2_secret_key: {_set(settings.r2_secret_key)}",
        f"server_endpoint_url: {settings.server_endpoint_url or '-'}",
        f"server_bucket: {settings.server_bucket or '-'}",
        f"server_access_key: {_set(settings.server_access_key)}",
        f"server_secret_key: {_set(settings.server_secret_key)}",
        f"tunnel_playit_secret: {_set(settings.tunnel_playit_secret)}",
        "",
        f"== log tail (last {tail_lines} lines) ==",
    ]
    parts.extend(_tail(logs.log_path(settings), tail_lines) or ["(no log file yet)"])
    return redact("\n".join(parts))
