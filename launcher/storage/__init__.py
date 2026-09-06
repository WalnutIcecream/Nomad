"""Storage backend protocol and registry for Nomad worlds.

A *backend* is anything that can answer the world-lease primitive and hold a
world blob. There are three built-in directions:

* ``r2``   -> Cloudflare R2 / any S3 endpoint (``launcher.storage.r2``)
* ``git``  -> a git repo (mundane, free, unlimited storage) (``launcher.storage.git``)
* ``vps``  -> your own server over S3/MinIO (``launcher.storage.vps``)

The launcher, agent and CLI talk only to the ``WorldStoreProtocol``; the
backend is selected once via ``nomad storage set`` and its settings live in
``LauncherSettings``. Adding a fourth direction means implementing the protocol
and adding one entry to ``build_store`` — the rest of the app never changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from launcher.cloud import Lease, UsageCounter


@runtime_checkable
class WorldStoreProtocol(Protocol):
    """The storage contract every backend (r2, git, vps) must satisfy."""

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


def build_store(settings, usage: UsageCounter | None = None) -> WorldStoreProtocol:
    """Construct the configured storage backend from launcher settings."""
    backend = settings.storage_backend
    if backend == "r2":
        from launcher.storage.r2 import R2WorldStore

        return R2WorldStore.build(settings, usage=usage)
    if backend == "git":
        from launcher.storage.git import GitWorldStore

        return GitWorldStore.build(settings, usage=usage)
    if backend == "vps":
        from launcher.storage.vps import VpsWorldStore

        return VpsWorldStore.build(settings, usage=usage)
    raise ValueError(f"unknown storage backend: {backend!r}")


__all__ = ["WorldStoreProtocol", "build_store", "Lease", "UsageCounter"]