"""FastAPI dependency plumbing: how a request becomes the calling user.

``get_current_user`` is the single choke point through which every
authenticated request passes. It resolves the ``Authorization: Bearer <token>``
header to a ``User`` entity, and nothing else in the codebase has to worry
about tokens again — a route that declares
``user: User = Depends(get_current_user)`` receives a real user or the request
never reaches the handler.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.core.security import hash_session_token
from backend.db.session import get_db
from backend.domain.entities import User
from backend.features.auth.repository import SessionRepository, UserRepository

# ``auto_error=False`` lets the dependency produce its own 401 messages instead
# of FastAPI's generic one.
bearer_scheme = HTTPBearer(auto_error=False)


def _session_is_valid(session_expires_at: datetime) -> bool:
    """A session is valid while its expiry timestamp is still in the future."""
    return session_expires_at > datetime.now(timezone.utc)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_db),
) -> User:
    """Resolve the bearer token to a User, or raise 401."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing token"
        )

    token_hash = hash_session_token(credentials.credentials)
    session_repository = SessionRepository()
    session_record = session_repository.find_by_token_hash(session, token_hash)

    if session_record is None or not _session_is_valid(session_record.expires_at):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token"
        )

    user_repository = UserRepository()
    user = user_repository.find_by_id(session, session_record.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user"
        )
    return user