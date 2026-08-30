"""HTTP endpoints for the members feature.

Thin router: parse, delegate to ``MemberService``, return. Membership checks
and owner-only guards live in ``service.py`` (via ``core.access``).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.domain.entities import User
from backend.domain.schemas import MemberAddRequest, MemberOut
from backend.features.members.service import MemberService

router = APIRouter(prefix="/worlds/{world_id}/members", tags=["members"])


@router.get("", response_model=list[MemberOut])
def list_members(
    world_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[MemberOut]:
    """Roster of a world (any member may read it)."""
    service = MemberService(session)
    return service.list_members(world_id, user)


@router.post("", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    world_id: uuid.UUID,
    payload: MemberAddRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> MemberOut:
    """Add a user to the world (owner only)."""
    service = MemberService(session)
    return service.add_member(world_id, user, payload)


@router.delete("/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    world_id: uuid.UUID,
    member_user_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    """Remove a member from the world (owner only)."""
    service = MemberService(session)
    service.remove_member(world_id, user, member_user_id)