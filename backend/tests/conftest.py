from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.db.session import Base, init_db
from backend.main import app

TEST_DB_URL = "postgresql+psycopg://nomad:nomad_dev@localhost:5432/nomad_test"


@pytest.fixture(scope="session")
def engine():
    """Create the test database once, then yield an engine bound to it."""
    admin = create_engine(
        "postgresql+psycopg://nomad:nomad_dev@localhost:5432/nomad",
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as conn:
        # Terminate any lingering sessions so the drop can proceed.
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = 'nomad_test'"
            )
        )
        conn.execute(text("DROP DATABASE IF EXISTS nomad_test"))
        conn.execute(text("CREATE DATABASE nomad_test"))
    test_engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture()
def client(engine) -> Generator[TestClient, None, None]:
    init_db(TEST_DB_URL)
    # Kill any connections held by the module-scoped uvicorn worker so the
    # TRUNCATE below can acquire its locks instead of blocking forever.
    with engine.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = 'nomad_test' AND pid <> pg_backend_pid()"
            )
        )
        conn.commit()
        conn.execute(
            text(
                "TRUNCATE users, worlds, world_members, host_leases, "
                "world_versions, sessions RESTART IDENTITY CASCADE"
            )
        )
        conn.commit()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def controller_url(engine) -> Generator[str, None, None]:
    """Boot a real uvicorn server against the test DB for sync tests.

    Uses fork on POSIX (faster, avoids re-import cost) and spawn on Windows
    (fork does not exist there). The worker module cleans up any inherited
    engine state either way.
    """
    import multiprocessing as mp
    import os

    from backend.tests.server import wait_for_port
    from backend.tests.server_worker import run_test_server

    ctx = mp.get_context("spawn" if os.name == "nt" else "fork")
    port = 8971
    proc = ctx.Process(target=run_test_server, daemon=True)
    proc.start()
    assert wait_for_port(port), "test server did not come up"
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    proc.join(timeout=5)
