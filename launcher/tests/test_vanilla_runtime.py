from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from shared.protocol.models import ServerProperties

from launcher.minecraft.process import ProcessHandle
from launcher.minecraft.vanilla import VanillaMinecraftRuntime

JAVA_AVAILABLE = shutil.which("java") is not None


def _fake_java(tmp_path: Path, version: str) -> Path:
    """A shim java executable that reports the given version and exits 0."""
    fake = tmp_path / "java21.bat"
    fake.write_text(f"@echo off\r\necho openjdk version \"{version}\"\r\n", encoding="utf-8")
    return fake


@pytest.fixture
def runtime() -> VanillaMinecraftRuntime:
    return VanillaMinecraftRuntime()


def test_validate_missing_jar(runtime: VanillaMinecraftRuntime, tmp_path: Path) -> None:
    install_dir = tmp_path / "empty"
    install_dir.mkdir()
    ok, reason = runtime.validate(install_dir, "java")
    assert not ok
    assert "server.jar" in reason


def test_validate_with_java_checks_jar(
    runtime: VanillaMinecraftRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dir = tmp_path / "runtime"
    install_dir.mkdir()
    (install_dir / "server.jar").write_bytes(b"placeholder")
    fake_java = _fake_java(tmp_path, "21.0.4")
    monkeypatch.setattr(shutil, "which", lambda _name: str(fake_java))
    ok, reason = runtime.validate(install_dir, "java")
    assert ok, reason


def test_validate_rejects_old_java(
    runtime: VanillaMinecraftRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dir = tmp_path / "runtime"
    install_dir.mkdir()
    (install_dir / "server.jar").write_bytes(b"placeholder")
    fake_java = _fake_java(tmp_path, "1.8.0_401")
    monkeypatch.setattr(shutil, "which", lambda _name: str(fake_java))
    ok, reason = runtime.validate(install_dir, "java")
    assert not ok
    assert "21" in reason


def test_process_handle_lifecycle(tmp_path: Path) -> None:
    """Exercise ProcessHandle with a python process that reads commands.

    The process prints READY and echoes STOP when it receives the word ``stop``.
    """
    script = (
        "import sys\n"
        "print('READY', flush=True)\n"
        "for line in sys.stdin:\n"
        "    if line.strip() == 'stop':\n"
        "        print('STOPPED', flush=True)\n"
        "        break\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    handle = ProcessHandle(process, tmp_path)
    assert handle.is_running()

    # Wait briefly for READY to be captured.
    deadline = 100
    while deadline > 0 and not any("READY" in line for line in handle.logs()):
        deadline -= 1
        import time

        time.sleep(0.05)
    assert any("READY" in line for line in handle.logs())

    handle.send_command("stop")
    exit_code = handle.wait(timeout=10)
    assert exit_code == 0
    assert not handle.is_running()
    assert any("STOPPED" in line for line in handle.logs())
