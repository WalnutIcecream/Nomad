from __future__ import annotations

import datetime
from pathlib import Path

from launcher.cloud import WorldStore
from launcher.config import LauncherSettings
from launcher.storage import build_store
from launcher.tests.fake_s3 import FakeS3Client


def _duration(lease) -> datetime.timedelta:
    acquired = datetime.datetime.fromisoformat(lease.acquired_at)
    expires = datetime.datetime.fromisoformat(lease.expires_at)
    return expires - acquired


def test_worldstore_honours_lease_seconds() -> None:
    store = WorldStore(FakeS3Client(), player_name="alice", lease_seconds=60)
    assert _duration(store.acquire("world-1")) == datetime.timedelta(seconds=60)


def test_renew_extends_from_now() -> None:
    """Renew pushes the expiry lease_seconds into the future from the renew time."""
    store = WorldStore(FakeS3Client(), player_name="alice", lease_seconds=120)
    lease = store.acquire("world-1")
    renewed = store.renew("world-1", lease)

    remaining = (
        datetime.datetime.fromisoformat(renewed.expires_at) - datetime.datetime.now(datetime.UTC)
    ).total_seconds()
    assert abs(remaining - 120) < 3
    assert renewed.acquired_at == lease.acquired_at


def test_build_store_passes_lease_duration(tmp_path: Path) -> None:
    """NOMAD_LEASE_DURATION_SECONDS must reach the WorldStore built for a backend."""
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path / "data"
    s.storage_backend = "server"
    s.server_endpoint_url = "https://storage.example.com"
    s.server_bucket = "nomad-worlds"
    s.server_access_key = "key"
    s.server_secret_key = "secret"
    s.lease_duration_seconds = 45

    store = build_store(s)
    assert store._store.lease_seconds == 45  # type: ignore[attr-defined]
