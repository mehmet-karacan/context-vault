"""Regression tests for executable CI workflow contracts."""

from __future__ import annotations

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[4]
BACKEND_WORKFLOW = REPO / ".github/workflows/ci-backend.yml"
OWNERSHIP_WORKFLOW = REPO / ".github/workflows/commit-ownership.yml"
SECURITY_WORKFLOW = REPO / ".github/workflows/ci-security.yml"
FRONTEND_WORKFLOW = REPO / ".github/workflows/ci-frontend.yml"
FRONTEND_DOCKERFILE = REPO / "document-rag-platform/apps/web/Dockerfile"


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


def test_ownership_checks_scan_only_commits_introduced_by_the_event() -> None:
    """Ignore synthetic merges and pre-existing base-branch history."""

    for path, job_name in (
        (OWNERSHIP_WORKFLOW, "check-ownership"),
        (SECURITY_WORKFLOW, "supply-chain"),
    ):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        steps = workflow["jobs"][job_name]["steps"]
        ownership_step = next(
            step
            for step in steps
            if step.get("name")
            in {"Commit sahiplik ve vocab denetimi", "Commit ownership check"}
        )
        environment = ownership_step["env"]
        commands = ownership_step["run"]

        assert environment["EVENT_NAME"] == "${{ github.event_name }}"
        assert environment["BEFORE_SHA"] == "${{ github.event.before }}"
        assert environment["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
        assert environment["HEAD_SHA"] == (
            "${{ github.event.pull_request.head.sha || github.sha }}"
        )
        assert not any(
            line.strip().split(maxsplit=1)[0] == "--all"
            for line in commands.splitlines()
            if line.strip()
        )
        assert 'refspec="$BASE_SHA..$HEAD_SHA"' in commands
        assert 'refspec="$BEFORE_SHA..$HEAD_SHA"' in commands
        assert '--refspec "$refspec"' in commands


def test_frontend_image_and_ci_share_the_canonical_node_version() -> None:
    workflow = FRONTEND_WORKFLOW.read_text(encoding="utf-8")
    dockerfile = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")

    assert "node-version-file: document-rag-platform/apps/web/.nvmrc" in workflow
    assert "NODE_VERSION=\"$(tr -d '[:space:]' < .nvmrc)\"" in workflow
    assert '--build-arg "NODE_VERSION=$NODE_VERSION"' in workflow
    assert "ARG NODE_VERSION" in dockerfile
    assert "FROM node:${NODE_VERSION}-bookworm-slim@sha256:" in dockerfile
    assert "USER node" in dockerfile
