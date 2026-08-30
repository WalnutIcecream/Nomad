"""HTTP endpoints for the connection feature (reachability info).

Thin router: parse, delegate to ``ConnectionService``, return.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.config import ControllerSettings
from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.domain.entities import User
from backend.domain.schemas import (
    ConnectionInfoUpdate,
    RelayTokenResponse,
    WorldConnectionOut,
)
from backend.features.connection.service import ConnectionService

router = APIRouter(prefix="/worlds/{world_id}/connection", tags=["connection"])


def _settings() -> ControllerSettings:
    """Per-request settings source (reads env / .env each call)."""
    return ControllerSettings()


@router.get("", response_model=WorldConnectionOut)
def get_connection(
    world_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> WorldConnectionOut:
    """Current connection info (any member may read it)."""
    service = ConnectionService(session, _settings())
    return service.get_connection(world_id, user)


@router.put("", response_model=WorldConnectionOut)
def update_connection(
    world_id: uuid.UUID,
    payload: ConnectionInfoUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> WorldConnectionOut:
    """The active host reports how players reach its server."""
    service = ConnectionService(session, _settings())
    return service.update_connection(world_id, user, payload)


@router.post("/relay-token", response_model=RelayTokenResponse)
def get_relay_token(
    world_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> RelayTokenResponse:
    """The active host requests a fresh relay token (NAT-friendly fallback)."""
    service = ConnectionService(session, _settings())
    return service.get_relay_token(world_id, user)