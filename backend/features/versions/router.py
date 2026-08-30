"""HTTP endpoints for the versions feature (version ledger + snapshots).

Thin router: parse, delegate to ``VersionService``, return. All safety rules
(lease validation, base-version conflicts, two-phase upload) live in
``service.py``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.config import ControllerSettings
from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.domain.entities import User
from backend.domain.schemas import (
    RestoreRequest,
    RestoreResponse,
    SnapshotCompleteRequest,
    SnapshotCompleteResponse,
    SnapshotPrepareRequest,
    SnapshotPrepareResponse,
    VersionOut,
)
from backend.features.versions.service import VersionService

router = APIRouter(prefix="/worlds/{world_id}", tags=["snapshots"])


def _settings() -> ControllerSettings:
    """Per-request settings source (reads env / .env each call)."""
    return ControllerSettings()


@router.get("/versions", response_model=list[VersionOut])
def list_versions(
    world_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[VersionOut]:
    """Every snapshot version of a world, newest first (members only)."""
    service = VersionService(session, _settings())
    return service.list_versions(world_id, user)


@router.post("/snapshot/prepare", response_model=SnapshotPrepareResponse)
def snapshot_prepare(
    world_id: uuid.UUID,
    payload: SnapshotPrepareRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> SnapshotPrepareResponse:
    """Open a two-phase upload slot (validates the host's position)."""
    service = VersionService(session, _settings())
    return service.prepare_snapshot(world_id, user, payload)


@router.put("/snapshot/{upload_id}")
async def upload_snapshot(
    world_id: uuid.UUID,
    upload_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    """Stream the snapshot archive into the staging slot."""
    service = VersionService(session, _settings())
    return await service.upload_snapshot(
        world_id, upload_id, user, chunk_stream=request.stream()
    )


@router.post("/snapshot/complete", response_model=SnapshotCompleteResponse)
def snapshot_complete(
    world_id: uuid.UUID,
    payload: SnapshotCompleteRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> SnapshotCompleteResponse:
    """Commit the staged blob and advance the version ledger."""
    service = VersionService(session, _settings())
    return service.complete_snapshot(world_id, user, payload)


@router.post("/restore", response_model=RestoreResponse)
def restore_version(
    world_id: uuid.UUID,
    payload: RestoreRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> RestoreResponse:
    """Return a download location for a specific version (members only)."""
    service = VersionService(session, _settings())
    return service.restore_version(world_id, user, payload)


@router.get("/versions/{version_number}/download")
def download_version(
    world_id: uuid.UUID,
    version_number: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> FileResponse:
    """Stream a version's archive to an authorized member."""
    service = VersionService(session, _settings())
    return service.download_version(world_id, version_number, user)