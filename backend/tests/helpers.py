from __future__ import annotations

from fastapi.testclient import TestClient


def _register(client: TestClient, username: str, password: str = "password123") -> dict:
    resp = client.post("/auth/register", json={"username": username, "password": password})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(client: TestClient, username: str, password: str = "password123") -> str:
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_world(client: TestClient, token: str, name: str = "Walnut SMP") -> dict:
    resp = client.post(
        "/worlds",
        json={"name": name, "minecraft_version": "1.21.1"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _acquire(client: TestClient, token: str, world_id: str) -> dict:
    return client.post(f"/worlds/{world_id}/host/acquire", headers=_auth(token)).json()
