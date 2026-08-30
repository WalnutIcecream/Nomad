"""Business logic for creating, listing and reading worlds.

Every method:
    * re-checks authorization instead of trusting the caller,
    * runs its database work through the repositories in
      ``backend.features.worlds.repository``,
    * returns the shared ``WorldOut`` contract from
      ``backend.domain.schemas``.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from backend.core.access import get_world_or_404, require_membership
from backend.domain.entities import User, World
from backend.domain.schemas import WorldCreate, WorldOut
from backend.features.auth.repository import UserRepository
from backend.features.worlds.repository import WorldMemberRepository, WorldRepository


class WorldService:
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

    def _host_name(self, world: World) -> str | None:
        """Username of the current host, or None when no one is hosting."""
        if world.current_host_id is None:
            return None
        host = self.user_repository.find_by_id(self.session, world.current_host_id)
        return host.username if host is not None else None

    def _to_world_out(
        self,
        world: World,
        member_count: int,
        current_host_name: str | None = None,
    ) -> WorldOut:
        """Build the API shape for a world from its entity."""
        latest_version_number = None
        if world.latest_version_id is not None and world.latest_version is not None:
            latest_version_number = world.latest_version.version_number
        return WorldOut(
            id=world.id,
            name=world.name,
            status=world.status,
            latest_version=latest_version_number,
            current_host=world.current_host_id,
            current_host_name=current_host_name,
            minecraft_version=world.minecraft_version,
            server_software=world.server_software,
            member_count=member_count,
            connection=world.connection_info,
        )

    def create(self, payload: WorldCreate, user: User) -> WorldOut:
        """Create a world owned by the user and return it."""
        new_world = self.world_repository.create(
            self.session,
            name=payload.name,
            owner_id=user.id,
            minecraft_version=payload.minecraft_version,
        )
        self.session.flush()  # populate new_world.id before linking the owner row
        self.member_repository.add(
            self.session,
            world_id=new_world.id,
            user_id=user.id,
            role="owner",
        )
        self.session.commit()
        self.session.refresh(new_world)
        return self._to_world_out(
            new_world, member_count=1, current_host_name=user.username
        )

    def list_for_user(self, user: User) -> list[WorldOut]:
        """Every world the user belongs to, as the API shape."""
        rows = self.world_repository.list_with_member_counts_for_user(
            self.session, user.id
        )
        result: list[WorldOut] = []
        for world, member_count in rows:
            host_name = self._host_name(world)
            result.append(
                self._to_world_out(
                    world, member_count=member_count, current_host_name=host_name
                )
            )
        return result

    def get_for_user(self, world_id: uuid.UUID, user: User) -> WorldOut:
        """Detail view of one world for an authorized member."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        member_count = self.world_repository.count_members(self.session, world_id)
        host_name = self._host_name(world)
        return self._to_world_out(
            world, member_count=member_count, current_host_name=host_name
        )

    def get_status_for_user(self, world_id: uuid.UUID, user: User) -> WorldOut:
        """Pollable status view — same content as the detail view, reused by
        the launcher's 5-second world-card polling."""
        return self.get_for_user(world_id, user)