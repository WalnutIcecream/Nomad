from __future__ import annotations

from fastapi.testclient import TestClient

from backend.tests.helpers import _login, _register


class TestAuth:
    def test_register_login_roundtrip(self, client: TestClient) -> None:
        user = _register(client, "alice")
        assert user["username"] == "alice"
        token = _login(client, "alice")
        assert len(token) > 20

    def test_duplicate_username_conflict(self, client: TestClient) -> None:
        _register(client, "bob")
        resp = client.post(
            "/auth/register", json={"username": "bob", "password": "password123"}
        )
        assert resp.status_code == 409

    def test_wrong_password_rejected(self, client: TestClient) -> None:
        _register(client, "carol")
        resp = client.post(
            "/auth/login", json={"username": "carol", "password": "wrongpass"}
        )
        assert resp.status_code == 401

    def test_missing_token_rejected(self, client: TestClient) -> None:
        resp = client.get("/worlds")
        assert resp.status_code == 401
