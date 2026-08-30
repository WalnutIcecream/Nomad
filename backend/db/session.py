"""Database engine and request-scoped session lifecycle.

This module is deliberately tiny and is the ONLY place in the application that
creates an engine or a session factory.

Why this design helps debugging:
    * The module-level ``engine`` and ``session_factory`` are explicit state
      that a debugger can inspect (and that tests replace when they point the
      app at ``nomad_test``).
    * ``init_db`` is idempotent — call it early (startup, tests) or let
      ``get_db`` lazily initialise on the first request.
    * ``pool_pre_ping`` revalidates dead cloud-database connections instead of
      surfacing stale pooled sockets.
    * ``expire_on_commit=False`` means a business service may keep reading an
      entity's attributes after a ``commit`` without a surprise reload.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import ControllerSettings


class Base(DeclarativeBase):
    """Root declarative base every ORM entity inherits from."""


def make_engine(database_url: str) -> Engine:
    """Build an engine for the given database URL (local or cloud Postgres)."""
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine):
    """Build a session factory bound to the engine.

    ``autoflush=False`` keeps control of when the session talks to the
    database; ``expire_on_commit=False`` keeps entities usable after commit.
    """
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Application-wide singleton state. Harmless to import; only populated once
# ``init_db`` is called (either explicitly at startup or lazily by ``get_db``).
engine: Engine | None = None
session_factory = None


def init_db(database_url: str | None = None) -> None:
    """Initialise the application-wide engine and session factory.

    Args:
        database_url: overrides the ``NOMAD_DATABASE_URL`` setting. Tests use
            this to point the controller at ``nomad_test``.
    """
    global engine, session_factory
    settings = ControllerSettings()
    engine = make_engine(database_url or settings.database_url)
    session_factory = make_session_factory(engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield one session per request, always close it."""
    if session_factory is None:
        init_db()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_engine() -> Engine:
    """Return the application engine, lazily initialising it if necessary."""
    if engine is None:
        init_db()
    return engine