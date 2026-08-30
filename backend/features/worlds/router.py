"""HTTP endpoints for the worlds feature.

Thin router: parse, delegate to ``WorldService``, return. Authorization and
world-building all live in ``service.py``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.domain.entities import User
from backend.domain.schemas import WorldCreate, WorldOut
from backend.features.worlds.service import WorldService

router = APIRouter(prefix="/worlds", tags=["worlds"])


@router.get("", response_model=list[WorldOut])
def list_worlds(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[WorldOut]:
    """Every world the authenticated user belongs to."""
    service = WorldService(session)
    return service.list_for_user(user)


@router.post("", response_model=WorldOut, status_code=status.HTTP_201_CREATED)
def create_world(
    payload: WorldCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> WorldOut:
    """Create a new world owned by the authenticated user."""
    service = WorldService(session)
    return service.create(payload, user)


@router.get("/{world_id}", response_model=WorldOut)
def get_world(
    world_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> WorldOut:
    """Detail view of one world for a member."""
    service = WorldService(session)
    return service.get_for_user(world_id, user)


@router.get("/{world_id}/status", response_model=WorldOut)
def get_world_status(
    world_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> WorldOut:
    """Pollable status view used by the launcher UI."""
    service = WorldService(session)
    return service.get_status_for_user(world_id, user)