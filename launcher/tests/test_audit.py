from __future__ import annotations

from pathlib import Path

import pytest

from launcher import audit
from launcher.config import LauncherSettings


@pytest.fixture(autouse=True)
def _reset_audit():
    audit.reset()
    yield
    audit.reset()


def _settings(tmp_path: Path, enabled: bool = True) -> LauncherSettings:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path
    s.audit_log = enabled
    return s


def test_record_writes_provenance_without_secrets(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    audit.configure(settings)
    audit.record(
        "acquire", backend="ssh", target="nomad@box", key="SHA256:abc", world="w1", outcome="ok"
    )

    log = tmp_path / "logs" / "audit.log"
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "action=acquire" in text
    assert "backend=ssh" in text
    assert "target=nomad@box" in text
    assert "key=SHA256:abc" in text
    assert "world=w1" in text


def test_record_is_noop_when_unconfigured(tmp_path: Path) -> None:
    audit.record("acquire", world="w1")  # must not raise
    assert not (tmp_path / "logs" / "audit.log").exists()


def test_audit_disabled_writes_nothing(tmp_path: Path) -> None:
    audit.configure(_settings(tmp_path, enabled=False))
    audit.record("acquire", world="w1")
    assert not (tmp_path / "logs" / "audit.log").exists()


def test_audit_file_is_owner_only(tmp_path: Path) -> None:
    import os

    if os.name != "posix":
        return
    audit.configure(_settings(tmp_path))
    audit.record("acquire", world="w1")
    log = tmp_path / "logs" / "audit.log"
    assert (log.stat().st_mode & 0o777) == 0o600


def test_ssh_store_records_acquire_and_release(tmp_path: Path) -> None:
    from launcher.storage.ssh import SshWorldStore

    class _FakeRemote:
        def __init__(self) -> None:
            self.dirs: set[str] = set()
            self.files: dict[str, bytes] = {}

        def mkdir(self, remote: str) -> bool:
            if remote in self.dirs:
                return False
            self.dirs.add(remote)
            return True

        def read(self, remote: str):
            return self.files.get(remote)

        def write(self, remote: str, data: bytes) -> None:
            self.files[remote] = data

        def remove_tree(self, remote: str) -> None:
            self.dirs = {d for d in self.dirs if not d.startswith(remote)}
            self.files = {k: v for k, v in self.files.items() if not k.startswith(remote)}

        def exists(self, remote: str) -> bool:
            return remote in self.dirs or remote in self.files

        def push_file(self, local, remote):
            pass

        def pull_file(self, remote, local):
            return False

    audit.configure(_settings(tmp_path))
    store = SshWorldStore(_FakeRemote(), base="/srv/nomad", player_name="alice")
    lease = store.acquire("w1")
    store.release("w1", lease)

    text = (tmp_path / "logs" / "audit.log").read_text(encoding="utf-8")
    assert "action=acquire" in text
    assert "action=release" in text
    assert "world=w1" in text