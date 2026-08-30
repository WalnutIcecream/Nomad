"""Alembic environment: how migrations find the settings + schema.

The connection URL comes from ``NOMAD_DATABASE_URL`` (via
``ControllerSettings``), so the same migration commands work against a local
Postgres in a developer's machine and a managed cloud database — only the
environment variable changes. The ``backend.domain.entities`` import exists
solely to register every ORM table on ``Base.metadata`` so autogenerate sees
the full schema.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

from backend.config import ControllerSettings
from backend.db.session import Base
from backend.domain import entities  # noqa: F401 — registers all tables on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", ControllerSettings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without a live connection (dry-run / scripting)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the real database."""

    def get_connection():
        return engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        ).connect()

    with get_connection() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()