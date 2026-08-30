"""Business logic for the version ledger and the two-phase snapshot upload.

Safety rules preserved verbatim from the original design:

    * A snapshot may only be prepared by the user who holds an ACTIVE lease.
    * The uploader's ``base_version`` must equal the cloud latest version —
      a stale host is rejected with 409 instead of silently overwriting
      newer work.
    * Upload is two-phase: ``prepare`` validates + flips the world to SYNCING;
      the PUT streams bytes to a *staging* file; ``complete`` re-validates the
      lease, then moves the staged blob into the versioned store and advances
      the ledger. An interrupted transfer therefore never leaves the cloud in
      a half-updated state.
    * Restores/downloads require membership and a real version + blob, both
      404 otherwise.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.config import ControllerSettings
from backend.core.access import get_world_or_404, require_membership
from backend.core.blobstore import BlobStore
from backend.domain.entities import HostLease, User, WorldVersion
from backend.domain.schemas import (
    RestoreRequest,
    RestoreResponse,
    SnapshotCompleteRequest,
    SnapshotCompleteResponse,
    SnapshotPrepareRequest,
    SnapshotPrepareResponse,
    VersionOut,
)
from backend.features.hosting.repository import HostLeaseRepository
from backend.features.hosting.service import is_lease_active
from backend.features.versions.repository import WorldVersionRepository
from backend.features.worlds.repository import WorldRepository


class VersionService:
    def __init__(
        self,
        session: Session,
        settings: ControllerSettings,
        world_repository: WorldRepository | None = None,
        lease_repository: HostLeaseRepository | None = None,
        version_repository: WorldVersionRepository | None = None,
        blob_store: BlobStore | None = None,
    ) -> None:
        """Wire the service to the request session, settings and storage."""
        self.session = session
        self.settings = settings
        self.world_repository = world_repository if world_repository is not None else WorldRepository()
        self.lease_repository = lease_repository if lease_repository is not None else HostLeaseRepository()
        self.version_repository = version_repository if version_repository is not None else WorldVersionRepository()
        self.blob_store = (
            blob_store if blob_store is not None else BlobStore(Path(settings.blob_dir))
        )

    def _require_active_lease_for_user(
        self, world_id: uuid.UUID, lease_id: uuid.UUID, user: User
    ) -> HostLease:
        """Load the caller's lease and insist it is alive (409 otherwise).

        ``find_by_world_lease_and_host`` already restricts the row to this
        user's own leases, so a foreign/unknown lease simply comes back None.
        """
        lease = self.lease_repository.find_by_world_lease_and_host(
            self.session, world_id, lease_id, user.id
        )
        if lease is None or not is_lease_active(lease, datetime.now(timezone.utc)):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="lease is not active"
            )
        return lease

    @staticmethod
    def _to_version_out(version: WorldVersion) -> VersionOut:
        """Build the API shape for a version row."""
        return VersionOut(
            version_number=version.version_number,
            created_by=version.created_by,
            minecraft_version=version.minecraft_version,
            server_version=version.server_version,
            created_at=version.created_at,
        )

    def list_versions(self, world_id: uuid.UUID, user: User) -> list[VersionOut]:
        """All snapshot versions of a world, newest first (members only)."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)

        versions = self.version_repository.list_for_world(self.session, world_id)
        return [self._to_version_out(version) for version in versions]

    def prepare_snapshot(
        self, world_id: uuid.UUID, user: User, payload: SnapshotPrepareRequest
    ) -> SnapshotPrepareResponse:
        """Validate the uploader's position and open a staging upload slot."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        self._require_active_lease_for_user(world_id, payload.lease_id, user)

        cloud_latest = world.latest_version
        if cloud_latest is not None and cloud_latest.version_number != payload.base_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"version conflict: base is v{payload.base_version}, "
                    f"cloud is v{cloud_latest.version_number}"
                ),
            )

        # The world is actively syncing until complete (or a crash/timeout).
        world.status = "syncing"
        self.session.commit()

        upload_id = uuid.uuid4()
        return SnapshotPrepareResponse(
            upload_id=upload_id,
            upload_url=f"/worlds/{world_id}/snapshot/{upload_id}",
        )

    async def upload_snapshot(
        self,
        world_id: uuid.UUID,
        upload_id: uuid.UUID,
        user: User,
        chunk_stream: AsyncIterator[bytes],
    ) -> dict:
        """Stream the archive body into the staging file for this upload.

        The lease was validated at prepare; ``complete`` re-validates it, so a
        host whose lease dies mid-transfer still cannot commit anything.
        """
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)

        staged_path = self.blob_store.staging_path(world_id, upload_id)
        staged_path.parent.mkdir(parents=True, exist_ok=True)

        total_bytes = 0
        with staged_path.open("wb") as staging_file:
            async for chunk in chunk_stream:
                staging_file.write(chunk)
                total_bytes += len(chunk)

        if total_bytes == 0:
            self.blob_store.abort(world_id, upload_id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty upload"
            )

        return {"upload_id": str(upload_id), "size_bytes": total_bytes}

    def complete_snapshot(
        self, world_id: uuid.UUID, user: User, payload: SnapshotCompleteRequest
    ) -> SnapshotCompleteResponse:
        """Move the staged blob into the versioned store and advance the ledger.

        This is the single point where a snapshot becomes the new cloud latest;
        before this line runs, an interrupted transfer changed nothing.
        """
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        lease = self._require_active_lease_for_user(world_id, payload.lease_id, user)

        if not self.blob_store.has_staged(world_id, payload.upload_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="no staged upload",
            )

        cloud_latest = world.latest_version
        next_version_number = (cloud_latest.version_number + 1) if cloud_latest is not None else 1

        new_version = self.version_repository.create(
            self.session,
            world_id=world_id,
            version_number=next_version_number,
            storage_key=f"v{next_version_number}",
            created_by=user.id,
            minecraft_version=payload.minecraft_version,
            server_version=payload.server_version,
        )
        # Flush so the new version has an id before we reference it below.
        self.session.flush()
        self.blob_store.commit(world_id, payload.upload_id, next_version_number)

        world.latest_version_id = new_version.id
        world.status = "sleeping"
        world.current_host_id = None
        lease.status = "released"
        lease.released_at = datetime.now(timezone.utc)
        self.session.commit()

        return SnapshotCompleteResponse(version_number=next_version_number)

    def restore_version(
        self, world_id: uuid.UUID, user: User, payload: RestoreRequest
    ) -> RestoreResponse:
        """Return a download location for a specific version (members only)."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)

        version = self.version_repository.find_by_world_and_number(
            self.session, world_id, payload.version_number
        )
        if version is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="version not found"
            )

        return RestoreResponse(
            download_url=(
                f"/worlds/{world_id}/versions/{version.version_number}/download"
            )
        )

    def download_version(
        self, world_id: uuid.UUID, version_number: int, user: User
    ) -> FileResponse:
        """Stream a version's archive to an authorized member."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)

        version = self.version_repository.find_by_world_and_number(
            self.session, world_id, version_number
        )
        if version is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="version not found"
            )

        archive_path = self.blob_store.get_version_path(world_id, version_number)
        if archive_path is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="blob not found"
            )

        return FileResponse(
            archive_path,
            media_type="application/gzip",
            filename=f"world-v{version_number}.tar.gz",
        )