from __future__ import annotations

import logging
from pathlib import Path

from launcher import __version__, diagnostics, logs
from launcher.config import LauncherSettings


def _settings(tmp_path: Path) -> LauncherSettings:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path / "data"
    return s


def test_collect_includes_version_and_config(tmp_path: Path) -> None:
    text = diagnostics.collect(_settings(tmp_path))
    assert __version__ in text
    assert "== configuration ==" in text
    assert "storage_backend:" in text


def test_collect_never_leaks_secret_values(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    s.r2_secret_key = "supersecretvalue"
    s.server_endpoint_url = "https://user:password123@storage.example.com"

    text = diagnostics.collect(s)
    assert "supersecretvalue" not in text
    assert "password123" not in text  # URL userinfo is masked
    assert "r2_secret_key: set" in text


def test_collect_reports_absent_secrets_as_dash(tmp_path: Path) -> None:
    assert "r2_secret_key: -" in diagnostics.collect(_settings(tmp_path))


def test_collect_includes_the_log_tail(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    path = logs.log_path(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("first-line\nlast-line-here\n", encoding="utf-8")

    assert "last-line-here" in diagnostics.collect(s, tail_lines=5)


def test_logs_configure_writes_records_to_the_file(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    logs.reset()
    logs.configure(s)
    try:
        logging.getLogger("nomad.tests").warning("hello-from-the-file-logger")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert "hello-from-the-file-logger" in logs.log_path(s).read_text(encoding="utf-8")
    finally:
        logs.reset()
