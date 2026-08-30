from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from shared.protocol.models import VersionMetadata

from launcher.storage.git import GitStorage
from launcher.sync.snapshot import create_snapshot, restore_snapshot

ALICE = UUID("00000000-0000-0000-0000-00000000000a")
BOB = UUID("00000000-0000-0000-0000-00000000000b")


def _make_meta(version: int, created_by: UUID) -> VersionMetadata:
    return VersionMetadata(
        version=version,
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        created_by=created_by,
        minecraft_version="1.21.1",
        storage_key=f"v{version}",
    )


def _make_world(path: Path, content: bytes) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "level.dat").write_bytes(content)
    (path / "region").mkdir(exist_ok=True)
    (path / "region" / "r.0.0.mca").write_bytes(content * 4)


@pytest.fixture
def bare_remote(tmp_path: Path) -> Path:
    """Create a bare git repository to serve as the shared remote."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    return remote


def test_alice_to_bob_roundtrip(tmp_path: Path, bare_remote: Path) -> None:
    """The Stage 3 milestone: Alice plays, Bob sees her exact world."""
    alice_repo = tmp_path / "alice"
    bob_repo = tmp_path / "bob"

    alice_storage = GitStorage(alice_repo, remote_url=str(bare_remote))
    alice_world = tmp_path / "alice-world"
    _make_world(alice_world, b"alice-built-a-castle")

    meta = create_snapshot(alice_storage, alice_world, "1.21.1", ALICE, base_version=None)
    assert meta.version == 1
    assert alice_storage.get_latest_version().version == 1

    bob_storage = GitStorage(bob_repo, remote_url=str(bare_remote))
    bob_world = tmp_path / "bob-world"
    restore_snapshot(bob_storage, bob_world, version=1)

    assert (bob_world / "level.dat").read_bytes() == b"alice-built-a-castle"
    assert (bob_world / "region" / "r.0.0.mca").read_bytes() == b"alice-built-a-castle" * 4


def test_bob_pushes_v2_alice_receives(tmp_path: Path, bare_remote: Path) -> None:
    alice_storage = GitStorage(tmp_path / "alice", remote_url=str(bare_remote))
    bob_storage = GitStorage(tmp_path / "bob", remote_url=str(bare_remote))

    alice_world = tmp_path / "alice-world"
    _make_world(alice_world, b"v1-data")
    create_snapshot(alice_storage, alice_world, "1.21.1", ALICE, base_version=None)

    # Bob pulls v1, then plays and pushes v2.
    bob_world = tmp_path / "bob-world"
    restore_snapshot(bob_storage, bob_world, version=1)
    (bob_world / "level.dat").write_bytes(b"v2-bob-added-a-farm")
    meta = create_snapshot(bob_storage, bob_world, "1.21.1", BOB, base_version=1)
    assert meta.version == 2

    # Alice restores the new latest and sees Bob's changes.
    alice_world = tmp_path / "alice-world-restored"
    restore_snapshot(alice_storage, alice_world, version=2)
    assert (alice_world / "level.dat").read_bytes() == b"v2-bob-added-a-farm"


def test_conflict_rejected_without_overwrite(tmp_path: Path, bare_remote: Path) -> None:
    """If the cloud is newer than a host's base, the push is rejected."""
    alice_storage = GitStorage(tmp_path / "alice", remote_url=str(bare_remote))
    bob_storage = GitStorage(tmp_path / "bob", remote_url=str(bare_remote))

    alice_world = tmp_path / "alice-world"
    _make_world(alice_world, b"v1")
    create_snapshot(alice_storage, alice_world, "1.21.1", ALICE, base_version=None)

    bob_world = tmp_path / "bob-world"
    restore_snapshot(bob_storage, bob_world, version=1)
    (bob_world / "level.dat").write_bytes(b"v2")
    create_snapshot(bob_storage, bob_world, "1.21.1", BOB, base_version=1)

    # Alice still thinks the world is at v1. Her stale upload must be rejected.
    with pytest.raises(ValueError, match="version conflict"):
        create_snapshot(alice_storage, alice_world, "1.21.1", ALICE, base_version=1)

    # Cloud remains v2 (Bob's version was not overwritten).
    assert alice_storage.get_latest_version().version == 2


def test_list_versions_from_remote(tmp_path: Path, bare_remote: Path) -> None:
    alice_storage = GitStorage(tmp_path / "alice", remote_url=str(bare_remote))
    alice_world = tmp_path / "alice-world"
    _make_world(alice_world, b"v1")
    create_snapshot(alice_storage, alice_world, "1.21.1", ALICE, base_version=None)
    (alice_world / "level.dat").write_bytes(b"v2")
    create_snapshot(alice_storage, alice_world, "1.21.1", ALICE, base_version=1)

    # A fresh clone sees both versions without having pushed anything itself.
    fresh = GitStorage(tmp_path / "fresh", remote_url=str(bare_remote))
    versions = fresh.list_versions()
    assert [m.version for m in versions] == [1, 2]
    assert fresh.get_latest_version().version == 2


def test_restore_verifies_checksum(tmp_path: Path, bare_remote: Path) -> None:
    """A snapshot whose archive does not match its manifest is rejected."""
    alice_storage = GitStorage(tmp_path / "alice", remote_url=str(bare_remote))
    alice_world = tmp_path / "alice-world"
    _make_world(alice_world, b"v1")
    create_snapshot(alice_storage, alice_world, "1.21.1", ALICE, base_version=None)

    # Simulate a corrupted snapshot: commit a tampered archive under a new
    # version tag whose manifest still claims the original checksum.
    alice_storage._ensure_master()
    archive = alice_storage.repository / "world.tar.gz"
    archive.write_bytes(b"tampered-archive")
    alice_storage._git("add", "world.tar.gz")
    alice_storage._git("commit", "-m", "corrupted world version v2")
    commit = alice_storage._git("rev-parse", "HEAD")
    alice_storage._git("tag", "-f", "v2", commit)
    alice_storage._git("tag", "-f", "latest", commit)
    alice_storage._git("push", "origin", "master")
    alice_storage._git("push", "origin", "refs/tags/v2")
    alice_storage._git("push", "origin", "+refs/tags/latest")

    bob_storage = GitStorage(tmp_path / "bob", remote_url=str(bare_remote))
    with pytest.raises(Exception, match="checksum mismatch"):
        bob_storage.pull_snapshot("v2", tmp_path / "bob-world")


def test_local_storage_still_works_with_sync(tmp_path: Path) -> None:
    """The sync layer must treat local and git storage identically."""
    from launcher.storage.local import LocalStorage

    storage = LocalStorage(tmp_path / "storage")
    world = tmp_path / "world"
    _make_world(world, b"local-data")

    meta = create_snapshot(storage, world, "1.21.1", ALICE, base_version=None)
    assert meta.version == 1

    restored = tmp_path / "restored"
    restore_snapshot(storage, restored, version=1)
    assert (restored / "level.dat").read_bytes() == b"local-data"
