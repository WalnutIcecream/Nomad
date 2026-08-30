"""Business logic for managing who belongs to a world.

Membership rows are part of the same aggregate as the world, so this feature
reuses ``WorldMemberRepository`` (in ``features/worlds/repository.py``) rather
than duplicating SQL — membership is one concept with one home.

Rules enforced here:
    * list  -> any member of the world may see the roster
    * add   -> owners only; the target user must exist; no duplicates
    * remove-> owners only; the owner may never remove themselves
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.core.access import get_world_or_404, require_membership, require_owner
from backend.domain.entities import User
from backend.domain.schemas import MemberAddRequest, MemberOut
from backend.features.auth.repository import UserRepository
from backend.features.worlds.repository import WorldMemberRepository, WorldRepository


class MemberService:
    def __init__(
        self,
        session: Session,
        world_repository: WorldRepository | None = None,
        member_repository: WorldMemberRepository | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        """Wire the service to the request session and its repositories."""
        self.session = session
        self.world_repository = world_repository if world_repository is not None else WorldRepository()
        self.member_repository = member_repository if member_repository is not None else WorldMemberRepository()
        self.user_repository = user_repository if user_repository is not None else UserRepository()

    def list_members(self, world_id: uuid.UUID, user: User) -> list[MemberOut]:
        """Every member of a world (membership required)."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)

        rows = self.member_repository.list_with_usernames(self.session, world_id)
        result: list[MemberOut] = []
        for membership, username in rows:
            result.append(
                MemberOut(
                    user_id=membership.user_id,
                    username=username,
                    role=membership.role,
                )
            )
        return result

    def add_member(
        self, world_id: uuid.UUID, user: User, payload: MemberAddRequest
    ) -> MemberOut:
        """Invite a user into a world (owner only)."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        require_owner(world, user)

        target_user = self.user_repository.find_by_id(self.session, payload.user_id)
        if target_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="user not found"
            )

        existing_membership = self.member_repository.find_by_ids(
            self.session, world_id, target_user.id
        )
        if existing_membership is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="already a member"
            )

        self.member_repository.add(
            self.session,
            world_id=world_id,
            user_id=target_user.id,
            role="member",
        )
        self.session.commit()
        return MemberOut(
            user_id=target_user.id, username=target_user.username, role="member"
        )

    def remove_member(
        self, world_id: uuid.UUID, user: User, member_user_id: uuid.UUID
    ) -> None:
        """Remove a member from a world (owner only)."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        require_owner(world, user)

        if member_user_id == user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="owner cannot remove self",
            )

        membership = self.member_repository.find_by_ids(
            self.session, world_id, member_user_id
        )
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="member not found"
            )

        self.member_repository.remove(self.session, membership)
        self.session.commit()