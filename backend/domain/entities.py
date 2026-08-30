from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    worlds: Mapped[list["World"]] = relationship(
        back_populates="owner", foreign_keys="World.owner_id"
    )
    memberships: Mapped[list["WorldMember"]] = relationship(back_populates="user")


class World(Base):
    __tablename__ = "worlds"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    latest_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("world_versions.id"), nullable=True
    )
    current_host_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="sleeping")
    minecraft_version: Mapped[str] = mapped_column(String(32), nullable=False)
    server_software: Mapped[str] = mapped_column(String(32), nullable=False, default="vanilla")
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="git")
    storage_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    connection_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    owner: Mapped[User] = relationship(back_populates="worlds", foreign_keys=[owner_id])
    members: Mapped[list["WorldMember"]] = relationship(
        back_populates="world", cascade="all, delete-orphan"
    )
    versions: Mapped[list["WorldVersion"]] = relationship(
        back_populates="world",
        cascade="all, delete-orphan",
        foreign_keys="WorldVersion.world_id",
    )
    leases: Mapped[list["HostLease"]] = relationship(
        back_populates="world", cascade="all, delete-orphan"
    )
    latest_version: Mapped["WorldVersion | None"] = relationship(
        foreign_keys=[latest_version_id], remote_side="WorldVersion.id"
    )
    current_host: Mapped["User | None"] = relationship(foreign_keys=[current_host_id])


class WorldMember(Base):
    __tablename__ = "world_members"

    world_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    world: Mapped[World] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class HostLease(Base):
    __tablename__ = "host_leases"
    __table_args__ = (
        Index(
            "one_active_lease_per_world",
            "world_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    world_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("worlds.id"), nullable=False)
    host_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    world: Mapped[World] = relationship(back_populates="leases")
    host: Mapped[User] = relationship()


class WorldVersion(Base):
    __tablename__ = "world_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    world_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("worlds.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    minecraft_version: Mapped[str] = mapped_column(String(32), nullable=False)
    server_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    world: Mapped[World] = relationship(
        back_populates="versions", foreign_keys=[world_id]
    )
    creator: Mapped[User] = relationship()


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user: Mapped[User] = relationship()
