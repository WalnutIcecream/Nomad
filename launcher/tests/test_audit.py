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
        "acquire", backend="r2", target="bucket", world="w1", outcome="ok"
    )

    log = tmp_path / "logs" / "audit.log"
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "action=acquire" in text
    assert "backend=r2" in text
    assert "target=bucket" in text
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


def test_store_records_acquire_and_release(tmp_path: Path) -> None:
    from launcher.cloud import WorldStore

    from launcher.tests.fake_s3 import FakeS3Client

    audit.configure(_settings(tmp_path))
    store = WorldStore(FakeS3Client(), player_name="alice", backend="r2")
    lease = store.acquire("w1")
    store.release("w1", lease)

    text = (tmp_path / "logs" / "audit.log").read_text(encoding="utf-8")
    assert "action=acquire" in text
    assert "action=release" in text
    assert "backend=r2" in text
    assert "world=w1" in text