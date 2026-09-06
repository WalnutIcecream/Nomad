from __future__ import annotations

import json
import os
from pathlib import Path

from launcher.config import LauncherSettings, load_settings_file, save_settings_file


def test_settings_round_trip(tmp_path: Path) -> None:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path
    s.r2_account_id = "acct"
    s.r2_access_key = "key"
    s.r2_secret_key = "secret"
    s.r2_bucket = "bkt"
    s.player_name = "alice"
    s.public_address = "203.0.113.5:25565"
    save_settings_file(s)

    loaded = LauncherSettings(_env_file=None)
    loaded.data_dir = tmp_path
    load_settings_file(loaded)
    assert loaded.r2_account_id == "acct"
    assert loaded.r2_access_key == "key"
    assert loaded.r2_secret_key == "secret"
    assert loaded.r2_bucket == "bkt"
    assert loaded.player_name == "alice"
    assert loaded.public_address == "203.0.113.5:25565"


def test_env_overrides_settings_file(tmp_path: Path, monkeypatch) -> None:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path
    s.r2_account_id = "from-file"
    s.r2_bucket = "file-bucket"
    save_settings_file(s)

    # Env vars must win over the persisted file values.
    monkeypatch.setenv("NOMAD_R2_ACCOUNT_ID", "from-env")
    monkeypatch.setenv("NOMAD_R2_BUCKET", "env-bucket")
    loaded = LauncherSettings(_env_file=None)
    loaded.data_dir = tmp_path
    load_settings_file(loaded)
    assert loaded.r2_account_id == "from-env"
    assert loaded.r2_bucket == "env-bucket"


def test_bucket_env_drives_endpoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NOMAD_R2_ACCOUNT_ID", "abc")
    s = LauncherSettings(_env_file=None)
    assert s.endpoint_url == "https://abc.r2.cloudflarestorage.com"


def test_version_agnostic_defaults() -> None:
    s = LauncherSettings(_env_file=None)
    assert s.minecraft_version  # default is set; worlds can override per-world