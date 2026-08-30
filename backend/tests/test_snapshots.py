from __future__ import annotations

from fastapi.testclient import TestClient

from backend.tests.helpers import _acquire, _auth, _create_world, _login, _register


class TestSnapshots:
    def test_full_snapshot_flow(self, client: TestClient) -> None:
        _register(client, "snap_user")
        token = _login(client, "snap_user")
        world = _create_world(client, token)
        acquire = _acquire(client, token, world["id"])

        prepare = client.post(
            f"/worlds/{world['id']}/snapshot/prepare",
            json={"lease_id": str(acquire["lease_id"]), "base_version": 0},
            headers=_auth(token),
        )
        assert prepare.status_code == 200, prepare.text

        upload_id = prepare.json()["upload_id"]
        upload = client.put(
            f"/worlds/{world['id']}/snapshot/{upload_id}",
            content=b"fake-world-archive-bytes",
            headers=_auth(token),
        )
        assert upload.status_code == 200, upload.text

        complete = client.post(
            f"/worlds/{world['id']}/snapshot/complete",
            json={
                "upload_id": upload_id,
                "lease_id": str(acquire["lease_id"]),
                "minecraft_version": "1.21.1",
            },
            headers=_auth(token),
        )
        assert complete.status_code == 200, complete.text
        assert complete.json()["version_number"] == 1

        versions = client.get(f"/worlds/{world['id']}/versions", headers=_auth(token)).json()
        assert len(versions) == 1
        assert versions[0]["version_number"] == 1

        status = client.get(f"/worlds/{world['id']}/status", headers=_auth(token)).json()
        assert status["status"] == "sleeping"

    def test_version_conflict_rejected(self, client: TestClient) -> None:
        """Cloud is v1, host tries to push from base v0 -> rejected."""
        _register(client, "vconflict")
        token = _login(client, "vconflict")
        world = _create_world(client, token)

        acquire = _acquire(client, token, world["id"])
        prepare = client.post(
            f"/worlds/{world['id']}/snapshot/prepare",
            json={"lease_id": str(acquire["lease_id"]), "base_version": 0},
            headers=_auth(token),
        )
        assert prepare.status_code == 200
        upload_id = prepare.json()["upload_id"]
        upload = client.put(
            f"/worlds/{world['id']}/snapshot/{upload_id}",
            content=b"fake-world-archive-bytes",
            headers=_auth(token),
        )
        assert upload.status_code == 200
        client.post(
            f"/worlds/{world['id']}/snapshot/complete",
            json={
                "upload_id": upload_id,
                "lease_id": str(acquire["lease_id"]),
                "minecraft_version": "1.21.1",
            },
            headers=_auth(token),
        )

        acquire2 = _acquire(client, token, world["id"])
        prepare = client.post(
            f"/worlds/{world['id']}/snapshot/prepare",
            json={"lease_id": str(acquire2["lease_id"]), "base_version": 0},
            headers=_auth(token),
        )
        assert prepare.status_code == 409

    def test_interrupted_upload_keeps_latest(self, client: TestClient) -> None:
        """prepare without complete: latest version unchanged."""
        _register(client, "interrupt")
        token = _login(client, "interrupt")
        world = _create_world(client, token)

        acquire = _acquire(client, token, world["id"])
        prepare = client.post(
            f"/worlds/{world['id']}/snapshot/prepare",
            json={"lease_id": str(acquire["lease_id"]), "base_version": 0},
            headers=_auth(token),
        )
        assert prepare.status_code == 200
        # Never call complete.

        versions = client.get(f"/worlds/{world['id']}/versions", headers=_auth(token)).json()
        assert versions == []

        status = client.get(f"/worlds/{world['id']}/status", headers=_auth(token)).json()
        assert status["latest_version"] is None

    def test_restore_unknown_version_404(self, client: TestClient) -> None:
        _register(client, "restorer")
        token = _login(client, "restorer")
        world = _create_world(client, token)
        resp = client.post(
            f"/worlds/{world['id']}/restore",
            json={"version_number": 99},
            headers=_auth(token),
        )
        assert resp.status_code == 404
