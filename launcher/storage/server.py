"""Self-hosted S3 backend — "My own server".

This is the second and last user-facing storage direction: an S3-compatible
object store you run yourself, on a VPS or a home server. Garage is the
supported implementation; anything S3-compatible works. Storage and bandwidth
are whatever your machine gives you — no per-operation or per-GB bill, full
control, and no third party holding your worlds.

Nomad never configures Garage for you. Garage is server-side infrastructure:
the administrator sets up the server, creates the bucket, and mints an access
key; the app only needs to reach its S3 API.

Settings::

    NOMAD_STORAGE_BACKEND=server
    NOMAD_SERVER_ENDPOINT=https://storage.example.com
    NOMAD_SERVER_BUCKET=nomad-worlds
    NOMAD_SERVER_ACCESS_KEY / NOMAD_SERVER_SECRET_KEY
"""

from __future__ import annotations

from pathlib import Path

from launcher.cloud import ConnectionResult, Lease, S3Client, UsageCounter, WorldStore
from launcher.storage import WorldStoreProtocol


class ServerWorldStore(WorldStoreProtocol):
    name = "server"

    def __init__(self, store: WorldStore) -> None:
        self._store = store

    @classmethod
    def build(cls, settings, usage: UsageCounter | None = None) -> "ServerWorldStore":
        from launcher.secrets import secret_value

        endpoint = settings.server_endpoint_url.strip()
        bucket = settings.server_bucket.strip()
        access = secret_value(settings.server_access_key)
        secret = secret_value(settings.server_secret_key)
        missing = [
            name
            for name, value in (
                ("NOMAD_SERVER_ENDPOINT", endpoint),
                ("NOMAD_SERVER_BUCKET", bucket),
                ("NOMAD_SERVER_ACCESS_KEY", access),
                ("NOMAD_SERVER_SECRET_KEY", secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"self-hosted server needs: {', '.join(missing)}")
        if not endpoint.startswith(("http://", "https://")):
            endpoint = "https://" + endpoint
        client = S3Client(endpoint, access, secret, bucket)
        return cls(
            WorldStore(client, player_name=settings.player_name, usage=usage, backend=cls.name)
        )

    # --- WorldStoreProtocol ---------------------------------------------

    def acquire(self, world_id: str, address: str | None = None) -> Lease:
        return self._store.acquire(world_id, address=address)

    def renew(self, world_id: str, lease: Lease) -> Lease:
        return self._store.renew(world_id, lease)

    def release(self, world_id: str, lease: Lease) -> None:
        self._store.release(world_id, lease)

    def status(self, world_id: str) -> dict:
        return self._store.status(world_id)

    def download_world(self, world_id: str, dest: Path) -> bool:
        return self._store.download_world(world_id, dest)

    def upload_world(self, world_id: str, archive: Path) -> None:
        self._store.upload_world(world_id, archive)

    def test_connection(self) -> ConnectionResult:
        return self._store.test_connection("storage server")
