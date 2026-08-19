"""Aşama 7.7: API tests for repository/archive/directory ingestion endpoints.

Uses FastAPI ``TestClient`` against the real ``app`` with the ``get_db``
dependency overridden by an in-memory fake (pattern from
``tests/test_health.py`` / ``test_main_app.py``). Covers:

- feature-gate (``FEATURE_REPOSITORY_INGESTION`` off -> 403);
- directory-scan path security: absolute path rejected (400), unknown root
  alias rejected (403), path escaping the root rejected (403), an allowed
  relative path accepted (200);
- document files/versions endpoints return 404 for unknown documents.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from src.api.v1 import repositories as repo_mod
from src.config import settings
from src.db import get_db
from src.infrastructure.repositories.scan_result import ScanResult
from src.main import app
from src.models import Document, Project


class FakeAPIDB:
    """Minimal stand-in for the SQLAlchemy session consumed by these routes."""

    def __init__(self, project=None, documents=None):
        self.project = project
        self.documents = {str(d.id): d for d in (documents or [])}

    def get(self, model, id_):
        if model is Project:
            return self.project
        if model is Document:
            return self.documents.get(str(id_))
        return None

    def query(self, model):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return None

    def all(self):
        return []

    def add(self, obj):
        pass

    def commit(self):
        pass

    def refresh(self, obj):
        pass


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("src.main.init_db", lambda: None)
    # Feature ON by default for the security/404/success tests.
    monkeypatch.setattr(settings, "FEATURE_REPOSITORY_INGESTION", True)

    def _get_db():
        yield FakeAPIDB(project=Project(id=uuid.uuid4(), name="p"))

    app.dependency_overrides[get_db] = _get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@contextmanager
def _gate_off_client(monkeypatch):
    monkeypatch.setattr("src.main.init_db", lambda: None)
    monkeypatch.setattr(settings, "FEATURE_REPOSITORY_INGESTION", False)

    def _get_db():
        yield FakeAPIDB()

    app.dependency_overrides[get_db] = _get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_feature_gate_returns_403_when_disabled(monkeypatch):
    with _gate_off_client(monkeypatch) as c:
        resp = c.post("/directories/scan", json={"project_id": "x", "allowed_root_alias": "a", "relative_path": "b"})
        assert resp.status_code == 403


def test_directory_scan_rejects_absolute_path(client):
    resp = client.post(
        "/directories/scan",
        json={"project_id": "p", "allowed_root_alias": "workspace", "relative_path": "C:/Windows/system"},
    )
    assert resp.status_code == 400
    assert "Absolute paths" in resp.json()["detail"]


def test_directory_scan_rejects_unknown_root_alias(client):
    resp = client.post(
        "/directories/scan",
        json={"project_id": "p", "allowed_root_alias": "nope", "relative_path": "sub"},
    )
    assert resp.status_code == 403
    assert "Unknown allowed root alias" in resp.json()["detail"]


def test_directory_scan_rejects_path_escaping_root(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CODE_ALLOWED_ROOTS", str(tmp_path))
    resp = client.post(
        "/directories/scan",
        json={"project_id": "p", "allowed_root_alias": tmp_path.name, "relative_path": "../escape"},
    )
    assert resp.status_code == 403
    assert "escapes" in resp.json()["detail"]


def test_directory_scan_accepts_allowed_relative_path(client, tmp_path, monkeypatch):
    root = tmp_path
    sub = root / "project-a"
    sub.mkdir(exist_ok=True)
    monkeypatch.setattr(settings, "CODE_ALLOWED_ROOTS", str(root))

    fake_doc = Document(id=uuid.uuid4(), project_id=uuid.uuid4(), name="d", size=0)
    dummy_scan = ScanResult(source_type="directory", source_revision="m", root_dir=str(sub), files=[])

    class _FakeService:
        def run(self, db, doc, scan):
            return {"version_id": None, "version_no": 1, "files_count": 0,
                    "files_processed": 0, "files_copied": 0, "chunks": 0, "deleted_files": []}

    monkeypatch.setattr(repo_mod, "_discover_directory", lambda *a, **k: dummy_scan)
    monkeypatch.setattr(repo_mod, "_build_reindex_service", lambda: _FakeService())
    monkeypatch.setattr(
        repo_mod, "_get_or_create_source_document",
        lambda db, proj, st, uri, name: fake_doc,
    )

    resp = client.post(
        "/directories/scan",
        json={"project_id": "p", "allowed_root_alias": root.name, "relative_path": "project-a"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_type"] == "directory"
    assert body["document_id"] == str(fake_doc.id)
    assert body["version_no"] == 1


def test_document_files_404_for_unknown_document(client):
    resp = client.get(f"/documents/{uuid.uuid4()}/files")
    assert resp.status_code == 404


def test_document_versions_404_for_unknown_document(client):
    resp = client.get(f"/documents/{uuid.uuid4()}/versions")
    assert resp.status_code == 404


def test_repository_ingest_404_for_missing_project(client):
    real_db = FakeAPIDB(project=None)

    def _get_db():
        yield real_db

    app.dependency_overrides[get_db] = _get_db
    try:
        with TestClient(app) as c:
            resp = c.post(
                "/repositories/ingest",
                json={"project_id": str(uuid.uuid4()), "repository_url": "https://github.com/o/r.git"},
            )
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
