"""Audit trail of credential *use* — never credential values.

When something is wrong with storage access ("who took the lease?", "which key
did this host authenticate with?"), the useful information is provenance, not
the secret. This module writes exactly that to ``data/logs/audit.log``:

    action=acquire backend=ssh world=<id> target=nomad@box key=SHA256:... outcome=ok

The line is emitted through the normal logging stack (so the redaction layer in
:mod:`launcher.secrets` still applies) and the file is created ``0600``.

The sink is configured once at process entry with :func:`configure`; calls to
:func:`record` before configuration are silently dropped, so backends and tests
do not need special handling.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_logger = logging.getLogger("nomad.audit")
_configured = False


def configure(settings) -> None:
    """Attach the audit file handler when auditing is enabled."""
    global _configured
    if _configured or not getattr(settings, "audit_log", True):
        return
    path = Path(getattr(settings, "data_dir", Path.home() / ".nomad")) / "logs" / "audit.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)
        _logger.propagate = False
        if os.name == "posix":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        _configured = True
    except OSError:
        # Auditing must never break a host session.
        _logger.disabled = True


def reset() -> None:
    """Detach handlers and allow reconfiguration (used by tests)."""
    global _configured
    for handler in list(_logger.handlers):
        _logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    _configured = False


def record(action: str, **fields) -> None:
    """Write one audit line. Values are provenance only; never pass secrets."""
    if not _configured:
        return
    pairs = " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
    _logger.info("action=%s%s", action, f" {pairs}" if pairs else "")