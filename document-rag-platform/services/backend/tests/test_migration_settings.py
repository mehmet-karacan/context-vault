"""Regression tests for migration-only configuration and startup admission."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.migration_settings import MigrationSettings


def test_migration_settings_requires_only_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    settings = MigrationSettings(_env_file=None)

    assert settings.DATABASE_SCHEMA == "public"


def test_migration_settings_rejects_unsafe_schema_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.setenv("DATABASE_SCHEMA", "public; DROP SCHEMA public")

    with pytest.raises(ValidationError):
        MigrationSettings(_env_file=None)


def test_alembic_environment_does_not_import_application_settings():
    env_source = (Path(__file__).resolve().parents[1] / "alembic" / "env.py").read_text(
        encoding="utf-8"
    )

    assert "from src.config" not in env_source
    assert "from src.db" not in env_source
    assert "MigrationSettings" in env_source
