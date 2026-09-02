"""Minimal configuration surface for Alembic and schema admission checks.

Migration commands intentionally do not import :mod:`src.config`.  That
module validates application/provider configuration, while migrations only
need a database URL and schema-safe parameters.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrationSettings(BaseSettings):
    """Configuration that is safe to load in migration-only environments."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    DATABASE_SCHEMA: str = Field(default="public", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    MIGRATION_LOCK_TIMEOUT_MS: int = Field(default=5_000, ge=1, le=300_000)
    MIGRATION_STATEMENT_TIMEOUT_MS: int = Field(default=300_000, ge=1, le=3_600_000)


# Updated only by an intentional migration-lineage change. Runtime readiness
# compares the database's exact Alembic revision with this value and never
# mutates the schema to make a mismatch disappear.
EXPECTED_ALEMBIC_HEAD = "cv3_00000002"
