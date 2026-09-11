from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from launcher import ssh_identity
from launcher.config import LauncherSettings


def _settings(tmp_path: Path, **overrides) -> LauncherSettings:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def test_nomad_key_path_is_under_data_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert ssh_identity.nomad_key_path(settings) == tmp_path / "ssh" / "id_ed25519_nomad"


def test_resolve_key_prefers_explicit_setting(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ssh_key="/custom/key")
    assert ssh_identity.resolve_key(settings) == "/custom/key"


def test_resolve_key_uses_nomad_key_when_present(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    key = ssh_identity.nomad_key_path(settings)
    key.parent.mkdir(parents=True)
    key.write_text("priv")
    assert ssh_identity.resolve_key(settings) == str(key)


def test_resolve_key_falls_back_to_system(tmp_path: Path) -> None:
    assert ssh_identity.resolve_key(_settings(tmp_path)) == ""


def test_ensure_keypair_creates_files_with_owner_only_mode(tmp_path, monkeypatch) -> None:
    written: dict = {}

    def _fake_run(cmd, **kwargs):
        path = Path(cmd[-1])
        path.write_text("PRIVATE KEY")
        Path(str(path) + ".pub").write_text("ssh-ed25519 AAAAKEY nomad@test")
        written["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ssh_identity.shutil, "which", lambda name: "/usr/bin/ssh-keygen")
    monkeypatch.setattr(ssh_identity.subprocess, "run", _fake_run)

    key = ssh_identity.ensure_keypair(tmp_path / "ssh" / "id_ed25519_nomad", comment="nomad@alice")
    assert key.exists()
    assert Path(str(key) + ".pub").read_text().startswith("ssh-ed25519")
    assert "-t" in written["cmd"] and "ed25519" in written["cmd"]
    assert "-N" in written["cmd"] and "" in written["cmd"]  # no passphrase
    if os.name == "posix":
        assert (key.stat().st_mode & 0o777) == 0o600
        assert (key.parent.stat().st_mode & 0o777) == 0o700


def test_ensure_keypair_is_idempotent(tmp_path, monkeypatch) -> None:
    calls = []

    def _fake_run(cmd, **kwargs):
        # A real ssh-keygen writes the key file; the second call must see it
        # and skip generation entirely.
        path = Path(cmd[-1])
        path.write_text("PRIVATE")
        Path(str(path) + ".pub").write_text("ssh-ed25519 AAAAKEY nomad")
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ssh_identity.shutil, "which", lambda name: "/usr/bin/ssh-keygen")
    monkeypatch.setattr(ssh_identity.subprocess, "run", _fake_run)
    key = tmp_path / "ssh" / "id_ed25519_nomad"
    ssh_identity.ensure_keypair(key)
    ssh_identity.ensure_keypair(key)
    assert len(calls) == 1  # second call saw an existing key


def test_ensure_keypair_requires_ssh_keygen(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ssh_identity.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError) as exc:
        ssh_identity.ensure_keypair(tmp_path / "ssh" / "id_ed25519_nomad")
    assert "ssh-keygen" in str(exc.value)


def test_fingerprint_parses_ssh_keygen_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ssh_identity.shutil, "which", lambda name: "/usr/bin/ssh-keygen")
    monkeypatch.setattr(
        ssh_identity.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(
            returncode=0,
            stdout="256 SHA256:AbCdEf123 nomad@alice (ED25519)\n",
            stderr="",
        ),
    )
    assert ssh_identity.fingerprint(tmp_path / "k") == "SHA256:AbCdEf123"


def test_fingerprint_returns_empty_without_ssh_keygen(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ssh_identity.shutil, "which", lambda name: None)
    assert ssh_identity.fingerprint(tmp_path / "k") == ""


def test_resolve_remote_base_absolute_and_relative(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ssh_path="/srv/nomad")
    assert ssh_identity.resolve_remote_base(settings, "nomad") == "/srv/nomad"

    settings = _settings(tmp_path, ssh_path="nomad-worlds")
    assert ssh_identity.resolve_remote_base(settings, "nomad") == "/home/nomad/nomad-worlds"


def test_provision_commands_create_user_and_install_key() -> None:
    lines = ssh_identity.provision_commands("nomad", "ssh-ed25519 AAAAKEY c", "/srv/nomad")
    joined = "\n".join(lines)
    assert "useradd" in joined
    assert "/srv/nomad" in joined
    assert "authorized_keys" in joined
    assert "ssh-ed25519 AAAAKEY c" in joined
    # The user needs a real shell because the backend runs mkdir/cat remotely.
    assert "nologin" not in joined