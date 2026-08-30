"""Access-control helpers shared by every feature service.

These are plain functions (not FastAPI dependencies) so that business services
can call them directly. They keep the authorization rules in exactly one place:

    * a world must exist before anything else is decided  -> 404
    * the user must be a member of the world               -> 403
    * owners-only actions check ownership                  -> 403
    * host-only actions check the caller is current host   -> 403

Every helper raises an ``HTTPException`` immediately when the rule fails, so a
debugger can see straight from the stack trace which guard rejected a request.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.domain.entities import User, World, WorldMember
from backend.features.worlds.repository import WorldRepository, WorldMemberRepository


def get_world_or_404(session: Session, world_id: uuid.UUID) -> World:
    """Load a world by id, or raise 404 because it does not exist."""
    world_repository = WorldRepository()
    world = world_repository.find_by_id(session, world_id)
    if world is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="world not found")
    return world


def require_membership(session: Session, world: World, user: User) -> WorldMember:
    """Return the user's membership row for a world, or raise 403."""
    member_repository = WorldMemberRepository()
    membership = member_repository.find_by_ids(session, world.id, user.id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member of this world"
        )
    return membership


def require_owner(world: World, user: User) -> None:
    """Raise 403 unless the given user is the world owner."""
    if world.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="owner only")


def require_current_host(world: World, user: User) -> None:
    """Raise 403 unless the given user is the world's active host."""
    if world.current_host_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the current host may perform this action",
        )