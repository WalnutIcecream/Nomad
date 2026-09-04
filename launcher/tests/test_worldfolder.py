from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from launcher.sync.worldfolder import (
    ensure_world_level,
    local_folder_modified_since,
    reconcile_sources,
    validate_world_folder,
)

from . import make_cloud_world, make_world_folder


def _age(path: Path, seconds: float) -> None:
    """Pin a file's mtime to ``seconds`` ago (for deterministic merge tests)."""
    now = time.time()
    os.utime(path, (now - seconds, now - seconds))


def test_validate_world_folder_accepts_save_layout(tmp_path: Path) -> None:
    folder = make_world_folder(tmp_path, "Survival")
    ok, reason = validate_world_folder(folder)
    assert ok, reason
    assert reason == ""


def test_validate_world_folder_accepts_world_subdir_layout(tmp_path: Path) -> None:
    root = make_cloud_world(tmp_path)
    ok, reason = validate_world_folder(root)
    assert ok, reason


def test_validate_world_folder_rejects_missing(tmp_path: Path) -> None:
    ok, reason = validate_world_folder(tmp_path / "nope")
    assert not ok
    assert "does not exist" in reason


def test_validate_world_folder_rejects_empty_dir(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    ok, reason = validate_world_folder(empty)
    assert not ok
    assert "level.dat" in reason


def test_validate_world_folder_rejects_file(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")
    ok, reason = validate_world_folder(file_path)
    assert not ok
    assert "not a directory" in reason


def test_reconcile_merges_cloud_then_newer_local(tmp_path: Path) -> None:
    cloud = make_world_folder(tmp_path / "cloud-src")
    tracked = make_world_folder(tmp_path / "local-src")
    dest = tmp_path / "dest"

    # Cloud snapshot content is authoritative.
    cloud_level = cloud / "level.dat"
    cloud_level.write_bytes(b"cloud-new")
    # Local has a NEWER region file than the cloud's stale copy.
    cloud_region = cloud / "region" / "r.0.0.mca"
    local_region = tracked / "region" / "r.0.0.mca"
    local_region.write_bytes(b"local-newer")
    # Pin mtimes: cloud level.dat is newest, local region is newest region,
    # everything else older.
    _age(cloud_level, 0)
    _age(local_region, 0)
    _age(cloud_region, 200)
    _age(tracked / "level.dat", 200)

    reconcile_sources(cloud, tracked, dest)

    assert (dest / "level.dat").read_bytes() == b"cloud-new"
    # The newer local region file wins over the cloud's stale copy.
    assert (dest / "region" / "r.0.0.mca").read_bytes() == b"local-newer"


def test_reconcile_skips_newer_local_files(tmp_path: Path) -> None:
    cloud = make_world_folder(tmp_path / "cloud-src")
    tracked = make_world_folder(tmp_path / "local-src")
    dest = tmp_path / "dest"

    # Cloud and local both have level.dat; cloud is older -> local wins.
    cloud_level = cloud / "level.dat"
    cloud_level.write_bytes(b"old")
    local_level = tracked / "level.dat"
    local_level.write_bytes(b"new-local")
    _age(cloud_level, 100)  # cloud copy is 100s older
    _age(local_level, 0)

    reconcile_sources(cloud, tracked, dest)

    assert (dest / "level.dat").read_bytes() == b"new-local"


def test_reconcile_does_not_export_runtime_files(tmp_path: Path) -> None:
    tracked = make_world_folder(tmp_path / "local-src")
    (tracked / "server.properties").write_text("motd=hi\n")
    (tracked / "eula.txt").write_text("eula=true\n")
    (tracked / "usercache.json").write_text("{}")
    (tracked / "session.lock").write_bytes(b"lock")

    dest = tmp_path / "dest"
    reconcile_sources(None, tracked, dest)

    assert (dest / "level.dat").is_file()
    for artifact in ("server.properties", "eula.txt", "usercache.json", "session.lock"):
        assert not (dest / artifact).exists(), artifact


def test_ensure_world_level_rewraps_flat_save(tmp_path: Path) -> None:
    folder = make_world_folder(tmp_path, "flat")
    ensure_world_level(folder)

    # The level files moved into world/.
    assert (folder / "world" / "level.dat").is_file()
    assert (folder / "world" / "region" / "r.0.0.mca").is_file()
    # The launcher runtime artifacts stay at the top level.
    assert (folder / "server.properties").is_file() is False


def test_ensure_world_level_leaves_world_layout(tmp_path: Path) -> None:
    root = make_cloud_world(tmp_path)
    ensure_world_level(root)
    assert (root / "world" / "level.dat").is_file()
    # No double nesting.
    assert not (root / "world" / "world").exists()


def test_local_folder_modified_since_detects_changes(tmp_path: Path) -> None:
    folder = make_world_folder(tmp_path, "world")
    # Everything is old (100s ago), so nothing is newer than the cutoff.
    for path in folder.rglob("*"):
        if path.is_file():
            _age(path, 100)

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert not local_folder_modified_since(folder, since=cutoff)

    # Touch a world file after the cutoff.
    region = folder / "region" / "r.0.0.mca"
    now = datetime.now(timezone.utc)
    os.utime(region, (now.timestamp(), now.timestamp()))
    assert local_folder_modified_since(folder, since=cutoff)


def test_local_folder_modified_since_none_means_modified(tmp_path: Path) -> None:
    folder = make_world_folder(tmp_path, "world")
    assert local_folder_modified_since(folder, since=None)
