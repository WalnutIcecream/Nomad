"""Storage backend protocol and registry for Nomad worlds.

A *backend* is anything that can answer the world-lease primitive and hold a
world blob. There are two built-in directions, and they are the only two the
product offers:

* ``r2``     -> Cloudflare R2 (``launcher.storage.r2``)
* ``server`` -> your own S3-compatible server, e.g. Garage on a VPS or home box
  (``launcher.storage.server``)

The launcher, agent and CLI talk only to the ``WorldStoreProtocol``; the
backend is selected once (in the storage wizard or ``nomad storage set``) and
its settings live in ``LauncherSettings``. Adding a third direction means
implementing the protocol and adding one entry to ``build_store`` — nothing
else in the app changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from launcher.cloud import ConnectionResult, Lease, UsageCounter

# User-facing provider metadata. "server" is deliberately presented as a person
# would say it ("My own server"), never as "Generic S3".
PROVIDERS: dict[str, str] = {
    "r2": "Cloudflare R2",
    "server": "My own server",
}


def provider_label(backend: str) -> str:
    """Human name for a backend id, for messages and UI labels."""
    return PROVIDERS.get(backend, backend)


@runtime_checkable
class WorldStoreProtocol(Protocol):
    """The storage contract every backend (r2, server) must satisfy."""

    name: str

    def acquire(self, world_id: str, address: str | None = None) -> Lease:
        """Become host for a world, or raise LeaseError if already hosted."""
        ...

    def renew(self, world_id: str, lease: Lease) -> Lease:
        """Extend an active lease (must still hold its etag/cursor)."""
        ...

    def release(self, world_id: str, lease: Lease) -> None:
        """Mark the lease released so the next host can acquire."""
        ...

    def status(self, world_id: str) -> dict:
        """Public view: hosted/holder/address/expires_at."""
        ...

    def download_world(self, world_id: str, dest: Path) -> bool:
        """Fetch the world blob into ``dest``. False if none exists yet."""
        ...

    def upload_world(self, world_id: str, archive: Path) -> None:
        """Store the world blob (single per-world object, last write wins)."""
        ...

    def test_connection(self) -> ConnectionResult:
        """Verify the storage is reachable and writable before saving it."""
        ...


def build_store(settings, usage: UsageCounter | None = None) -> WorldStoreProtocol:
    """Construct the configured storage backend from launcher settings."""
    backend = settings.storage_backend
    if backend == "r2":
        from launcher.storage.r2 import R2WorldStore

        return R2WorldStore.build(settings, usage=usage)
    if backend == "server":
        from launcher.storage.server import ServerWorldStore

        return ServerWorldStore.build(settings, usage=usage)
    raise ValueError(
        f"unknown storage backend: {backend!r} (expected one of: {', '.join(PROVIDERS)})"
    )


__all__ = [
    "WorldStoreProtocol",
    "build_store",
    "provider_label",
    "PROVIDERS",
    "ConnectionResult",
    "Lease",
    "UsageCounter",
]
