from __future__ import annotations

from pathlib import Path
from uuid import UUID

import httpx

from shared.protocol.enums import WorldStatus

API_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class ControllerError(Exception):
    """Raised for non-2xx responses, carrying the HTTP status and detail."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ControllerClient:
    """HTTP client for the Nomad controller API (Stage 4+)."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.Client(
            base_url=self.base_url, timeout=API_TIMEOUT, follow_redirects=True
        )

    def close(self) -> None:
        try:
            self._client.close()
        except AttributeError:
            # ASGITransport (tests) has no sync close(); ignore.
            pass

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ControllerError(response.status_code, str(detail))

    # --- worlds ---------------------------------------------------------

    def list_worlds(self) -> list[dict]:
        response = self._client.get("/worlds", headers=self._headers())
        self._raise_for_status(response)
        return response.json()

    def create_world(self, name: str, minecraft_version: str) -> dict:
        response = self._client.post(
            "/worlds",
            json={"name": name, "minecraft_version": minecraft_version},
            headers=self._headers(),
        )
        self._raise_for_status(response)
        return response.json()

    def get_world(self, world_id: UUID) -> dict:
        response = self._client.get(f"/worlds/{world_id}", headers=self._headers())
        self._raise_for_status(response)
        return response.json()

    def get_world_status(self, world_id: UUID) -> dict:
        response = self._client.get(f"/worlds/{world_id}/status", headers=self._headers())
        self._raise_for_status(response)
        return response.json()

    # --- connection ------------------------------------------------------

    def update_connection(self, world_id: UUID, connection: dict) -> dict:
        response = self._client.put(
            f"/worlds/{world_id}/connection",
            json=connection,
            headers=self._headers(),
        )
        self._raise_for_status(response)
        return response.json()

    def get_connection(self, world_id: UUID) -> dict:
        response = self._client.get(f"/worlds/{world_id}/connection", headers=self._headers())
        self._raise_for_status(response)
        return response.json()

    # --- host lease -----------------------------------------------------

    def acquire_host(self, world_id: UUID) -> dict:
        response = self._client.post(
            f"/worlds/{world_id}/host/acquire", headers=self._headers()
        )
        self._raise_for_status(response)
        return response.json()

    def heartbeat(self, world_id: UUID, lease_id: UUID) -> dict:
        response = self._client.post(
            f"/worlds/{world_id}/host/heartbeat",
            json={"lease_id": str(lease_id)},
            headers=self._headers(),
        )
        self._raise_for_status(response)
        return response.json()

    def release_host(self, world_id: UUID, lease_id: UUID) -> None:
        response = self._client.post(
            f"/worlds/{world_id}/host/release",
            json={"lease_id": str(lease_id)},
            headers=self._headers(),
        )
        self._raise_for_status(response)

    # --- snapshots ------------------------------------------------------

    def snapshot_prepare(self, world_id: UUID, lease_id: UUID, base_version: int) -> dict:
        response = self._client.post(
            f"/worlds/{world_id}/snapshot/prepare",
            json={"lease_id": str(lease_id), "base_version": base_version},
            headers=self._headers(),
        )
        self._raise_for_status(response)
        return response.json()

    def upload_snapshot(self, world_id: UUID, upload_id: UUID, archive_path: Path) -> None:
        with archive_path.open("rb") as handle:
            response = self._client.put(
                f"/worlds/{world_id}/snapshot/{upload_id}",
                content=handle.read(),
                headers=self._headers(),
            )
        self._raise_for_status(response)

    def snapshot_complete(
        self,
        world_id: UUID,
        lease_id: UUID,
        upload_id: UUID,
        minecraft_version: str,
        server_version: str | None = None,
    ) -> dict:
        response = self._client.post(
            f"/worlds/{world_id}/snapshot/complete",
            json={
                "upload_id": str(upload_id),
                "lease_id": str(lease_id),
                "minecraft_version": minecraft_version,
                "server_version": server_version,
            },
            headers=self._headers(),
        )
        self._raise_for_status(response)
        return response.json()

    def list_versions(self, world_id: UUID) -> list[dict]:
        response = self._client.get(f"/worlds/{world_id}/versions", headers=self._headers())
        self._raise_for_status(response)
        return response.json()

    def restore_request(self, world_id: UUID, version_number: int) -> str:
        response = self._client.post(
            f"/worlds/{world_id}/restore",
            json={"version_number": version_number},
            headers=self._headers(),
        )
        self._raise_for_status(response)
        return response.json()["download_url"]

    def download_version(self, world_id: UUID, version_number: int, dest_path: Path) -> None:
        url = self.restore_request(world_id, version_number)
        response = self._client.get(url, headers=self._headers())
        self._raise_for_status(response)
        dest_path.write_bytes(response.content)
