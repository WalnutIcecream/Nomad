"""SSH backend tests using an in-memory remote filesystem."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from launcher.cloud import CloudError, LeaseError
from launcher.storage.ssh import SshWorldStore


class FakeRemote:
    """In-memory RemoteTransport with real mkdir/cat/rm semantics."""

    def __init__(self) -> None:
        self.dirs: set[str] = set()
        self.files: dict[str, bytes] = {}

    def _parent(self, remote: str) -> str:
        return remote.rsplit("/", 1)[0] if "/" in remote else ""

    def mkdir(self, remote: str) -> bool:
        if remote in self.dirs:
            return False
        # Mark ancestor dirs as existing (like `mkdir -p parent`).
        parent = self._parent(remote)
        if parent:
            self.dirs.add(parent)
        self.dirs.add(remote)
        return True

    def exists(self, remote: str) -> bool:
        return remote in self.dirs or remote in self.files

    def read(self, remote: str) -> bytes | None:
        return self.files.get(remote)

    def write(self, remote: str, data: bytes) -> None:
        parent = self._parent(remote)
        if parent:
            self.dirs.add(parent)
        self.files[remote] = data

    def remove_tree(self, remote: str) -> None:
        self.dirs = {d for d in self.dirs if not (d == remote or d.startswith(remote + "/"))}
        self.files = {k: v for k, v in self.files.items() if not (k == remote or k.startswith(remote + "/"))}

    def push_file(self, local: Path, remote: str) -> None:
        self.write(remote, local.read_bytes())

    def pull_file(self, remote: str, local: Path) -> bool:
        if remote not in self.files:
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self.files[remote])
        return True


def _store(player: str = "alice") -> SshWorldStore:
    return SshWorldStore(FakeRemote(), base="/srv/nomad", player_name=player)


def test_acquire_and_status_round_trip() -> None:
    store = _store()
    lease = store.acquire("w1", address="203.0.113.5:25565")
    assert lease.holder == "alice"
    assert lease.etag

    status = store.status("w1")
    assert status["hosted"] is True
    assert status["holder"] == "alice"
    assert status["address"] == "203.0.113.5:25565"


def test_second_acquire_denied_on_same_remote() -> None:
    remote = FakeRemote()
    alice = SshWorldStore(remote, base="/srv/nomad", player_name="alice")
    alice.acquire("w1")

    bob = SshWorldStore(remote, base="/srv/nomad", player_name="bob")
    with pytest.raises(LeaseError):
        bob.acquire("w1")
    # Alice still owns it.
    assert alice.status("w1")["holder"] == "alice"


def test_renew_lost_lease_fails() -> None:
    remote = FakeRemote()
    alice = SshWorldStore(remote, base="/srv/nomad", player_name="alice")
    lease = alice.acquire("w1")
    alice.release("w1", lease)

    # A different host acquires; alice's stale cursor must not renew.
    bob = SshWorldStore(remote, base="/srv/nomad", player_name="bob")
    bob.acquire("w1")
    with pytest.raises(LeaseError):
        alice.renew("w1", lease)


def test_release_frees_the_world() -> None:
    remote = FakeRemote()
    alice = SshWorldStore(remote, base="/srv/nomad", player_name="alice")
    lease = alice.acquire("w1")
    alice.release("w1", lease)
    assert alice.status("w1")["hosted"] is False

    bob = SshWorldStore(remote, base="/srv/nomad", player_name="bob")
    assert bob.acquire("w1").holder == "bob"


def test_expired_lease_is_reclaimed() -> None:
    import datetime

    remote = FakeRemote()
    alice = SshWorldStore(remote, base="/srv/nomad", player_name="alice")
    alice.acquire("w1")

    # Age alice's lease so it is expired.
    key = "/srv/nomad/w1/lease/lease.json"
    data = json.loads(remote.files[key].decode("utf-8"))
    data["expires_at"] = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    ).isoformat()
    remote.files[key] = json.dumps(data).encode("utf-8")

    bob = SshWorldStore(remote, base="/srv/nomad", player_name="bob")
    assert bob.acquire("w1").holder == "bob"


def test_world_upload_download_with_integrity(tmp_path: Path) -> None:
    remote = FakeRemote()
    store = SshWorldStore(remote, base="/srv/nomad", player_name="alice")

    archive = tmp_path / "w.tar.gz"
    archive.write_bytes(b"world-bytes")
    store.upload_world("w1", archive)

    dest = tmp_path / "out.tar.gz"
    assert store.download_world("w1", dest) is True
    assert dest.read_bytes() == b"world-bytes"

    # Tampering with the stored blob must be rejected.
    remote.files["/srv/nomad/w1/world.tar.gz"] = b"tampered"
    with pytest.raises(CloudError):
        store.download_world("w1", tmp_path / "bad.tar.gz")


def test_download_missing_returns_false(tmp_path: Path) -> None:
    store = _store()
    assert store.download_world("w-missing", tmp_path / "x.tar.gz") is False


def test_build_requires_target() -> None:
    from launcher.config import LauncherSettings

    settings = LauncherSettings(_env_file=None)
    settings.storage_backend = "ssh"
    settings.ssh_target = ""
    with pytest.raises(ValueError):
        SshWorldStore.build(settings)


# --- connection multiplexing ---------------------------------------------


def test_connection_options_include_multiplexing_and_keepalive(tmp_path) -> None:
    from launcher.ssh_connection import SshConnection

    connection = SshConnection(target="me@box", control_path=tmp_path / "ctl")
    args = connection.option_args()
    assert "-T" in args
    assert "ControlMaster=auto" in args
    assert "ControlPersist=yes" in args  # session-wide, not a 5-minute timeout
    assert "ServerAliveInterval=30" in args
    assert any(tok.startswith("ControlPath=") for tok in args)
    assert connection.multiplexed is True


def test_connection_without_control_path_has_no_multiplexing() -> None:
    from launcher.ssh_connection import SshConnection

    connection = SshConnection(target="me@box", control_path=None)
    args = connection.option_args()
    assert not any("ControlMaster" in tok for tok in args)
    assert connection.multiplexed is False


def test_connection_build_disables_multiplexing_on_windows(tmp_path, monkeypatch) -> None:
    import os

    from launcher.config import LauncherSettings
    from launcher.ssh_connection import SshConnection

    monkeypatch.setattr(os, "name", "nt")
    settings = LauncherSettings(_env_file=None)
    settings.data_dir = tmp_path
    settings.ssh_target = "me@box"
    connection = SshConnection.build(settings)
    assert connection.control_path is None


def test_connection_command_puts_target_before_command() -> None:
    from launcher.ssh_connection import SshConnection

    connection = SshConnection(target="me@box")
    argv = connection.command("true")
    assert argv[0] == "ssh"
    assert argv[-2] == "me@box"
    assert argv[-1] == "true"


def test_create_and_close_master(tmp_path) -> None:
    from unittest.mock import patch

    from launcher.ssh_connection import SshConnection
    from launcher.storage.ssh import SshTransport

    class _Result:
        returncode = 0
        stdout = b""
        stderr = b""

    calls: list = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Result()

    transport = SshTransport(SshConnection(target="me@box", control_path=tmp_path / "ctl"))
    with patch("launcher.storage.ssh.subprocess.run", _fake_run):
        transport.open()  # forces the master up
        transport.close()  # tears it down

    # open() ran a trivial command; close() sent the multiplex shutdown in the
    # documented order (ssh -O exit <options> <target>).
    assert calls[0][-1] == "true"
    assert calls[-1][:3] == ["ssh", "-O", "exit"]