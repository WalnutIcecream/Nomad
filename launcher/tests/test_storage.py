from __future__ import annotations

import json
from pathlib import Path

import pytest

from launcher.cloud import LeaseError, WorldStore
from launcher.config import LauncherSettings, load_settings_file, save_settings_file
from launcher.storage import build_store
from launcher.storage.r2 import R2WorldStore
from launcher.storage.server import ServerWorldStore
from launcher.tests.fake_s3 import FakeS3Client


def _r2_settings(tmp_path: Path) -> LauncherSettings:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path / "data"
    s.player_name = "alice"
    s.storage_backend = "r2"
    s.r2_account_id = "acct"
    s.r2_access_key = "key"
    s.r2_secret_key = "secret"
    s.r2_bucket = "bucket"
    return s


def _server_settings(tmp_path: Path) -> LauncherSettings:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path / "data"
    s.player_name = "alice"
    s.storage_backend = "server"
    s.server_endpoint_url = "https://storage.example.com"
    s.server_bucket = "nomad-worlds"
    s.server_access_key = "key"
    s.server_secret_key = "secret"
    return s


def test_build_store_dispatch() -> None:
    s = LauncherSettings(_env_file=None)
    s.storage_backend = "r2"
    s.r2_account_id = "acct"
    s.r2_access_key = "k"
    s.r2_secret_key = "s"
    s.r2_bucket = "b"
    store = build_store(s)
    assert isinstance(store, R2WorldStore)
    assert store.name == "r2"


def test_build_store_server_dispatch(tmp_path: Path) -> None:
    store = build_store(_server_settings(tmp_path))
    assert isinstance(store, ServerWorldStore)
    assert store.name == "server"


def test_build_store_rejects_obsolete_backends() -> None:
    for obsolete in ("git", "vps", "ssh"):
        s = LauncherSettings(_env_file=None)
        s.storage_backend = obsolete
        with pytest.raises(ValueError, match="unknown storage backend"):
            build_store(s)


def test_server_build_requires_every_field(tmp_path: Path) -> None:
    s = _server_settings(tmp_path)
    s.server_secret_key = ""
    with pytest.raises(ValueError, match="NOMAD_SERVER_SECRET_KEY"):
        build_store(s)


def test_server_endpoint_gets_a_scheme(tmp_path: Path) -> None:
    s = _server_settings(tmp_path)
    s.server_endpoint_url = "storage.example.com"
    assert s.server_endpoint == "https://storage.example.com"


def test_server_acquire_release_round_trip() -> None:
    store = ServerWorldStore(WorldStore(FakeS3Client(), player_name="alice"))
    lease = store.acquire("world-1", address="1.2.3.4:25565")
    assert lease.status == "active"
    assert store.status("world-1")["hosted"] is True

    store.release("world-1", lease)
    assert store.status("world-1")["hosted"] is False


def test_server_second_acquire_denied() -> None:
    client = FakeS3Client()
    ServerWorldStore(WorldStore(client, player_name="alice")).acquire("world-1")
    with pytest.raises(LeaseError):
        ServerWorldStore(WorldStore(client, player_name="bob")).acquire("world-1")


def test_server_world_upload_download(tmp_path: Path) -> None:
    store = ServerWorldStore(WorldStore(FakeS3Client(), player_name="alice"))
    archive = tmp_path / "w.tar.gz"
    archive.write_bytes(b"minecraft-world-data")
    store.upload_world("world-1", archive)

    dest = tmp_path / "out.tar.gz"
    assert store.download_world("world-1", dest) is True
    assert dest.read_bytes() == b"minecraft-world-data"


def test_settings_persist_backend(tmp_path: Path) -> None:
    s = _server_settings(tmp_path)
    save_settings_file(s)
    stored = json.loads((tmp_path / "data" / "settings.json").read_text(encoding="utf-8"))
    assert stored["storage_backend"] == "server"
    assert stored["server_endpoint_url"] == "https://storage.example.com"


def test_settings_round_trip_backend(tmp_path: Path) -> None:
    s = _server_settings(tmp_path)
    save_settings_file(s)
    loaded = LauncherSettings(_env_file=None)
    loaded.data_dir = tmp_path / "data"
    load_settings_file(loaded)
    assert loaded.storage_backend == "server"
    assert loaded.server_bucket == "nomad-worlds"
