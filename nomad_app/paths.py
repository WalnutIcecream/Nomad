"""Path resolution for the bundled application.

When frozen by PyInstaller the layout is::

    dist/Nomad/
        Nomad.exe                  <- the hub (double-click target)
        _internal/                 <- bundled Python + assets
        postgresql/bin/...         <- embedded PostgreSQL binaries
        data/                      <- runtime data, created on first run

The same code runs from a source checkout during development, in which case
``bundle_root()`` is the repository root and ``postgresql`` may be supplied
via ``NOMAD_PG_BIN`` for local testing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """Directory holding the executables (frozen) or the repo root (dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir(rel: str) -> Path:
    """Resolve bundled read-only assets (e.g. alembic migrations)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate = Path(meipass) / rel
            if candidate.exists():
                return candidate
    return bundle_root() / rel


def default_data_dir() -> Path:
    """Portable data directory kept next to the application bundle."""
    return bundle_root() / "data"


def postgres_bin_dir() -> Path:
    """Where the embedded PostgreSQL executables live.

    Overridable with ``NOMAD_PG_BIN`` so the hub can be dev-tested against a
    downloaded copy before a full bundle is assembled.
    """
    override = os.environ.get("NOMAD_PG_BIN")
    if override:
        return Path(override)
    return bundle_root() / "postgresql" / "bin"