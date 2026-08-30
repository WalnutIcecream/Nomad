"""WorldStorage backends and the settings-driven factory.

``build_storage`` picks the backend from ``LauncherSettings.storage_backend``
("local" or "git"); the sync layer never sees the concrete class, only the
``WorldStorage`` protocol, so local and git behave identically.
"""

from __future__ import annotations

from launcher.config import LauncherSettings

from launcher.storage.base import WorldStorage
from launcher.storage.git import GitStorage
from launcher.storage.local import LocalStorage


def build_storage(settings: LauncherSettings) -> WorldStorage:
    """Instantiate the configured storage backend for a launcher settings."""
    if settings.storage_backend == "git":
        return GitStorage(settings.git_repo_dir, remote_url=settings.storage_remote)
    return LocalStorage(settings.storage_dir)


__all__ = ["WorldStorage", "LocalStorage", "GitStorage", "build_storage"]