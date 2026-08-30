from __future__ import annotations

import os
import tempfile

TEST_DB_URL = "postgresql+psycopg://nomad:nomad_dev@localhost:5432/nomad_test"


def _ensure_test_db() -> None:
    from sqlalchemy import create_engine, text

    admin = create_engine(
        "postgresql+psycopg://nomad:nomad_dev@localhost:5432/nomad",
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'nomad_test'")
        ).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE nomad_test"))


def run_test_server() -> None:
    """Module-level target for the sync-test uvicorn process.

    On POSIX the child may inherit the parent's SQLAlchemy engine via fork;
    on Windows it starts fresh. Either way we dispose any inherited engine and
    create a new one so the server never shares a connection pool with the
    pytest process.
    """
    import uvicorn

    import backend.db.session as database
    from backend.main import app

    _ensure_test_db()

    # Drop any inherited engine/session factory, then point at the test DB.
    if database.engine is not None:
        database.engine.dispose()
    database.engine = None
    database.session_factory = None
    database.init_db(TEST_DB_URL)

    # OS-neutral scratch dir for test blobs.
    os.environ.setdefault("NOMAD_BLOB_DIR", os.path.join(tempfile.gettempdir(), "nomad_test_blobs"))
    uvicorn.run(app, host="127.0.0.1", port=8971, log_level="error")
