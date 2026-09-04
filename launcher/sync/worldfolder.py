"""Validation and merging helpers for world folders.

A *tracked folder* is a directory the player already uses for single-player.
When the launcher hosts a world whose tracked folder is set, it must combine
the cloud's latest snapshot with whatever the player has locally. The rules:

    * The cloud snapshot is the source of truth for world state (multiplayer
      sessions are authoritative), but
    * local files that are *newer* than their cloud copy are treated as the
      player's in-progress work and never overwritten.

``reconcile_sources`` copies the cloud world into ``dest`` and then copies in
the tracked folder, skipping any file whose cloud copy is not older than the
local one. ``validate_world_folder`` verifies the merged result is a bootable
Minecraft world directory before the server starts.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_MC_LEVEL_DATA = "level.dat"
_MC_LEVEL_DATA_OLD = "level.dat_old"
_MC_EXCLUDES = {
    "session.lock",
    "usercache.json",
    "usernamecache.json",
    "server.properties",
    "eula.txt",
    "banned-players.json",
    "banned-ips.json",
    "whitelist.json",
    "ops.json",
    "logs",
    "nomad.pid",
    "nomad.stop",
}


def _is_excluded(name: str) -> bool:
    """Files that must never flow into a tracked folder (server runtime
    artifacts and launcher markers are machine-local)."""
    return name in _MC_EXCLUDES


def _is_minecraft_world(dir_path: Path) -> bool:
    """A real Minecraft save must contain level.dat (or its backup)."""
    return (dir_path / _MC_LEVEL_DATA).is_file() or (
        dir_path / _MC_LEVEL_DATA_OLD
    ).is_file()


def _file_mtime(path: Path) -> float:
    """Mtime of a file, or 0 when it does not exist (treat as oldest)."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _copy_tree_merge(src: Path, dest: Path) -> None:
    """Copy every file under ``src`` into ``dest``.

    Existing destination files are only overwritten when the source file is
    strictly newer — a *newer* local file is the player's own work and wins.
    Server runtime artifacts and launcher markers are never copied.
    """
    src = Path(src)
    dest = Path(dest)
    for root, dirs, files in os.walk(src):
        # Prune excluded directories in-place so os.walk never descends.
        dirs[:] = [d for d in dirs if not _is_excluded(d)]
        rel_root = Path(root).relative_to(src)
        for name in sorted(files):
            if _is_excluded(name):
                continue
            rel = rel_root / name
            target = dest / rel
            source = Path(root) / name
            source_mtime = _file_mtime(source)
            target_mtime = _file_mtime(target)
            if target.exists() and source_mtime <= target_mtime:
                continue  # local copy is newer or identical; keep it
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            logger.debug("synced %s -> %s", rel, target)


def validate_world_folder(folder: Path) -> tuple[bool, str]:
    """Return (ok, reason) describing whether ``folder`` can be served.

    The folder must exist, contain a Minecraft world (``level.dat`` or its
    backup, directly or in a ``world`` subdirectory) and not look like a stray
    directory picked by accident.
    """
    if not folder.exists():
        return False, f"folder does not exist: {folder}"
    if not folder.is_dir():
        return False, f"not a directory: {folder}"
    if _is_minecraft_world(folder) or (folder / "world").is_dir():
        return True, ""
    return (
        False,
        "no level.dat found — pick the folder that contains your world's level.dat",
    )


def reconcile_sources(
    cloud_snapshot_dir: Path | None,
    local_src_dir: Path,
    dest_dir: Path,
) -> None:
    """Combine a cloud snapshot and a tracked local folder into ``dest_dir``.

    The cloud snapshot is copied first (it is authoritative for state), then
    the tracked folder is merged on top with the newer-file-wins rule above.
    ``cloud_snapshot_dir`` may be None when the cloud has no version yet.

    Returns nothing; raises OSError on failure. Callers should validate the
    merged result afterwards with ``validate_world_folder``.
    """
    if cloud_snapshot_dir is not None:
        if not cloud_snapshot_dir.is_dir():
            raise FileNotFoundError(
                f"cloud snapshot directory not found: {cloud_snapshot_dir}"
            )
        _copy_tree_merge(cloud_snapshot_dir, dest_dir)
    _copy_tree_merge(local_src_dir, dest_dir)


def ensure_world_level(dest_dir: Path) -> None:
    """Make ``dest_dir`` a bootable level directory.

    Vanilla's ``level-name`` is ``world`` (see ``ServerProperties``), so the
    server looks for ``<dest>/world``. After reconciling a tracked folder the
    level's own files sit directly in ``dest``; this renames them into the
    ``world`` subdirectory the server expects. A folder that already has the
    layout is left untouched.
    """
    dest_dir = Path(dest_dir)
    if (dest_dir / "world").is_dir():
        return
    if not _is_minecraft_world(dest_dir):
        return
    world_dir = dest_dir / "world"
    world_dir.mkdir(parents=True, exist_ok=True)
    for child in sorted(dest_dir.iterdir()):
        if child == world_dir:
            continue
        if _is_excluded(child.name):
            continue
        target = world_dir / child.name
        if child.is_dir():
            shutil.move(str(child), str(target))
        else:
            shutil.move(str(child), str(target))


def local_folder_modified_since(folder: Path, since: datetime | None) -> bool:
    """True when any world file under ``folder`` changed after ``since``.

    Used to decide whether a tracked folder's changes need publishing before a
    host session ends. When ``since`` is None every existing file counts as
    modified (first import).
    """
    if not folder.is_dir():
        return False
    cutoff = since.timestamp() if since is not None else None
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not _is_excluded(d)]
        for name in files:
            if _is_excluded(name):
                continue
            mtime = _file_mtime(Path(root) / name)
            if cutoff is None or mtime > cutoff:
                return True
    return False
