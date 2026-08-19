"""Alembic environment script.

Wires Alembic's migration runner to this backend's existing, typed
configuration (``src.config.settings``) and SQLAlchemy metadata
(``src.db.Base.metadata`` populated by ``src.models``), instead of
duplicating connection/URL logic in ``alembic.ini``.

Runs from ``services/backend/`` (both the ``alembic.ini`` file and the
Docker image's ``WORKDIR`` are that directory), so ``import src...`` works
once that directory is on ``sys.path`` — done explicitly below so this also
works when Alembic is invoked from a different working directory.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# services/backend/alembic/env.py -> services/backend/
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.config import settings  # noqa: E402
from src.db import Base  # noqa: E402
from src import models  # noqa: E402,F401 -- registers every model class on Base.metadata

# This is the Alembic Config object, which provides access to the values
# within alembic.ini.
config = context.config

# Single source of truth for the DB URL: src.config.Settings.DATABASE_URL
# (same value src/db.py's engine uses), not a second copy in alembic.ini.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Interpret the config file for Python logging, if alembic.ini declares one.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata used for 'autogenerate' support (`alembic revision --autogenerate`).
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no live DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
