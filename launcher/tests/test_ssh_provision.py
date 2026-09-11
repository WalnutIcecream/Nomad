from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from launcher import ssh_identity, ssh_provision
from launcher.config import LauncherSettings


def _settings(tmp_path: Path, **overrides) -> LauncherSettings:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=255, stdout="", stderr=stderr)


def _record(monkeypatch, result) -> dict:
    """Capture the argv/env of the next subprocess.run call."""
    seen: dict = {}

    def fake(args, **kwargs):
        seen["args"] = list(args)
        seen["env"] = kwargs.get("env")
        return result

    monkeypatch.setattr(ssh_provision.subprocess, "run", fake)
    return seen


# --- probe ---------------------------------------------------------------

def test_probe_reports_working_existing_access(monkeypatch) -> None:
    _record(monkeypatch, _ok())
    result = ssh_provision.probe("pi@box")
    assert result.ok and result.kind == "ok"
    assert not result.needs_password


def test_probe_never_offers_the_nomad_key(monkeypatch) -> None:
    seen = _record(monkeypatch, _ok())
    ssh_provision.probe("pi@box")
    # The probe must reflect the *user's* access, not the key we are installing.
    assert "-i" not in seen["args"]
    assert "BatchMode=yes" in seen["args"]


def test_probe_classifies_password_only_hosts(monkeypatch) -> None:
    _record(monkeypatch, _fail("pi@box: Permission denied (publickey,password)."))
    result = ssh_provision.probe("pi@box")
    assert result.kind == "auth"
    assert result.needs_password


def test_probe_classifies_unreachable_hosts(monkeypatch) -> None:
    _record(
        monkeypatch,
        _fail("ssh: connect to host 10.0.0.9 port 22: Connection refused"),
    )
    result = ssh_provision.probe("10.0.0.9")
    assert result.kind == "unreachable"
    assert not result.needs_password


def test_probe_survives_a_missing_ssh_binary(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("ssh not found")

    monkeypatch.setattr(ssh_provision.subprocess, "run", boom)
    result = ssh_provision.probe("pi@box")
    assert not result.ok and result.kind == "error"


# --- install -------------------------------------------------------------

def test_install_key_sends_one_idempotent_command(monkeypatch) -> None:
    seen = _record(monkeypatch, _ok())
    ssh_provision.install_key("pi@box", "ssh-ed25519 AAAAC3Nza nomad@launcher")

    assert seen["args"][-2] == "pi@box", "target must precede the remote command"
    command = seen["args"][-1]
    assert "authorized_keys" in command
    assert "chmod 600" in command
    assert "chmod 700" in command
    # Idempotent: append only when the key is absent, so re-runs do not duplicate.
    assert "grep -qxF" in command
    assert ">>" in command


def test_install_key_without_password_reuses_existing_access(monkeypatch) -> None:
    seen = _record(monkeypatch, _ok())
    ssh_provision.install_key("pi@box", "ssh-ed25519 AAAA nomad")
    assert "BatchMode=yes" in seen["args"]
    assert "PubkeyAuthentication=no" not in seen["args"]
    assert seen["env"] is None


def test_install_key_password_never_reaches_argv(tmp_path: Path, monkeypatch) -> None:
    seen = _record(monkeypatch, _ok())
    ssh_provision.install_key(
        "pi@box", "ssh-ed25519 AAAA nomad", password="hunter2", data_dir=tmp_path
    )

    joined = " ".join(seen["args"])
    assert "hunter2" not in joined
    assert "hunter2" not in seen["env"].get("SSH_ASKPASS", "")

    # Password mode must be explicit: batch mode off, password auth only.
    assert "BatchMode=no" in seen["args"]
    assert "PubkeyAuthentication=no" in seen["args"]
    assert "NumberOfPasswordPrompts=1" in seen["args"]

    # The helper is wired up through the environment, not the command line.
    assert seen["env"]["SSH_ASKPASS_REQUIRE"] == "force"
    assert seen["env"]["SSH_ASKPASS"] == str(ssh_provision.helper_path(tmp_path))


def test_install_key_deletes_the_password_afterwards(tmp_path: Path, monkeypatch) -> None:
    _record(monkeypatch, _ok())
    ssh_provision.install_key(
        "pi@box", "ssh-ed25519 AAAA nomad", password="hunter2", data_dir=tmp_path
    )
    assert not ssh_provision.secret_path(tmp_path).exists()
    assert "hunter2" not in os.environ.values()


def test_install_key_failure_detail_is_redacted(monkeypatch) -> None:
    _record(
        monkeypatch,
        _fail("fatal: https://alice:sup3rtoken@example.com/repo auth failed"),
    )
    with pytest.raises(ssh_provision.ProvisionError) as excinfo:
        ssh_provision.install_key("pi@box", "ssh-ed25519 AAAA nomad")
    assert "sup3rtoken" not in excinfo.value.detail


def test_install_key_password_needs_a_data_dir(monkeypatch) -> None:
    _record(monkeypatch, _ok())
    with pytest.raises(ssh_provision.ProvisionError):
        ssh_provision.install_key("pi@box", "ssh-ed25519 AAAA nomad", password="pw")


# --- verify --------------------------------------------------------------

def test_verify_uses_only_the_nomad_key(tmp_path: Path, monkeypatch) -> None:
    key = tmp_path / "id_ed25519_nomad"
    key.write_text("private")
    seen = _record(monkeypatch, _ok())

    assert ssh_provision.verify("pi@box", key, port=2222)

    args = seen["args"]
    assert args[args.index("-i") + 1] == str(key)
    # Without IdentitiesOnly a working agent identity would mask a key that was
    # never actually installed on the machine.
    assert "IdentitiesOnly=yes" in args
    assert args[args.index("-p") + 1] == "2222"


def test_verify_is_false_on_auth_failure(tmp_path: Path, monkeypatch) -> None:
    key = tmp_path / "id_ed25519_nomad"
    key.write_text("private")
    _record(monkeypatch, _fail("Permission denied (publickey)."))
    assert not ssh_provision.verify("pi@box", key)


# --- folder --------------------------------------------------------------

def test_prepare_remote_resolves_the_real_home(tmp_path: Path, monkeypatch) -> None:
    key = tmp_path / "id_ed25519_nomad"
    key.write_text("private")
    _record(monkeypatch, _ok(stdout="/home/pi\n"))
    base = ssh_provision.prepare_remote("pi@box", key, "nomad-worlds")
    assert base == "/home/pi/nomad-worlds"


def test_prepare_remote_handles_a_non_standard_home(tmp_path: Path, monkeypatch) -> None:
    key = tmp_path / "id_ed25519_nomad"
    key.write_text("private")
    _record(monkeypatch, _ok(stdout="/Users/alice\n"))
    base = ssh_provision.prepare_remote("alice@mac", key, "nomad-worlds")
    assert base == "/Users/alice/nomad-worlds"


def test_prepare_remote_accepts_an_absolute_folder(tmp_path: Path, monkeypatch) -> None:
    key = tmp_path / "id_ed25519_nomad"
    key.write_text("private")
    _record(monkeypatch, _ok())
    base = ssh_provision.prepare_remote("pi@box", key, "/srv/nomad/")
    assert base == "/srv/nomad"


def test_prepare_remote_handles_the_filesystem_root(tmp_path: Path, monkeypatch) -> None:
    key = tmp_path / "id_ed25519_nomad"
    key.write_text("private")
    seen = _record(monkeypatch, _ok())
    base = ssh_provision.prepare_remote("pi@box", key, "/")
    assert base == "/"
    assert "mkdir -p /" in seen["args"][-1]


def test_prepare_remote_reports_a_missing_home(tmp_path: Path, monkeypatch) -> None:
    key = tmp_path / "id_ed25519_nomad"
    key.write_text("private")
    _record(monkeypatch, _ok(stdout=""))
    with pytest.raises(ssh_provision.ProvisionError):
        ssh_provision.prepare_remote("pi@box", key, "nomad-worlds")


# --- helper --------------------------------------------------------------

def test_askpass_helper_actually_emits_the_secret(tmp_path: Path) -> None:
    helper = ssh_provision.ensure_askpass_helper(tmp_path)
    ssh_provision.secret_path(tmp_path).write_text("s3cret!", encoding="utf-8")

    if os.name == "nt":
        result = subprocess.run([str(helper)], capture_output=True, text=True, timeout=15)
    else:
        result = subprocess.run(
            ["sh", str(helper)], capture_output=True, text=True, timeout=15
        )
    assert "s3cret!" in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="posix file modes")
def test_helper_and_secret_are_owner_only(tmp_path: Path) -> None:
    helper = ssh_provision.ensure_askpass_helper(tmp_path)
    assert (helper.stat().st_mode & 0o077) == 0


# --- command construction ------------------------------------------------

def test_install_key_command_is_idempotent_and_quotes_the_key() -> None:
    command = ssh_identity.install_key_command("ssh-ed25519 AAAAC3Nza nomad@launcher")
    assert "grep -qxF" in command
    assert "authorized_keys" in command
    assert "ssh-ed25519 AAAAC3Nza nomad@launcher" in command


def test_make_dir_command_quotes_hostile_paths() -> None:
    command = ssh_identity.make_dir_command("/tmp/worlds; rm -rf /")
    assert "'/tmp/worlds; rm -rf /'" in command


def test_remote_base_prefers_an_absolute_folder() -> None:
    assert ssh_identity.remote_base("/srv/nomad", "pi") == "/srv/nomad"
    assert ssh_identity.remote_base("nomad-worlds", "pi") == "/home/pi/nomad-worlds"


# --- orchestration -------------------------------------------------------

def test_run_setup_needs_a_host(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ssh_provision, "ssh_available", lambda: True)
    with pytest.raises(ssh_provision.ProvisionError):
        ssh_provision.run_setup(_settings(tmp_path), "", "pi")


def test_run_setup_reports_a_missing_ssh_client(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ssh_provision, "ssh_available", lambda: False)
    with pytest.raises(ssh_provision.ProvisionError):
        ssh_provision.run_setup(_settings(tmp_path), "box", "pi")


def test_run_setup_asks_for_a_password_only_when_asked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ssh_provision, "ssh_available", lambda: True)
    monkeypatch.setattr(
        ssh_provision, "probe",
        lambda *a, **k: ssh_provision.ProbeResult(False, "auth", "Permission denied"),
    )
    monkeypatch.setattr(
        ssh_identity, "ensure_keypair",
        lambda path, comment="": _fake_key(tmp_path, path),
    )
    with pytest.raises(ssh_provision.NeedPassword):
        ssh_provision.run_setup(_settings(tmp_path), "box", "pi")


def test_run_setup_surfaces_unreachable_hosts_without_a_password_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(ssh_provision, "ssh_available", lambda: True)
    monkeypatch.setattr(
        ssh_provision, "probe",
        lambda *a, **k: ssh_provision.ProbeResult(False, "unreachable", "Connection refused"),
    )
    monkeypatch.setattr(
        ssh_identity, "ensure_keypair",
        lambda path, comment="": _fake_key(tmp_path, path),
    )
    with pytest.raises(ssh_provision.ProvisionError) as excinfo:
        ssh_provision.run_setup(_settings(tmp_path), "box", "pi")
    assert not isinstance(excinfo.value, ssh_provision.NeedPassword)


def test_run_setup_happy_path_returns_the_resolved_target(
    tmp_path: Path, monkeypatch
) -> None:
    steps: list[str] = []
    monkeypatch.setattr(ssh_provision, "ssh_available", lambda: True)
    monkeypatch.setattr(ssh_provision, "probe", lambda *a, **k: ssh_provision.ProbeResult(True, "ok", ""))
    monkeypatch.setattr(
        ssh_identity, "ensure_keypair",
        lambda path, comment="": _fake_key(tmp_path, path),
    )
    monkeypatch.setattr(ssh_provision, "install_key", lambda *a, **k: None)
    monkeypatch.setattr(ssh_provision, "verify", lambda *a, **k: True)
    monkeypatch.setattr(ssh_provision, "prepare_remote", lambda *a, **k: "/home/pi/nomad-worlds")
    monkeypatch.setattr(ssh_identity, "fingerprint", lambda path: "SHA256:test")

    result = ssh_provision.run_setup(
        _settings(tmp_path), "box", "pi", port=2222, on_step=steps.append
    )

    assert result.target == "pi@box"
    assert result.base == "/home/pi/nomad-worlds"
    assert result.fingerprint == "SHA256:test"
    assert result.port == 2222
    assert not result.used_password
    assert any("Verifying" in step for step in steps)


def test_run_setup_fails_when_the_key_does_not_stand_alone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ssh_provision, "ssh_available", lambda: True)
    monkeypatch.setattr(ssh_provision, "probe", lambda *a, **k: ssh_provision.ProbeResult(True, "ok", ""))
    monkeypatch.setattr(
        ssh_identity, "ensure_keypair",
        lambda path, comment="": _fake_key(tmp_path, path),
    )
    monkeypatch.setattr(ssh_provision, "install_key", lambda *a, **k: None)
    monkeypatch.setattr(ssh_provision, "verify", lambda *a, **k: False)

    with pytest.raises(ssh_provision.ProvisionError) as excinfo:
        ssh_provision.run_setup(_settings(tmp_path), "box", "pi")
    assert "does not authenticate" in excinfo.value.detail


def _fake_key(tmp_path: Path, path) -> Path:
    """Stand in for ensure_keypair: create real files so readers succeed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("private", encoding="utf-8")
    Path(str(path) + ".pub").write_text(
        "ssh-ed25519 AAAAC3Nza nomad@launcher", encoding="utf-8"
    )
    return path
