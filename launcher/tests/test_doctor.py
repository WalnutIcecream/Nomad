from __future__ import annotations

from pathlib import Path

from launcher import doctor, storage
from launcher.cloud import ConnectionResult
from launcher.config import LauncherSettings


def _settings(tmp_path: Path) -> LauncherSettings:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path / "data"
    s.public_address = "203.0.113.5:25565"
    return s


def test_check_data_dir_ok(tmp_path: Path) -> None:
    assert doctor.check_data_dir(_settings(tmp_path)).ok


def test_check_data_dir_flags_unwritable(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    s = _settings(tmp_path)
    s.data_dir = blocker  # a file where a directory is expected
    check = doctor.check_data_dir(s)
    assert not check.ok
    assert check.fix


def test_check_disk_space_ok(tmp_path: Path) -> None:
    assert doctor.check_disk_space(_settings(tmp_path)).ok


def test_check_java_missing_binary(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    s.java_path = "/definitely/not/java"
    check = doctor.check_java(s)
    assert not check.ok
    assert "not found" in check.detail


def test_preflight_passes_on_a_good_machine(tmp_path: Path) -> None:
    failures = [c for c in doctor.preflight(_settings(tmp_path)) if not c.ok]
    assert failures == []


def test_preflight_flags_unwritable_data_dir(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    s = _settings(tmp_path)
    s.data_dir = blocker
    failures = [c for c in doctor.preflight(s) if not c.ok]
    assert any(c.name == "data directory" for c in failures)


def test_check_storage_reports_connection_failure(monkeypatch, tmp_path: Path) -> None:
    class _FakeStore:
        name = "fake"

        def test_connection(self) -> ConnectionResult:
            return ConnectionResult(False, "Nomad couldn't access this bucket.")

    monkeypatch.setattr(storage, "build_store", lambda *a, **k: _FakeStore())
    check = doctor.check_storage(_settings(tmp_path))
    assert not check.ok
    assert check.fix


def test_check_storage_reports_success(monkeypatch, tmp_path: Path) -> None:
    class _FakeStore:
        name = "fake"

        def test_connection(self) -> ConnectionResult:
            return ConnectionResult(True, "Connected.")

    monkeypatch.setattr(storage, "build_store", lambda *a, **k: _FakeStore())
    assert doctor.check_storage(_settings(tmp_path)).ok


def test_check_tunnel_none_reports_address(tmp_path: Path) -> None:
    check = doctor.check_tunnel(_settings(tmp_path))
    assert check.ok
    assert "203.0.113.5:25565" in check.detail
