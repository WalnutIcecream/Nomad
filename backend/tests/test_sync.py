from __future__ import annotations

import multiprocessing
import time
from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.tests.helpers import _auth, _create_world, _login, _register
from launcher.controller import ControllerClient, ControllerError
from launcher.sync.snapshot import pull_latest_world, push_world_snapshot


@pytest.fixture()
def launcher_client(client: TestClient, controller_url: str) -> ControllerClient:
    _register(client, "syncuser")
    token = _login(client, "syncuser")
    hx = ControllerClient(controller_url, token)
    yield hx
    hx.close()


def _make_world_dir(tmp_path: Path, content: bytes) -> Path:
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(content)
    (world / "region").mkdir()
    (world / "region" / "r.0.0.mca").write_bytes(content * 4)
    return world


class TestSync:
    def test_push_then_pull_roundtrip(
        self, client: TestClient, launcher_client: ControllerClient, tmp_path: Path
    ) -> None:
        """Alice pushes a snapshot; Bob pulls it and sees her exact world."""
        token = _login(client, "syncuser")
        world = _create_world(client, token)
        world_id = world["id"]

        acquire = client.post(f"/worlds/{world_id}/host/acquire", headers=_auth(token)).json()
        assert acquire["acquired"]

        world_dir = _make_world_dir(tmp_path, b"alice-built-a-castle")
        version = push_world_snapshot(
            launcher_client, world_id, acquire["lease_id"], world_dir, base_version=0, minecraft_version="1.21.1"
        )
        assert version == 1

        bob_dir = tmp_path / "bob-world"
        pulled = pull_latest_world(launcher_client, world_id, bob_dir)
        assert pulled == 1
        assert (bob_dir / "level.dat").read_bytes() == b"alice-built-a-castle"
        assert (bob_dir / "region" / "r.0.0.mca").read_bytes() == b"alice-built-a-castle" * 4

    def test_interrupted_upload_keeps_latest(
        self, client: TestClient, launcher_client: ControllerClient, tmp_path: Path
    ) -> None:
        token = _login(client, "syncuser")
        world = _create_world(client, token)
        acquire = client.post(f"/worlds/{world['id']}/host/acquire", headers=_auth(token)).json()
        world_dir = _make_world_dir(tmp_path, b"v1")
        push_world_snapshot(launcher_client, world["id"], acquire["lease_id"], world_dir, 0, "1.21.1")

        acquire2 = client.post(f"/worlds/{world['id']}/host/acquire", headers=_auth(token)).json()
        prepare = launcher_client.snapshot_prepare(world["id"], acquire2["lease_id"], base_version=1)
        assert prepare["upload_id"]

        versions = launcher_client.list_versions(world["id"])
        assert [v["version_number"] for v in versions] == [1]

    def test_conflict_rejected(
        self, client: TestClient, launcher_client: ControllerClient, tmp_path: Path
    ) -> None:
        token = _login(client, "syncuser")
        world = _create_world(client, token)
        acquire = client.post(f"/worlds/{world['id']}/host/acquire", headers=_auth(token)).json()
        world_dir = _make_world_dir(tmp_path, b"v1")
        push_world_snapshot(launcher_client, world["id"], acquire["lease_id"], world_dir, 0, "1.21.1")

        acquire2 = client.post(f"/worlds/{world['id']}/host/acquire", headers=_auth(token)).json()
        with pytest.raises(ControllerError) as excinfo:
            push_world_snapshot(
                launcher_client, world["id"], acquire2["lease_id"], world_dir, base_version=0, minecraft_version="1.21.1"
            )
        assert excinfo.value.status_code == 409
        versions = launcher_client.list_versions(world["id"])
        assert [v["version_number"] for v in versions] == [1]

    def test_expired_lease_upload_rejected(
        self, client: TestClient, launcher_client: ControllerClient, tmp_path: Path
    ) -> None:
        token = _login(client, "syncuser")
        world = _create_world(client, token)
        acquire = client.post(f"/worlds/{world['id']}/host/acquire", headers=_auth(token)).json()
        world_dir = _make_world_dir(tmp_path, b"data")

        from backend.db.session import get_engine
        from sqlalchemy import text as sa_text

        with get_engine().connect() as conn:
            conn.execute(
                sa_text("UPDATE host_leases SET status='expired' WHERE id = :lid"),
                {"lid": str(acquire["lease_id"])},
            )
            conn.commit()

        with pytest.raises(ControllerError) as excinfo:
            push_world_snapshot(
                launcher_client, world["id"], acquire["lease_id"], world_dir, 0, "1.21.1"
            )
        assert excinfo.value.status_code == 409
