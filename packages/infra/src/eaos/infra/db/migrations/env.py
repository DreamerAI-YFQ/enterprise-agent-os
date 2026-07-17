"""Alembic migration environment.

Reads the database URL from the ``EAOS_DB__URL`` environment variable and
converts the asyncpg driver to psycopg (sync) for migration execution. Migrations
use raw SQL (target_metadata=None) — the schema source of truth is the migration
files, not ORM models.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

config = context.config

# Match the application configuration flow for local development while still
# allowing deployment environments to override every value explicitly.
load_dotenv()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_url() -> str:
    """Read DB URL from env, converting asyncpg -> psycopg for sync migrations."""
    url = os.getenv(
        "EAOS_DB__URL",
        "postgresql+asyncpg://eaos:eaos@localhost:5432/eaos",
    )
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


def run_migrations_offline() -> None:
    """Run migrations in offline mode (emit SQL to stdout)."""
    context.configure(
        url=_get_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live DB connection."""
    connectable = engine_from_config(
        {"sqlalchemy.url": _get_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
