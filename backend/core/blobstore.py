"""Snapshot archive persistence for the controller.

The controller is authoritative for the *metadata* (versions, ownership,
leases) which lives in PostgreSQL; this store is authoritative for the *bytes*
(the ``world.tar.gz`` archives themselves).

Intentional abstraction, kept tiny: the interface is a handful of paths and
moves. A future S3/R2/blob adapter can replace this class without any feature
code knowing — the upload/download/version flow only ever calls the methods
below.

Layout::

    <root>/
      <world_id>/
        staging/<upload_id>.tar.gz   # in-progress two-phase upload
        v1.tar.gz                    # committed, immutable version
        v2.tar.gz
        ...
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _world_directory(self, world_id: UUID) -> Path:
        """Directory holding every blob belonging to one world."""
        return self.root / str(world_id)

    def staging_path(self, world_id: UUID, upload_id: UUID) -> Path:
        """Where an in-progress upload is buffered before it is committed."""
        return self._world_directory(world_id) / "staging" / f"{upload_id}.tar.gz"

    def version_path(self, world_id: UUID, version: int) -> Path:
        """Canonical path of a committed snapshot version."""
        return self._world_directory(world_id) / f"v{version}.tar.gz"

    def has_staged(self, world_id: UUID, upload_id: UUID) -> bool:
        """True if the upload bytes already arrived and are waiting to commit."""
        return self.staging_path(world_id, upload_id).exists()

    def commit(self, world_id: UUID, upload_id: UUID, version: int) -> Path:
        """Finalise an upload: move the staged file into the versioned store."""
        staged = self.staging_path(world_id, upload_id)
        if not staged.exists():
            raise FileNotFoundError(f"no staged upload for {upload_id}")
        target = self.version_path(world_id, version)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), target)
        return target

    def abort(self, world_id: UUID, upload_id: UUID) -> None:
        """Discard a staged upload (e.g. an empty/no-op transfer)."""
        staged = self.staging_path(world_id, upload_id)
        if staged.exists():
            staged.unlink()

    def get_version_path(self, world_id: UUID, version: int) -> Path | None:
        """Path of a committed version, or None if the blob is missing."""
        path = self.version_path(world_id, version)
        return path if path.exists() else None