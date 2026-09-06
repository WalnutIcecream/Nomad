"""Cloudflare R2 backend — the default, free-tier-friendly direction.

Wraps the existing S3 client + ``WorldStore`` CAS lease protocol unchanged; it
satisfies the shared ``WorldStoreProtocol`` so nothing else in the app changes
when you pick this backend.

Charges: reads (Class B) free, 1M Class A /month free, storage 10 GB-month free,
egress $0. See the README "R2 path" section for the numbers.
"""

from __future__ import annotations

from pathlib import Path

from launcher.cloud import Lease, S3Client, UsageCounter, WorldStore
from launcher.storage import WorldStoreProtocol


class R2WorldStore(WorldStoreProtocol):
    name = "r2"

    def __init__(self, store: WorldStore) -> None:
        self._store = store

    @classmethod
    def build(cls, settings, usage: UsageCounter | None = None) -> "R2WorldStore":
        if not (
            settings.r2_account_id
            and settings.r2_access_key
            and settings.r2_secret_key
            and settings.r2_bucket
        ):
            raise ValueError("R2 backend requires account_id, access_key, secret_key and bucket")
        client = S3Client(
            settings.endpoint_url,
            settings.r2_access_key,
            settings.r2_secret_key,
            settings.r2_bucket,
        )
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