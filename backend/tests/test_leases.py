from __future__ import annotations

import threading
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.tests.helpers import _acquire, _auth, _create_world, _login, _register


def _add_bob_to_world(client: TestClient, token: str, world_id: str, bob_username: str) -> None:
    bob_login = client.post(
        "/auth/login", json={"username": bob_username, "password": "password123"}
    ).json()
    resp = client.post(
        f"/worlds/{world_id}/members",
        json={"user_id": str(bob_login["user"]["id"])},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text


class TestLeases:
    def test_acquire_then_release(self, client: TestClient) -> None:
        _register(client, "host1")
        token = _login(client, "host1")
        world = _create_world(client, token)

        acquire = _acquire(client, token, world["id"])
        assert acquire["acquired"] is True
        assert acquire["lease_id"] is not None

        resp = client.post(
            f"/worlds/{world['id']}/host/release",
            json={"lease_id": str(acquire["lease_id"])},
            headers=_auth(token),
        )
        assert resp.status_code == 204

        status = client.get(f"/worlds/{world['id']}/status", headers=_auth(token)).json()
        assert status["status"] == "sleeping"
        reacquire = _acquire(client, token, world["id"])
        assert reacquire["acquired"] is True

    def test_second_host_rejected(self, client: TestClient) -> None:
        _register(client, "alice_host")
        _register(client, "bob_host")
        alice_token = _login(client, "alice_host")
        bob_token = _login(client, "bob_host")

        world = _create_world(client, alice_token)
        _add_bob_to_world(client, alice_token, world["id"], "bob_host")

        acquire_alice = _acquire(client, alice_token, world["id"])
        assert acquire_alice["acquired"] is True

        acquire_bob = _acquire(client, bob_token, world["id"])
        assert acquire_bob["acquired"] is False
        assert acquire_bob["current_host"] is not None

    def test_concurrent_acquire_exactly_one_host(self, client: TestClient) -> None:
        """Two simultaneous acquire requests: exactly one succeeds."""
        _register(client, "racer1")
        _register(client, "racer2")
        t1 = _login(client, "racer1")
        t2 = _login(client, "racer2")
        world = _create_world(client, t1)
        _add_bob_to_world(client, t1, world["id"], "racer2")

        results: list[dict] = []
        errors: list[Exception] = []

        def hit(token: str) -> None:
            try:
                resp = client.post(f"/worlds/{world['id']}/host/acquire", headers=_auth(token))
                results.append(resp.json())
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [
            threading.Thread(target=hit, args=(t1,)),
            threading.Thread(target=hit, args=(t2,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        assert len(results) == 2
        acquired = [r for r in results if r.get("acquired")]
        rejected = [r for r in results if not r.get("acquired")]
        assert len(acquired) == 1
        assert len(rejected) == 1

    def test_heartbeat_without_lease_rejected(self, client: TestClient) -> None:
        _register(client, "hb_user")
        token = _login(client, "hb_user")
        world = _create_world(client, token)
        resp = client.post(
            f"/worlds/{world['id']}/host/heartbeat",
            json={"lease_id": str(uuid4())},
            headers=_auth(token),
        )
        assert resp.status_code == 409

    def test_stale_host_upload_rejected(self, client: TestClient) -> None:
        """Alice's lease expires, Bob acquires; Alice's upload must be rejected."""
        _register(client, "stale_alice")
        _register(client, "stale_bob")
        alice_token = _login(client, "stale_alice")
        bob_token = _login(client, "stale_bob")

        world = _create_world(client, alice_token)
        _add_bob_to_world(client, alice_token, world["id"], "stale_bob")

        acquire_alice = _acquire(client, alice_token, world["id"])

        # Alice's lease is force-expired by the DB (simulating a crashed host
        # whose heartbeats stopped and lease duration elapsed).
        from backend.db.session import get_engine
        from sqlalchemy import text as sa_text

        with get_engine().connect() as conn:
            conn.execute(
                sa_text("UPDATE host_leases SET status='expired' WHERE id = :lease_id"),
                {"lease_id": str(acquire_alice["lease_id"])},
            )
            conn.commit()

        acquire_bob = _acquire(client, bob_token, world["id"])
        assert acquire_bob["acquired"] is True

        # Alice tries to upload a snapshot with her dead lease.
        prepare = client.post(
            f"/worlds/{world['id']}/snapshot/prepare",
            json={"lease_id": str(acquire_alice["lease_id"]), "base_version": 0},
            headers=_auth(alice_token),
        )
        assert prepare.status_code == 409
