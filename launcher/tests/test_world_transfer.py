from __future__ import annotations

from pathlib import Path

import pytest

from launcher.cloud import CloudError, WorldStore, world_hash_key, world_key
from launcher.tests.fake_s3 import FakeS3Client


def test_download_world_reports_progress(tmp_path: Path) -> None:
    store = WorldStore(FakeS3Client(), player_name="alice")
    archive = tmp_path / "w.tar.gz"
    archive.write_bytes(b"x" * 1234)
    store.upload_world("w1", archive)

    events: list[tuple[int, int | None]] = []
    dest = tmp_path / "out.tar.gz"
    assert store.download_world("w1", dest, on_progress=lambda d, t: events.append((d, t)))

    assert dest.read_bytes() == archive.read_bytes()
    assert events == [(1234, 1234)]


def test_download_world_missing_returns_false(tmp_path: Path) -> None:
    store = WorldStore(FakeS3Client(), player_name="alice")
    assert store.download_world("absent", tmp_path / "x.tar.gz") is False


def test_download_world_rejects_a_bad_hash_and_removes_the_file(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = WorldStore(client, player_name="alice")
    client.objects[world_key("w1")] = (b"world-bytes", '"e1"')
    client.objects[world_hash_key("w1")] = (b"not-the-real-hash", '"e2"')

    dest = tmp_path / "x.tar.gz"
    with pytest.raises(CloudError):
        store.download_world("w1", dest)
    assert not dest.exists()
