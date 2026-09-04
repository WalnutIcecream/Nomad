"""End-to-end host-cycle tests: the real HostAgent over the fake S3 double."""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from launcher.agent import HostAgent
from launcher.cloud import WorldStore, lease_key, world_key
from launcher.tests.fake_s3 import FakeS3Client

WORLD = {"id": "w1", "name": "Survival", "minecraft_version": "1.21.1"}


def _settings(tmp: Path, player: str) -> MagicMock:
    s = MagicMock()
    s.data_dir = tmp / "data"
    s.worlds_dir = tmp / "data" / "worlds"
    s.install_dir = tmp / "runtime"
    s.heartbeat_interval_seconds = 1
    s.stop_timeout_seconds = 5
    s.minecraft_version = "1.21.1"
    s.memory = "2G"
    s.server_port = 25565
    s.player_name = player
    s.public_address = "203.0.113.5:25565"
    s.eula_accepted = True
    s.java_path = "java"
    s.lease_duration_seconds = 60
    return s


class FakeRuntime:
    """A MinecraftRuntime double whose server stays up until stopped."""

    def __init__(self, seed_world: bytes = b"alice-world") -> None:
        self.started = False
        self._running = False
        self.run_dir: Path | None = None
        self._seed_world = seed_world

    def install(self, version, dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "server.jar").write_bytes(b"jar")
        return dest / "server.jar"

    def validate(self, install_dir, java_path):
        return True, ""

    def start(self, install_dir, world_dir, properties, java_path, memory):
        self.started = True
        self._running = True
        self.run_dir = world_dir
        # Simulate the server creating its level on first boot.
        (world_dir / "world" / "region").mkdir(parents=True, exist_ok=True)
        (world_dir / "world" / "level.dat").write_bytes(self._seed_world)
        return self

    def is_running(self):
        return self._running

    def stop(self, handle=None):
        self._running = False


def _host_until_stopped(agent: HostAgent, runtime: FakeRuntime) -> int:
    result: dict = {}

    def worker():
        result["code"] = agent.host()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not runtime.started:
        time.sleep(0.02)
    time.sleep(0.2)
    agent.stop_requested.set()
    thread.join(timeout=15)
    return int(result.get("code", -1))


def test_full_host_cycle(tmp_path: Path) -> None:
    client = FakeS3Client()

    # Alice hosts, then uploads + releases.
    alice_store = WorldStore(client, player_name="alice")
    alice_runtime = FakeRuntime()
    alice = HostAgent(_settings(tmp_path, "alice"), alice_store, WORLD, runtime=alice_runtime)
    assert _host_until_stopped(alice, alice_runtime) == 0

    # The world is in the bucket and the lease is released.
    assert world_key("w1") in client.objects
    lease = json.loads(client.objects[lease_key("w1")][0].decode())
    assert lease["status"] == "released"
    assert lease["holder"] == "alice"
    assert lease["address"] == "203.0.113.5:25565"

    # Bob hosts next: acquires the released lease and boots from alice's world.
    bob_store = WorldStore(client, player_name="bob")
    # Bob's runtime seeds a FRESH world on boot; if alice's world was pulled,
    # bob's run dir would already contain alice's level.dat (runtime start
    # overwrites with its own seed afterward, so check the upload object).
    bob_runtime = FakeRuntime()
    bob = HostAgent(_settings(tmp_path, "bob"), bob_store, WORLD, runtime=bob_runtime)
    assert _host_until_stopped(bob, bob_runtime) == 0

    # Alice's world object is what bob pulled and re-uploaded (content intact).
    assert world_key("w1") in client.objects
    # And the lease shows bob as the latest holder after his release.
    lease = json.loads(client.objects[lease_key("w1")][0].decode())
    assert lease["status"] == "released"
    assert lease["holder"] == "bob"

    # A third party is denied while an active lease is held.
    dave_store = WorldStore(client, player_name="dave")
    dave_store.acquire("w1")
    carol_store = WorldStore(client, player_name="carol")
    import pytest

    with pytest.raises(Exception) as exc:
        carol_store.acquire("w1")
    assert "hosted by" in str(exc.value)


def test_host_denied_when_someone_else_active(tmp_path: Path) -> None:
    client = FakeS3Client()
    alice_store = WorldStore(client, player_name="alice")
    alice_store.acquire("w1")

    # Bob presses Play while alice is active: agent returns 1 (host exists).
    bob_runtime = FakeRuntime()
    bob_store = WorldStore(client, player_name="bob")
    bob = HostAgent(_settings(tmp_path, "bob"), bob_store, WORLD, runtime=bob_runtime)
    assert bob.host() == 1
    assert bob_runtime.started is False
