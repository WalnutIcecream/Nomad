"""HTTP endpoints for the auth feature.

This router is deliberately thin: it parses the request, constructs an
``AuthService`` bound to the request session, and returns whatever the service
produces. All rules live in ``service.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.config import ControllerSettings
from backend.db.session import get_db
from backend.domain.schemas import LoginRequest, LoginResponse, UserOut
from backend.features.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _settings() -> ControllerSettings:
    """Per-request settings source (reads env / .env each call)."""
    return ControllerSettings()


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    payload: LoginRequest,
    session: Session = Depends(get_db),
) -> UserOut:
    """Create a new account and return its public profile."""
    service = AuthService(session, _settings())
    created_user = service.register(payload.username, payload.password)
    return UserOut.model_validate(created_user)


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    session: Session = Depends(get_db),
) -> LoginResponse:
    """Exchange valid credentials for a bearer session token."""
    service = AuthService(session, _settings())
    return service.login(payload.username, payload.password)