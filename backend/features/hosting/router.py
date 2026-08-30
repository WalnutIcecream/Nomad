"""HTTP endpoints for the hosting feature (lease lifecycle).

Thin router: parse, delegate to ``HostingService``, return. The atomic
acquire/heartbeat/release rules live in ``service.py``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.config import ControllerSettings
from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.domain.entities import User
from backend.domain.schemas import AcquireResponse, HeartbeatRequest, ReleaseRequest
from backend.features.hosting.service import HostingService

router = APIRouter(prefix="/worlds/{world_id}/host", tags=["host"])


def _settings() -> ControllerSettings:
    """Per-request settings source (reads env / .env each call)."""
    return ControllerSettings()


@router.post("/acquire", response_model=AcquireResponse)
def acquire_host(
    world_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> AcquireResponse:
    """Atomically become the host, or learn who already holds the lease."""
    service = HostingService(session, _settings())
    return service.acquire(world_id, user)


@router.post("/heartbeat", response_model=AcquireResponse)
def heartbeat(
    world_id: uuid.UUID,
    payload: HeartbeatRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> AcquireResponse:
    """Extend the caller's lease to prove the host is still alive."""
    service = HostingService(session, _settings())
    return service.heartbeat(world_id, user, payload)


@router.post("/release", status_code=status.HTTP_204_NO_CONTENT)
def release_host(
    world_id: uuid.UUID,
    payload: ReleaseRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    """Gracefully stop hosting and put the world back to SLEEPING."""
    service = HostingService(session, _settings())
    service.release(world_id, user, payload)