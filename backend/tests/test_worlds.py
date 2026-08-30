from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from backend.tests.helpers import _auth, _create_world, _login, _register


class TestWorlds:
    def test_create_and_list(self, client: TestClient) -> None:
        _register(client, "dave")
        token = _login(client, "dave")
        world = _create_world(client, token)
        assert world["name"] == "Walnut SMP"
        assert world["status"] == "sleeping"
        assert world["member_count"] == 1

        worlds = client.get("/worlds", headers=_auth(token)).json()
        assert len(worlds) == 1
        assert worlds[0]["id"] == world["id"]

    def test_non_member_cannot_view(self, client: TestClient) -> None:
        _register(client, "owner1")
        _register(client, "intruder")
        owner_token = _login(client, "owner1")
        intruder_token = _login(client, "intruder")

        world = _create_world(client, owner_token)
        resp = client.get(f"/worlds/{world['id']}", headers=_auth(intruder_token))
        assert resp.status_code == 403

    def test_non_member_cannot_acquire_host(self, client: TestClient) -> None:
        _register(client, "owner2")
        _register(client, "outsider")
        owner_token = _login(client, "owner2")
        outsider_token = _login(client, "outsider")

        world = _create_world(client, owner_token)
        resp = client.post(f"/worlds/{world['id']}/host/acquire", headers=_auth(outsider_token))
        assert resp.status_code == 403

    def test_add_member_then_view(self, client: TestClient) -> None:
        _register(client, "owner3")
        _register(client, "friend")
        owner_token = _login(client, "owner3")
        friend_token = _login(client, "friend")

        world = _create_world(client, owner_token)
        friend_login = client.post(
            "/auth/login", json={"username": "friend", "password": "password123"}
        ).json()
        friend_user_id = friend_login["user"]["id"]

        resp = client.post(
            f"/worlds/{world['id']}/members",
            json={"user_id": str(friend_user_id)},
            headers=_auth(owner_token),
        )
        assert resp.status_code == 201, resp.text

        worlds = client.get("/worlds", headers=_auth(friend_token)).json()
        assert len(worlds) == 1

        members = client.get(f"/worlds/{world['id']}/members", headers=_auth(owner_token)).json()
        assert {m["username"] for m in members} == {"owner3", "friend"}

    def test_non_owner_cannot_add_member(self, client: TestClient) -> None:
        _register(client, "owner4")
        _register(client, "meddler")
        owner_token = _login(client, "owner4")
        meddler_token = _login(client, "meddler")

        world = _create_world(client, owner_token)
        resp = client.post(
            f"/worlds/{world['id']}/members",
            json={"user_id": str(uuid4())},
            headers=_auth(meddler_token),
        )
        assert resp.status_code == 403
