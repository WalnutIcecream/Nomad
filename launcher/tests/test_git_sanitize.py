from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from launcher.cloud import LeaseError
from launcher.storage import git as git_module
from launcher.storage.git import GitWorldStore


def _store(tmp_path: Path, remote: str = "https://user:ghp_SECRETTOKEN@example.com/x.git"):
    return GitWorldStore(tmp_path / "repo", remote_url=remote)


def test_git_stderr_is_redacted_before_reaching_the_error(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    failing = SimpleNamespace(
        returncode=128,
        stdout="",
        stderr="fatal: could not read from 'https://user:ghp_SECRETTOKEN@example.com/x.git'\n",
    )
    monkeypatch.setattr(git_module.subprocess, "run", lambda *a, **k: failing)

    with pytest.raises(LeaseError) as exc:
        store._git("push", "origin", "master")
    assert "ghp_SECRETTOKEN" not in exc.value.detail
    assert "user:***@" in exc.value.detail


def test_push_error_is_redacted(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    failing = SimpleNamespace(
        returncode=1,
        stdout="",
        stderr="remote: auth failed for https://user:ghp_SECRETTOKEN@example.com/x.git\n",
    )
    monkeypatch.setattr(git_module.subprocess, "run", lambda *a, **k: failing)

    with pytest.raises(LeaseError) as exc:
        store._push()
    assert "ghp_SECRETTOKEN" not in exc.value.detail


def test_git_success_path_unchanged(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    ok = SimpleNamespace(returncode=0, stdout="  master  \n", stderr="")
    monkeypatch.setattr(git_module.subprocess, "run", lambda *a, **k: ok)
    assert store._git("rev-parse", "HEAD") == "master"