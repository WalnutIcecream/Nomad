"""A rotating log file so users have something to attach to a bug report.

The launcher logs to stderr by default (gone when the window closes), so this
also writes the same records — already globally redacted by
:mod:`launcher.secrets` — to ``data/logs/nomad.log``. ``nomad diagnostics`` and
the GUI's "Copy diagnostics" read it back.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False


def log_path(settings) -> Path:
    return Path(settings.data_dir) / "logs" / "nomad.log"


def configure(settings) -> None:
    """Attach a rotating file handler to the root logger (idempotent)."""
    global _configured
    if _configured:
        return
    path = log_path(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        return  # logging must never break startup
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    _configured = True


def reset() -> None:
    """Detach the file handler (used by tests)."""
    global _configured
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, RotatingFileHandler):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    _configured = False
