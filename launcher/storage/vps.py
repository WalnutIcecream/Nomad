"""VPS backend — a self-hosted, zero-cost-per-GB direction.

Reuses the exact S3 client + CAS lease protocol, pointed at your own
S3-compatible server on a personal VPS (MinIO, Garage, SeaweedFS). Storage and
bandwidth are whatever your VPS disk/plan gives you — no per-operation or
per-GB bill, full control.

Settings::

    NOMAD_STORAGE_BACKEND=vps
    NOMAD_VPS_ENDPOINT=https://s3.myvps.example
    NOMAD_VPS_BUCKET=nomad-worlds
    NOMAD_R2_ACCESS_KEY / NOMAD_R2_SECRET_KEY   (or NOMAD_VPS_ACCESS_KEY/...)
"""

from __future__ import annotations

from pathlib import Path

from launcher.cloud import Lease, S3Client, UsageCounter, WorldStore
from launcher.storage import WorldStoreProtocol


class VpsWorldStore(WorldStoreProtocol):
    name = "vps"

    def __init__(self, store: WorldStore) -> None:
        self._store = store

    @classmethod
    def build(cls, settings, usage: UsageCounter | None = None) -> "VpsWorldStore":
        endpoint = settings.vps_endpoint_url or settings.r2_endpoint_url
        bucket = settings.vps_bucket or settings.r2_bucket
        access = getattr(settings, "vps_access_key", None) or settings.r2_access_key
        secret = getattr(settings, "vps_secret_key", None) or settings.r2_secret_key
        if not (endpoint and bucket and access and secret):
            raise ValueError(
                "VPS backend requires NOMAD_VPS_ENDPOINT, NOMAD_VPS_BUCKET and access/secret keys"
            )
        client = S3Client(endpoint, access, secret, bucket)
        return cls(WorldStore(client, player_name=settings.player_name, usage=usage))

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