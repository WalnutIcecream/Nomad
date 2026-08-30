"""Business logic for registering users and logging them in.

Owns the decisions behind the ``/auth/*`` endpoints:
    * usernames are unique — a duplicate registration is a 409
    * login checks the Argon2id hash, never the plaintext
    * a successful login mints a fresh session token (client keeps the raw
      token; the controller stores only its hash)

Transactions: every method ends with exactly one commit so the endpoint is
stateless on success. Nothing here touches SQL directly — all persistence goes
through the repositories.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.config import ControllerSettings
from backend.core.security import hash_password, hash_session_token, new_session_token, verify_password
from backend.domain.entities import User
from backend.domain.schemas import LoginResponse, UserOut
from backend.features.auth.repository import SessionRepository, UserRepository


class AuthService:
    def __init__(
        self,
        session: Session,
        settings: ControllerSettings,
        user_repository: UserRepository | None = None,
        session_repository: SessionRepository | None = None,
    ) -> None:
        """Wire the service to the request session and its repositories.

        Repositories default to the real ones so callers (routers) stay
        terse; tests can inject doubles by passing them explicitly.
        """
        self.session = session
        self.settings = settings
        self.user_repository = user_repository if user_repository is not None else UserRepository()
        self.session_repository = (
            session_repository if session_repository is not None else SessionRepository()
        )

    def register(self, username: str, password: str) -> User:
        """Create a user account, or raise 409 if the username is taken."""
        existing_user = self.user_repository.find_by_username(self.session, username)
        if existing_user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="username taken"
            )

        password_hash = hash_password(password)
        new_user = self.user_repository.create(self.session, username, password_hash)
        self.session.commit()
        self.session.refresh(new_user)
        return new_user

    def login(self, username: str, password: str) -> LoginResponse:
        """Verify credentials, mint a session token and return it."""
        user = self.user_repository.find_by_username(self.session, username)
        if user is None or not verify_password(password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
            )

        raw_token = new_session_token()
        token_hash = hash_session_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self.settings.session_ttl_seconds
        )
        self.session_repository.create(
            self.session, user.id, token_hash, expires_at
        )
        self.session.commit()

        return LoginResponse(token=raw_token, user=UserOut.model_validate(user))