from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from shared.protocol.models import VersionMetadata

from launcher.storage.local import LocalStorage

CREATED_BY = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


def _make_meta(version: int) -> VersionMetadata:
    return VersionMetadata(
        version=version,
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        created_by=CREATED_BY,
        minecraft_version="1.21.1",
        storage_key=f"v{version}",
    )


def test_roundtrip_small_world(tmp_path: Path, storage: LocalStorage) -> None:
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(b"\x00\x01\x02")
    (world / "region").mkdir()
    (world / "region" / "r.0.0.mca").write_bytes(b"region-data")

    meta = storage.push_snapshot(1, world, _make_meta(1))

    restored = tmp_path / "restored"
    storage.restore_version(1, restored)

    assert (restored / "level.dat").read_bytes() == b"\x00\x01\x02"
    assert (restored / "region" / "r.0.0.mca").read_bytes() == b"region-data"
    assert meta.sha256 is not None and len(meta.sha256) == 64


def test_list_and_latest(storage: LocalStorage, tmp_path: Path) -> None:
    for version in (1, 2, 3):
        world = tmp_path / f"w{version}"
        world.mkdir()
        (world / "level.dat").write_bytes(f"v{version}".encode())
        storage.push_snapshot(version, world, _make_meta(version))

    versions = storage.list_versions()
    assert [m.version for m in versions] == [1, 2, 3]
    assert storage.get_latest_version().version == 3


def test_push_duplicate_version_rejected(storage: LocalStorage, tmp_path: Path) -> None:
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(b"data")
    storage.push_snapshot(1, world, _make_meta(1))

    with pytest.raises(FileExistsError):
        storage.push_snapshot(1, world, _make_meta(1))


def test_restore_missing_version_raises(storage: LocalStorage, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        storage.restore_version(42, tmp_path / "out")


def test_restore_detects_corruption(storage: LocalStorage, tmp_path: Path) -> None:
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(b"original")
    storage.push_snapshot(1, world, _make_meta(1))

    archive = storage._archive_path(1)
    archive.write_bytes(b"corrupted archive bytes")

    with pytest.raises(Exception):
        storage.restore_version(1, tmp_path / "out")
