"""Database access for users and login sessions (auth feature).

Repository layer rules:
    * The ONLY database queries for this feature live in this file.
    * Methods return entities (or ``None`` when nothing matched) and never
      raise HTTP errors — the service decides what ``None`` means.
    * No commit happens here; the service owns the transaction so it can
      group several operations atomically.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.entities import Session as SessionEntity
from backend.domain.entities import User


class UserRepository:
    def find_by_username(self, session: Session, username: str) -> User | None:
        """Return the user with the exact username, or None."""
        statement = select(User).where(User.username == username)
        return session.execute(statement).scalar_one_or_none()

    def find_by_id(self, session: Session, user_id: uuid.UUID) -> User | None:
        """Return the user with the given id, or None."""
        return session.get(User, user_id)

    def create(self, session: Session, username: str, password_hash: str) -> User:
        """Persist a new user (not committed yet) and return the entity."""
        new_user = User(username=username, password_hash=password_hash)
        session.add(new_user)
        return new_user


class SessionRepository:
    def create(
        self,
        session: Session,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> SessionEntity:
        """Persist a new login session (not committed yet) and return it."""
        new_entity = SessionEntity(
            user_id=user_id, token_hash=token_hash, expires_at=expires_at
        )
        session.add(new_entity)
        return new_entity

    def find_by_token_hash(self, session: Session, token_hash: str) -> SessionEntity | None:
        """Return the session whose hashed token matches, or None."""
        statement = select(SessionEntity).where(SessionEntity.token_hash == token_hash)
        return session.execute(statement).scalar_one_or_none()