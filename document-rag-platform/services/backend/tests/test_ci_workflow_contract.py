"""Regression tests for executable CI workflow contracts."""

from __future__ import annotations

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[4]
BACKEND_WORKFLOW = REPO / ".github/workflows/ci-backend.yml"


def _workflow() -> dict:
    return yaml.safe_load(BACKEND_WORKFLOW.read_text(encoding="utf-8"))


def test_backend_workflow_declares_non_secret_storage_test_configuration() -> None:
    environment = _workflow()["jobs"]["backend"]["env"]

    assert environment["MINIO_ENDPOINT"] == "localhost:9000"
    assert environment["MINIO_ACCESS_KEY"] == "cv3_ci"
    assert environment["MINIO_SECRET_KEY"] == "cv3-ci-test-only-12345"
    assert environment["MINIO_BUCKET"] == "rag-documents"
    assert environment["OBJECT_STORAGE_ENCRYPTION_KEY"] == (
        "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    )


def test_restore_fixture_preserves_migration_and_seed_data_before_upgrade() -> None:
    """The fixture must retain Alembic and required active-profile rows."""

    workflow = _workflow()
    steps = workflow["jobs"]["backend"]["steps"]
    restored_schema_step = next(
        step
        for step in steps
        if step.get("name") == "Restored-schema compatibility fixture"
    )
    commands = restored_schema_step["run"]

    restore = commands.index("pg_restore ")
    upgrade = commands.index("uv run alembic upgrade head", restore)
    verify = commands.index("scripts/verify_migrations.py", upgrade)

    assert "--schema-only" not in commands
    assert "alembic stamp" not in commands
    assert restore < upgrade < verify
