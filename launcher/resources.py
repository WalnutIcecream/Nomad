"""Locate bundled resource files (icons, desktop entry) at runtime.

The same files must be found whether Nomad runs from a source checkout, a
PyInstaller one-file build, or a PyInstaller one-dir build, so every layout is
probed here and nothing else has to know about ``sys._MEIPASS``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(name: str) -> Path | None:
    """Return the first existing ``assets/<name>`` across known layouts."""
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / name)

    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir / "assets" / name)
    candidates.append(exe_dir / "_internal" / "assets" / name)

    # Source checkout: this file is launcher/resources.py, assets sit beside it.
    candidates.append(Path(__file__).resolve().parent.parent / "assets" / name)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def icon_path() -> Path | None:
    """The window/app icon (PNG), if one ships with this build."""
    return resource_path("nomad.png")
