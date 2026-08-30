from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from launcher.storage.local import LocalStorage
from launcher.sync.snapshot import create_snapshot, restore_snapshot

CREATED_BY = UUID("00000000-0000-0000-0000-000000000001")


def test_create_then_restore_roundtrip(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(b"world-state-v1")
    (world / "region").mkdir()
    (world / "region" / "r.0.0.mca").write_bytes(b"chunk-data")

    meta = create_snapshot(storage, world, "1.21.1", CREATED_BY, base_version=None)
    assert meta.version == 1
    assert meta.sha256 is not None

    restored = tmp_path / "restored"
    restore_snapshot(storage, restored, version=1)
    assert (restored / "level.dat").read_bytes() == b"world-state-v1"
    assert (restored / "region" / "r.0.0.mca").read_bytes() == b"chunk-data"


def test_version_conflict_rejected(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(b"v1")
    create_snapshot(storage, world, "1.21.1", CREATED_BY, base_version=None)

    # Storage is now v1. Attempting to push with a stale base_version fails.
    with pytest.raises(ValueError, match="version conflict"):
        create_snapshot(storage, world, "1.21.1", CREATED_BY, base_version=0)


def test_incremental_versions(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(b"v1")
    first = create_snapshot(storage, world, "1.21.1", CREATED_BY, base_version=None)

    (world / "level.dat").write_bytes(b"v2")
    second = create_snapshot(
        storage, world, "1.21.1", CREATED_BY, base_version=first.version
    )

    assert first.version == 1
    assert second.version == 2
    assert storage.get_latest_version().version == 2
