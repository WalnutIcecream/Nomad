"""Database access for world version metadata (versions feature).

Note what lives where:
    * the version ROW (metadata: number, creator, minecraft version, sha) is
      persisted here;
    * the version BYTES (the ``world.tar.gz`` archive) are persisted by
      ``BlobStore`` in ``backend/core/blobstore.py``.

The two-phase upload flow in ``VersionService`` keeps the row and the blob in
lock-step so an interrupted transfer never advances the ledger.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.entities import WorldVersion


class WorldVersionRepository:
    def list_for_world(
        self, session: Session, world_id: uuid.UUID
    ) -> list[WorldVersion]:
        """Every version of a world, newest first."""
        statement = (
            select(WorldVersion)
            .where(WorldVersion.world_id == world_id)
            .order_by(WorldVersion.version_number.desc())
        )
        return list(session.execute(statement).scalars().all())

    def find_by_world_and_number(
        self, session: Session, world_id: uuid.UUID, version_number: int
    ) -> WorldVersion | None:
        """A specific version row of a world, or None."""
        statement = select(WorldVersion).where(
            WorldVersion.world_id == world_id,
            WorldVersion.version_number == version_number,
        )
        return session.execute(statement).scalar_one_or_none()

    def create(
        self,
        session: Session,
        world_id: uuid.UUID,
        version_number: int,
        storage_key: str,
        created_by: uuid.UUID,
        minecraft_version: str,
        server_version: str | None,
    ) -> WorldVersion:
        """Persist a new version row (not committed; caller flushes/commits)."""
        new_version = WorldVersion(
            world_id=world_id,
            version_number=version_number,
            storage_key=storage_key,
            created_by=created_by,
            minecraft_version=minecraft_version,
            server_version=server_version,
        )
        session.add(new_version)
        return new_version