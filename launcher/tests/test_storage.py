from __future__ import annotations

import json
from pathlib import Path

import pytest

from launcher.cloud import LeaseError, Lease
from launcher.config import LauncherSettings, save_settings_file
from launcher.storage import build_store
from launcher.storage.git import GitWorldStore


def _settings(tmp_path: Path) -> LauncherSettings:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path / "data"
    s.git_repo_dir = tmp_path / "repo"
    s.git_remote_url = ""
    s.player_name = "alice"
    s.storage_backend = "git"
    return s


def test_build_store_dispatch() -> None:
    s = LauncherSettings(_env_file=None)
    s.storage_backend = "git"
    s.git_repo_dir = Path(".") / "x"
    store = build_store(s)
    assert isinstance(store, GitWorldStore)
    assert store.name == "git"


def test_git_acquire_release_round_trip(tmp_path: Path) -> None:
    store = GitWorldStore(tmp_path / "repo", name="alice")
    lease = store.acquire("world-1", address="1.2.3.4:25565")
    assert lease.status == "active"
    assert store.status("world-1")["hosted"] is True

    store.release("world-1", lease)
    assert store.status("world-1")["hosted"] is False


def test_git_second_acquire_denied(tmp_path: Path) -> None:
    store = GitWorldStore(tmp_path / "repo", name="alice")
    store.acquire("world-1")
    second = GitWorldStore(tmp_path / "repo", name="bob")
    with pytest.raises(LeaseError):
        second.acquire("world-1")


def test_git_world_upload_download(tmp_path: Path) -> None:
    store = GitWorldStore(tmp_path / "repo", name="alice")
    archive = tmp_path / "w.tar.gz"
    archive.write_bytes(b"minecraft-world-data")
    store.upload_world("world-1", archive)

    dest = tmp_path / "out.tar.gz"
    assert store.download_world("world-1", dest) is True
    assert dest.read_bytes() == b"minecraft-world-data"


def test_settings_persist_backend(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    save_settings_file(s)
    stored = json.loads((tmp_path / "data" / "settings.json").read_text(encoding="utf-8"))
    assert stored["storage_backend"] == "git"


def test_settings_round_trip_backend(tmp_path: Path) -> None:
    from launcher.config import load_settings_file

    s = _settings(tmp_path)
    save_settings_file(s)
    loaded = LauncherSettings(_env_file=None)
    loaded.data_dir = tmp_path / "data"
    load_settings_file(loaded)
    assert loaded.storage_backend == "git"
    assert loaded.git_repo_dir == tmp_path / "repo"