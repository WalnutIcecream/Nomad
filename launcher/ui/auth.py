from __future__ import annotations

import httpx

from launcher.config import LauncherSettings
from launcher.controller import ControllerClient


def login(settings: LauncherSettings, username: str, password: str, register: bool = False) -> str:
    """Authenticate against the controller and return a session token."""
    url = settings.controller_url.rstrip("/")
    with httpx.Client(timeout=30) as client:
        if register:
            response = client.post(
                f"{url}/auth/register",
                json={"username": username, "password": password},
            )
            if response.status_code >= 400:
                detail = response.json().get("detail", response.text)
                raise RuntimeError(f"registration failed: {detail}")
        response = client.post(
            f"{url}/auth/login",
            json={"username": username, "password": password},
        )
        if response.status_code >= 400:
            raise RuntimeError("login failed: check credentials")
        token = response.json()["token"]

    settings.controller_token = token
    return token
