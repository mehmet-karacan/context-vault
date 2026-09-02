from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.config import Settings
from src.db import get_db
from src.infrastructure.security.auth import hash_api_key
from src.main import create_app
from src.models import ApiKey, Document, Principal, Project, Workspace, WorkspaceMembership


@pytest.mark.integration
def test_api_key_cannot_enumerate_another_workspace() -> None:
    """The full HTTP/auth/ORM path must return zero cross-workspace rows."""

    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    pepper = "integration-only-pepper-value-32-bytes"
    raw_key = "cv_test_0123456789abcdefghijklmnop"
    principal_id = uuid.uuid4()
    own_workspace_id = uuid.uuid4()
    other_workspace_id = uuid.uuid4()
    own_project_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    other_document_id = uuid.uuid4()

    try:
        session.add_all(
            [
                Principal(
                    id=principal_id,
                    subject=f"integration:{principal_id}",
                    is_active=True,
                ),
                Workspace(id=own_workspace_id, name="Integration own"),
                Workspace(id=other_workspace_id, name="Integration other"),
            ]
        )
        session.flush()
        session.add_all(
            [
                WorkspaceMembership(
                    workspace_id=own_workspace_id,
                    principal_id=principal_id,
                    role="member",
                ),
                ApiKey(
                    id=uuid.uuid4(),
                    principal_id=principal_id,
                    key_prefix=raw_key[:12],
                    key_hash=hash_api_key(raw_key, pepper),
                ),
                Project(
                    id=own_project_id,
                    workspace_id=own_workspace_id,
                    name="Visible",
                ),
                Project(
                    id=other_project_id,
                    workspace_id=other_workspace_id,
                    name="Hidden",
                ),
                Document(
                    id=other_document_id,
                    project_id=other_project_id,
                    name="secret.txt",
                    size=6,
                    status="uploaded",
                    uploaded_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

        app = create_app(
            Settings(
                APP_ENV="test",
                AUTH_MODE="api_key",
                API_KEY_PEPPER=pepper,
                RATE_LIMIT_ENABLED=False,
            )
        )
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-API-Key": raw_key,
            "X-Workspace-ID": str(own_workspace_id),
        }
        with TestClient(app) as client:
            listed = client.get("/api/v1/projects", headers=headers)
            cross_project = client.get(
                f"/api/v1/documents/{other_document_id}",
                params={"project_id": other_project_id},
                headers=headers,
            )
            absent = client.get(
                f"/api/v1/documents/{uuid.uuid4()}",
                params={"project_id": uuid.uuid4()},
                headers=headers,
            )

        assert listed.status_code == 200
        assert [row["id"] for row in listed.json()] == [str(own_project_id)]
        assert cross_project.status_code == 404
        assert absent.status_code == 404
        assert cross_project.json() == absent.json()
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()
        engine.dispose()
