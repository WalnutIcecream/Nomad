"""Database access for worlds and world membership (worlds feature).

The ``WorldMember`` rows belong to the same aggregate as ``World`` (a world
and its membership list), so both repositories live in this one file.

Repository layer rules:
    * No HTTP errors here — services turn ``None`` into the right status.
    * No commits here — the service owns the transaction.
    * ``lock_by_id`` is the concurrency seam: row lock ``FOR UPDATE`` is the
      mechanism behind atomic lease acquisition in the hosting feature.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.domain.entities import User, World, WorldMember


class WorldRepository:
    def find_by_id(self, session: Session, world_id: uuid.UUID) -> World | None:
        """Return the world with the given id, or None."""
        return session.get(World, world_id)

    def lock_by_id(self, session: Session, world_id: uuid.UUID) -> World | None:
        """Return the world row locked FOR UPDATE, or None.

        The lock serialises concurrent transactions on the same world — used
        so that two hosts can never both acquire the lease at once.
        """
        statement = select(World).where(World.id == world_id).with_for_update()
        return session.execute(statement).scalar_one_or_none()

    def create(
        self,
        session: Session,
        name: str,
        owner_id: uuid.UUID,
        minecraft_version: str,
    ) -> World:
        """Persist a new world in the default sleeping state (not committed)."""
        new_world = World(
            name=name,
            owner_id=owner_id,
            minecraft_version=minecraft_version,
            status="sleeping",
        )
        session.add(new_world)
        return new_world

    def list_with_member_counts_for_user(
        self, session: Session, user_id: uuid.UUID
    ) -> list[tuple[World, int]]:
        """All worlds the user belongs to, newest first, with member counts."""
        statement = (
            select(World, func.count(WorldMember.user_id))
            .join(WorldMember, WorldMember.world_id == World.id)
            .where(WorldMember.user_id == user_id)
            .group_by(World.id)
            .order_by(World.created_at.desc())
        )
        return list(session.execute(statement).all())

    def count_members(self, session: Session, world_id: uuid.UUID) -> int:
        """Number of members belonging to the world."""
        statement = select(func.count(WorldMember.user_id)).where(
            WorldMember.world_id == world_id
        )
        return session.execute(statement).scalar_one()


class WorldMemberRepository:
    def find_by_ids(
        self, session: Session, world_id: uuid.UUID, user_id: uuid.UUID
    ) -> WorldMember | None:
        """Return the membership row for a world+user pair, or None."""
        statement = select(WorldMember).where(
            WorldMember.world_id == world_id, WorldMember.user_id == user_id
        )
        return session.execute(statement).scalar_one_or_none()

    def add(
        self, session: Session, world_id: uuid.UUID, user_id: uuid.UUID, role: str
    ) -> WorldMember:
        """Persist a new membership row (not committed)."""
        new_membership = WorldMember(world_id=world_id, user_id=user_id, role=role)
        session.add(new_membership)
        return new_membership

    def remove(self, session: Session, membership: WorldMember) -> None:
        """Delete an existing membership row (committed by the service)."""
        session.delete(membership)

    def list_with_usernames(
        self, session: Session, world_id: uuid.UUID
    ) -> list[tuple[WorldMember, str]]:
        """All members of a world, paired with their usernames, ordered."""
        statement = (
            select(WorldMember, User.username)
            .join(User, User.id == WorldMember.user_id)
            .where(WorldMember.world_id == world_id)
            .order_by(WorldMember.role, User.username)
        )
        return list(session.execute(statement).all())