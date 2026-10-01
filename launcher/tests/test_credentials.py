"""Keychain credential storage: used when available, file fallback otherwise."""

from __future__ import annotations

import json
from pathlib import Path

from launcher import credentials
from launcher.config import LauncherSettings, load_settings_file, save_settings_file
from launcher.secrets import secret_value


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str):
        return self.store.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.store[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        self.store.pop((service, name), None)


def _use_fake_keychain(monkeypatch, fake: _FakeKeyring) -> None:
    monkeypatch.setattr(credentials, "keychain_enabled", lambda: True)
    monkeypatch.setattr(credentials, "keychain_available", lambda: True)
    monkeypatch.setattr(credentials, "set_secret", lambda name, value: _fake_set(fake, name, value))
    monkeypatch.setattr(credentials, "get_secret", lambda name: fake.get_password("nomad", name))
    monkeypatch.setattr(credentials, "delete_secret", lambda name: fake.delete_password("nomad", name))


def _fake_set(fake: _FakeKeyring, name: str, value: str) -> bool:
    fake.set_password("nomad", name, value)
    return True


def test_secrets_go_to_keychain_not_the_file(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeKeyring()
    _use_fake_keychain(monkeypatch, fake)

    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path
    s.r2_access_key = "AKIA-EXAMPLE"
    s.r2_secret_key = "super-secret"
    save_settings_file(s)

    stored = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert stored["r2_access_key"] == ""
    assert stored["r2_secret_key"] == ""
    assert fake.store[("nomad", "r2_secret_key")] == "super-secret"

    loaded = LauncherSettings(_env_file=None)
    loaded.data_dir = tmp_path
    load_settings_file(loaded)
    assert secret_value(loaded.r2_access_key) == "AKIA-EXAMPLE"
    assert secret_value(loaded.r2_secret_key) == "super-secret"


def test_falls_back_to_file_when_keychain_write_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(credentials, "keychain_enabled", lambda: True)
    monkeypatch.setattr(credentials, "keychain_available", lambda: True)
    monkeypatch.setattr(credentials, "set_secret", lambda name, value: False)

    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path
    s.r2_secret_key = "must-not-be-lost"
    save_settings_file(s)

    stored = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert stored["r2_secret_key"] == "must-not-be-lost"

    loaded = LauncherSettings(_env_file=None)
    loaded.data_dir = tmp_path
    load_settings_file(loaded)
    assert secret_value(loaded.r2_secret_key) == "must-not-be-lost"


def test_keychain_can_be_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("NOMAD_KEYCHAIN", "0")
    assert credentials.keychain_enabled() is False
    monkeypatch.setenv("NOMAD_KEYCHAIN", "1")
    assert credentials.keychain_enabled() is True
