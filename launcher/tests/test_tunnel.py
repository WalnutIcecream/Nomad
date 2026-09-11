from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from launcher.config import LauncherSettings
from launcher.tunnel import ReverseTunnel, TunnelError


# --- command / address ---------------------------------------------------


def _connection(target="me@box", key="", port=None, control_path=None):
    from launcher.ssh_connection import SshConnection

    return SshConnection(target=target, key=key, port=port, control_path=control_path)


def test_command_forwards_remote_to_local() -> None:
    tunnel = ReverseTunnel(_connection(), remote_port=25565, local_port=25565)
    cmd = tunnel.command()
    assert cmd[0] == "ssh"
    assert "-N" in cmd
    assert "25565:localhost:25565" in cmd
    assert "ExitOnForwardFailure=yes" in cmd
    assert cmd[-1] == "me@box"


def test_command_includes_key_and_port() -> None:
    tunnel = ReverseTunnel(
        _connection(key="/k/id", port=2222), 30000, 7777
    )
    cmd = tunnel.command()
    assert "-i" in cmd and "/k/id" in cmd
    assert "-p" in cmd and "2222" in cmd
    assert "30000:localhost:7777" in cmd


def test_tunnel_reuses_the_shared_control_socket(tmp_path) -> None:
    """The tunnel must ride the same multiplexed connection as storage."""
    tunnel = ReverseTunnel(
        _connection(control_path=tmp_path / "ctl"), 25565, 25565
    )
    cmd = tunnel.command()
    assert "ControlMaster=auto" in cmd
    assert any(tok == f"ControlPath={tmp_path / 'ctl'}" for tok in cmd)


def test_address_derives_host_from_target() -> None:
    assert ReverseTunnel(_connection("me@box.example"), 25565, 25565).address == "box.example:25565"


def test_address_uses_explicit_remote_host() -> None:
    tunnel = ReverseTunnel(
        _connection(), 25565, 25565, remote_host="play.example.com"
    )
    assert tunnel.address == "play.example.com:25565"


# --- lifecycle -----------------------------------------------------------


class _FakePopen:
    """A stand-in ssh process. ``exit_code=None`` means it stays running."""

    instances: list["_FakePopen"] = []
    exit_code = None
    stderr_bytes = b""

    def __init__(self, cmd, **kwargs) -> None:
        self.cmd = cmd
        self.stderr = io.BytesIO(type(self).stderr_bytes)
        self.terminated = False
        self.killed = False
        type(self).instances.append(self)

    def poll(self):
        if self.terminated or self.killed:
            return 0
        return type(self).exit_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> int:
        return 0


class _FailingPopen(_FakePopen):
    exit_code = 255
    stderr_bytes = b"remote port forwarding failed"


def test_start_succeeds_when_process_stays_up() -> None:
    _FakePopen.instances.clear()
    tunnel = ReverseTunnel(_connection(), 25565, 25565)
    with patch("launcher.tunnel.subprocess.Popen", _FakePopen):
        tunnel.start()
    assert tunnel.is_running() is True
    tunnel.stop()
    assert tunnel.is_running() is False
    assert _FakePopen.instances[0].terminated is True


def test_start_raises_when_ssh_exits_immediately() -> None:
    tunnel = ReverseTunnel(_connection(), 25565, 25565)
    with patch("launcher.tunnel.subprocess.Popen", _FailingPopen):
        with pytest.raises(TunnelError) as exc:
            tunnel.start()
    assert "forwarding failed" in str(exc.value)


def test_context_manager_stops_tunnel() -> None:
    _FakePopen.instances.clear()
    with patch("launcher.tunnel.subprocess.Popen", _FakePopen):
        with ReverseTunnel(_connection(), 25565, 25565) as tunnel:
            assert tunnel.is_running() is True
    assert _FakePopen.instances[0].terminated is True


# --- agent integration ---------------------------------------------------


class _FakeStore:
    name = "ssh"

    def __init__(self) -> None:
        self.acquired_address = None
        self.released = False

    def acquire(self, world_id, address=None):
        from launcher.cloud import Lease

        self.acquired_address = address
        return Lease(status="active", holder="me", etag="L1")

    def download_world(self, world_id, dest) -> bool:
        return False

    def renew(self, world_id, lease):
        return lease

    def release(self, world_id, lease) -> None:
        self.released = True

    def upload_world(self, world_id, archive) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeRuntime:
    def __init__(self) -> None:
        self.started = False
        self._running = False

    def install(self, version, dest):
        return dest

    def validate(self, install_dir, java_path):
        return True, ""

    def start(self, *a, **k):
        self.started = True
        self._running = True
        return self

    def is_running(self) -> bool:
        return self._running

    def stop(self, handle=None) -> None:
        self._running = False


class _RecordingTunnel:
    created: list["_RecordingTunnel"] = []

    def __init__(self, connection, remote_port, local_port, remote_host="") -> None:
        self.connection = connection
        self.remote_port = remote_port
        self.local_port = local_port
        self.remote_host = remote_host
        host = remote_host or connection.host
        self.address = f"{host}:{remote_port}"
        self.started = False
        self.stopped = False
        _RecordingTunnel.created.append(self)

    def start(self):
        self.started = True
        return self

    def stop(self) -> None:
        self.stopped = True


def test_agent_publishes_tunnel_address_and_stops_tunnel(tmp_path: Path) -> None:
    from launcher.agent import HostAgent

    _RecordingTunnel.created.clear()
    settings = LauncherSettings(_env_file=None)
    settings.data_dir = tmp_path
    settings.ssh_reverse_tunnel = True
    settings.ssh_target = "me@box"
    settings.ssh_remote_port = 25565
    settings.server_port = 25565
    settings.memory = "1G"

    store = _FakeStore()
    runtime = _FakeRuntime()
    agent = HostAgent(settings, store, {"id": "w1", "name": "W", "minecraft_version": "1.21.1"}, runtime=runtime)

    result: dict = {}

    def work():
        result["code"] = agent.host()

    with patch("launcher.agent.ReverseTunnel", _RecordingTunnel):
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        deadline = time.time() + 10
        while time.time() < deadline and not runtime.started:
            time.sleep(0.02)
        time.sleep(0.1)
        agent.stop_requested.set()
        thread.join(timeout=15)

    assert result.get("code") == 0
    # The tunnel address was published in the lease...
    assert store.acquired_address == "box:25565"
    # ...and the tunnel was opened then closed around the session.
    tunnel = _RecordingTunnel.created[0]
    assert tunnel.started is True
    assert tunnel.stopped is True


def test_agent_without_tunnel_publishes_public_address(tmp_path: Path) -> None:
    from launcher.agent import HostAgent

    settings = LauncherSettings(_env_file=None)
    settings.data_dir = tmp_path
    settings.ssh_reverse_tunnel = False
    settings.public_address = "203.0.113.9:25565"
    settings.server_port = 25565

    store = _FakeStore()
    runtime = _FakeRuntime()
    agent = HostAgent(settings, store, {"id": "w1", "name": "W", "minecraft_version": "1.21.1"}, runtime=runtime)
    result: dict = {}

    def work():
        result["code"] = agent.host()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not runtime.started:
        time.sleep(0.02)
    time.sleep(0.1)
    agent.stop_requested.set()
    thread.join(timeout=15)

    assert store.acquired_address == "203.0.113.9:25565"
    assert agent.tunnel is None