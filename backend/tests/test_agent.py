from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.tests.helpers import _auth, _create_world, _login, _register
from launcher.agent import HostAgent
from launcher.config import LauncherSettings
from launcher.state.machine import LauncherStateMachine
from launcher.state.persist import StateStore


class FakeRuntime:
    """Minimal MinecraftRuntime double that runs a stub 'server' process."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.install_calls = 0

    def install(self, version: str, dest: Path) -> Path:
        self.install_calls += 1
        dest.mkdir(parents=True, exist_ok=True)
        jar = dest / "server.jar"
        jar.write_bytes(b"stub")
        return jar

    def validate(self, install_dir: Path, java_path: str) -> tuple[bool, str]:
        return True, ""

    def start(self, install_dir: Path, world_dir: Path, properties, java_path: str, memory: str):
        self.started = True
        script = (
            "import time\n"
            "print('READY', flush=True)\n"
            "for line in __import__('sys').stdin:\n"
            "    if line.strip() == 'stop':\n"
            "        print('STOPPED', flush=True)\n"
            "        break\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        from launcher.minecraft.process import ProcessHandle

        return ProcessHandle(proc, world_dir)

    def stop(self, handle) -> None:
        self.stopped = True
        handle.send_command("stop")

    def is_running(self, handle) -> bool:
        return handle.is_running()

    def get_logs(self, handle):
        return iter(handle.logs())

    def get_version(self, install_dir: Path) -> str:
        return "1.21.1"


@pytest.fixture()
def agent_env(client: TestClient, controller_url: str, tmp_path: Path):
    """Wire up a HostAgent with a fake runtime against the live test server."""
    _register(client, "agentuser")
    token = _login(client, "agentuser")
    world = _create_world(client, token)
    world_id = world["id"]

    from launcher.controller import ControllerClient

    hx = ControllerClient(controller_url, token)

    settings = LauncherSettings(
        data_dir=tmp_path / "data",
        controller_url=controller_url,
        controller_token=token,
        heartbeat_interval_seconds=1,
        eula_accepted=True,
    )
    machine = LauncherStateMachine(StateStore(settings.state_file))
    agent = HostAgent(settings, machine, hx, world_id, FakeRuntime())
    return agent, client, token, world


class TestHostAgent:
    def test_full_host_lifecycle(self, agent_env) -> None:
        """Host -> graceful stop -> snapshot uploaded -> world sleeping."""
        agent, client, token, world = agent_env

        import threading

        def stop_later() -> None:
            import time

            time.sleep(2.0)
            agent.stop_requested.set()

        threading.Thread(target=stop_later, daemon=True).start()
        code = agent.host()
        assert code == 0

        status = client.get(f"/worlds/{world['id']}/status", headers=_auth(token)).json()
        assert status["status"] == "sleeping"
        assert status["latest_version"] == 1

        # The agent's state machine ended cleanly in IDLE.
        assert agent.machine.state.value == "idle"

    def test_crash_leaves_world_recoverable(self, agent_env) -> None:
        """A hard server crash is detected; cloud keeps last known-good."""
        import threading
        import time

        agent, client, token, world = agent_env

        # First run uploads v1 (stop the fake server after 2s).
        def stop_later() -> None:
            time.sleep(2.0)
            agent.stop_requested.set()

        threading.Thread(target=stop_later, daemon=True).start()
        assert agent.host() == 0

        # Second run: make the fake runtime crash by killing the process.
        from launcher.agent import HostAgent

        settings = agent.settings
        machine = LauncherStateMachine(StateStore(settings.state_file))
        runtime = FakeRuntime()

        # Patch start to return a process that dies immediately.
        class CrashedRuntime(FakeRuntime):
            def start(self, install_dir, world_dir, properties, java_path, memory):
                proc = subprocess.Popen(
                    [sys.executable, "-c", "import sys; sys.exit(3)"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                from launcher.minecraft.process import ProcessHandle

                return ProcessHandle(proc, world_dir)

        agent2 = HostAgent(settings, machine, agent.client, world["id"], CrashedRuntime())
        code = agent2.host()
        assert code == 1
        assert agent2.machine.state.value == "recover"

        # Cloud is untouched: still v1. The world stays claimed (starting)
        # until the crashed host's lease expires — that's the designed
        # split-brain guard, not corruption.
        status = client.get(f"/worlds/{world['id']}/status", headers=_auth(token)).json()
        assert status["latest_version"] == 1

    def test_second_host_redirected(self, agent_env) -> None:
        """If someone else already holds the lease, host() reports it."""
        agent, client, token, world = agent_env
        # Someone else (same token for simplicity) acquires first.
        first = client.post(f"/worlds/{world['id']}/host/acquire", headers=_auth(token)).json()
        assert first["acquired"]

        code = agent.host()
        assert code == 1
        assert agent.machine.state.value == "host_exists"
