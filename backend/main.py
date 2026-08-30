"""The controller's composition root.

This module is deliberately the only place that knows the cast of routers.
Every feature exposes an ``APIRouter`` and is registered here, so searching
for "where is this endpoint wired" starts and ends at ``create_app``.

The database is initialised lazily on the first request (see
``backend.db.session.get_db``) unless a test fixture already pointed it at a
test database.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.db import session as database


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialise the engine/session factory unless already configured.

    Tests call ``init_db`` in their fixtures first to redirect the controller
    to ``nomad_test``; the ``if`` guard stops startup from clobbering that.
    """
    if database.session_factory is None:
        database.init_db()
    yield


def create_app() -> FastAPI:
    """Build the application and mount every feature router."""
    app = FastAPI(title="Nomad Controller", version="0.1.0", lifespan=_lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Readiness probe used by the bundled hub and by ops."""
        return {"status": "ok"}

    from backend.features.auth.router import router as auth_router
    from backend.features.connection.router import router as connection_router
    from backend.features.hosting.router import router as hosting_router
    from backend.features.members.router import router as members_router
    from backend.features.versions.router import router as versions_router
    from backend.features.worlds.router import router as worlds_router

    app.include_router(auth_router)
    app.include_router(worlds_router)
    app.include_router(members_router)
    app.include_router(hosting_router)
    app.include_router(versions_router)
    app.include_router(connection_router)
    return app


app = create_app()