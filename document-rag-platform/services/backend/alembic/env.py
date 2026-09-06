"""Alembic environment script.

Wires Alembic's migration runner to migration-only configuration and
SQLAlchemy metadata without importing application/provider settings.

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
from sqlalchemy import engine_from_config, pool, text

# services/backend/alembic/env.py -> services/backend/
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src import models  # noqa: E402,F401 -- registers every model class on Base.metadata
from src.migration_settings import MigrationSettings  # noqa: E402
from src.persistence import Base  # noqa: E402

# This is the Alembic Config object, which provides access to the values
# within alembic.ini.
config = context.config

migration_settings = MigrationSettings()
config.set_main_option("sqlalchemy.url", migration_settings.DATABASE_URL)

# Interpret the config file for Python logging, if alembic.ini declares one.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata used for 'autogenerate' support (`alembic revision --autogenerate`).
target_metadata = Base.metadata


def include_object(object_, name, type_, reflected, compare_to):
    """Keep Alembic's own revision table outside application autogenerate."""
    return not (
        type_ == "table"
        and name == config.get_main_option("version_table", "alembic_version")
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no live DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        version_table_schema=migration_settings.DATABASE_SCHEMA,
        include_schemas=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
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
        connection.execute(
            text("SELECT set_config('lock_timeout', :timeout, false)"),
            {"timeout": f"{migration_settings.MIGRATION_LOCK_TIMEOUT_MS}ms"},
        )
        connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, false)"),
            {"timeout": f"{migration_settings.MIGRATION_STATEMENT_TIMEOUT_MS}ms"},
        )
        # SQLAlchemy 2.x autobegins on the SET_CONFIG calls. Commit this
        # configuration-only transaction so Alembic's migration transaction
        # is not rolled back when the connection context closes.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=migration_settings.DATABASE_SCHEMA,
            include_schemas=True,
            compare_type=True,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
