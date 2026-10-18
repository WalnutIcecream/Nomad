from __future__ import annotations

import io
import tarfile
from pathlib import Path

from launcher import java
from launcher.config import LauncherSettings


def _settings(tmp_path: Path) -> LauncherSettings:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path / "data"
    return s


def _agent(tmp_path: Path):
    from launcher.agent import HostAgent

    world = {"id": "w1", "name": "W", "minecraft_version": "1.21.1"}
    return HostAgent(_settings(tmp_path), store=None, world=world)


def test_provisioned_java_is_none_before_install(tmp_path: Path) -> None:
    assert java.provisioned_java(_settings(tmp_path)) is None


def test_java_major_none_for_missing_binary() -> None:
    assert java.java_major("/definitely/not/java") is None
    assert java.java_major("") is None


def test_ensure_java_downloads_and_extracts(tmp_path: Path, monkeypatch) -> None:
    s = _settings(tmp_path)

    def fake_download(url, dest, **kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(dest, "w:gz") as tf:
            data = b"#!/bin/sh\n"
            info = tarfile.TarInfo("jdk-21/bin/java")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        return dest

    monkeypatch.setattr(java, "download", fake_download)
    binary = java.ensure_java(s)

    assert binary.exists()
    assert binary == java.java_in(java.runtime_dir(s))
    assert java.provisioned_java(s) == binary


def test_ensure_java_reuses_an_existing_runtime(tmp_path: Path, monkeypatch) -> None:
    s = _settings(tmp_path)
    binary = java.java_in(java.runtime_dir(s))
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"#!/bin/sh\n")

    calls: list[int] = []
    monkeypatch.setattr(java, "download", lambda *a, **k: calls.append(1))

    assert java.ensure_java(s) == binary
    assert calls == []


def test_agent_resolve_java_prefers_working_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(java, "java_major", lambda path: 21)
    agent = _agent(tmp_path)
    assert agent._resolve_java() == agent.settings.java_path


def test_agent_resolve_java_falls_back_to_provisioned(tmp_path: Path, monkeypatch) -> None:
    s = _settings(tmp_path)
    binary = java.java_in(java.runtime_dir(s))
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"x")

    monkeypatch.setattr(java, "java_major", lambda path: None)
    assert _agent(tmp_path)._resolve_java() == str(binary)


def test_agent_resolve_java_provisions_when_absent(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "provisioned-java"
    monkeypatch.setattr(java, "java_major", lambda path: None)
    monkeypatch.setattr(java, "ensure_java", lambda settings, on_progress=None: target)
    assert _agent(tmp_path)._resolve_java() == str(target)


def test_platform_arch_mapping(monkeypatch) -> None:
    monkeypatch.setattr(java.platform, "machine", lambda: "x86_64")
    assert java._platform()[1] == "x64"
    monkeypatch.setattr(java.platform, "machine", lambda: "aarch64")
    assert java._platform()[1] == "aarch64"


def test_archive_suffix_is_zip_on_windows() -> None:
    assert java._archive_suffix("windows") == ".zip"
    assert java._archive_suffix("linux") == ".tar.gz"
