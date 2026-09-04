from __future__ import annotations

import hashlib
import json

from launcher.cloud import (
    CloudError,
    LeaseError,
    S3Client,
    WorldStore,
    lease_key,
    world_key,
)


class FakeS3Client(S3Client):
    """In-memory S3 double with real conditional-write semantics (ETag CAS).

    Models exactly what R2 provides for the operations we use:
    * GET returns 404 unless the object exists.
    * PUT with ``If-None-Match: *`` fails (412) when the object exists.
    * PUT with ``If-Match: <etag>`` fails (412) when the current etag differs.
    * Every successful PUT returns a new ETag.
    """

    def __init__(self, player_name: str = "alice") -> None:
        # Deliberately skip S3Client.__init__ (no network state needed).
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.player_name = player_name

    # --- internal helpers ----------------------------------------------

    def _etag(self, body: bytes) -> str:
        return '"' + hashlib.md5(body).hexdigest() + '"'

    # --- S3Client API used by WorldStore -------------------------------

    def get_object(self, key: str) -> bytes:
        if key not in self.objects:
            raise CloudError(404, "not found")
        return self.objects[key][0]

    def get_object_if_exists(self, key: str) -> bytes | None:
        if key not in self.objects:
            return None
        return self.objects[key][0]

    def get_object_etag(self, key: str) -> tuple[bytes, str | None]:
        if key not in self.objects:
            raise CloudError(404, "not found")
        body, etag = self.objects[key]
        return body, etag

    def put_object(
        self,
        key: str,
        body: bytes,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
        content_type: str = "application/octet-stream",
    ) -> str:
        if if_none_match and key in self.objects:
            raise LeaseError(412, "conditional write failed (lost the race)")
        if if_match is not None and key in self.objects:
            _, current_etag = self.objects[key]
            if if_match != current_etag:
                raise LeaseError(412, "conditional write failed (lost the race)")
        etag = self._etag(body)
        self.objects[key] = (body, etag)
        return etag

    def status_objects(self) -> dict[str, bytes]:
        return {k: v[0] for k, v in self.objects.items()}


def make_store(player: str = "alice") -> WorldStore:
    return WorldStore(FakeS3Client(), player_name=player)


def lease_body(store: WorldStore, world_id: str) -> dict:
    raw = store.client.objects[lease_key(world_id)][0]
    return json.loads(raw.decode("utf-8"))
