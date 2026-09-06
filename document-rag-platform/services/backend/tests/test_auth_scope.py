from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.config import Settings
from src.db import get_db
from src.domain.identity import PrincipalContext
from src.infrastructure.security.auth import get_principal_context, hash_api_key
from src.main import create_app
from src.models import ApiKey, Principal, WorkspaceMembership


PEPPER = "p" * 32
RAW_KEY = "cv_live_0123456789abcdefghijklmnop"
PRINCIPAL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _AuthDB:
    def __init__(self, *, member: bool = True):
        self.principal = Principal(
            id=PRINCIPAL_ID, subject="user:one", display_name="One", is_active=True
        )
        self.api_key = ApiKey(
            id=uuid.uuid4(),
            principal_id=PRINCIPAL_ID,
            key_prefix=RAW_KEY[:12],
            key_hash=hash_api_key(RAW_KEY, PEPPER),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        self.membership = (
            WorkspaceMembership(
                workspace_id=WORKSPACE_ID, principal_id=PRINCIPAL_ID, role="member"
            )
            if member
            else None
        )
        self.flushed = False

    def query(self, model):
        if model is ApiKey:
            return _Query([self.api_key])
        if model is WorkspaceMembership:
            return _Query([self.membership] if self.membership else [])
        return _Query([])

    def get(self, model, key):
        if model is Principal and key == PRINCIPAL_ID:
            return self.principal
        return None

    def flush(self):
        self.flushed = True


def _api_key_app(db: _AuthDB) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(
        APP_ENV="test",
        AUTH_MODE="api_key",
        API_KEY_PEPPER=PEPPER,
    )

    @app.get("/protected")
    def protected(principal=Depends(get_principal_context)):
        return {
            "principal_id": str(principal.principal_id),
            "workspace_id": str(principal.workspace_id),
        }

    app.dependency_overrides[get_db] = lambda: db
    return app


def test_api_key_hash_preserves_existing_hmac_records_without_storing_token():
    assert hash_api_key("cv_live_0123456789abcdef", "pepper-value") == (
        "69bac4551141c94b1efc39fae65cb3fb244af650ec51f0cfbf2e4c70a9a4366f"
    )


def test_api_key_authenticates_hash_only_and_membership():
    db = _AuthDB()
    with TestClient(_api_key_app(db)) as client:
        response = client.get(
            "/protected",
            headers={"X-API-Key": RAW_KEY, "X-Workspace-ID": str(WORKSPACE_ID)},
        )
    assert response.status_code == 200
    assert response.json()["principal_id"] == str(PRINCIPAL_ID)
    assert db.api_key.key_hash != RAW_KEY
    assert db.flushed is True


def test_api_key_rejects_missing_workspace_membership():
    with TestClient(_api_key_app(_AuthDB(member=False))) as client:
        response = client.get(
            "/protected",
            headers={"X-API-Key": RAW_KEY, "X-Workspace-ID": str(WORKSPACE_ID)},
        )
    assert response.status_code == 403


def test_api_key_rejects_bad_or_missing_key_without_detail_leak():
    with TestClient(_api_key_app(_AuthDB())) as client:
        missing = client.get("/protected")
        bad = client.get(
            "/protected",
            headers={
                "X-API-Key": "cv_live_XXXXXXXXXXXXXXXXXXXXXXXX",
                "X-Workspace-ID": str(WORKSPACE_ID),
            },
        )
    assert missing.status_code == 401
    assert bad.status_code == 401
    assert missing.json() == bad.json()


@pytest.mark.parametrize("environment", ["production", "staging", "development"])
def test_auth_disabled_rejected_outside_loopback_local(environment):
    with pytest.raises(ValueError, match="AUTH_MODE=disabled"):
        create_app(Settings(APP_ENV=environment, AUTH_MODE="disabled"))


def test_auth_disabled_rejects_non_loopback_bind_even_local():
    with pytest.raises(ValueError, match="loopback"):
        create_app(Settings(APP_ENV="local", AUTH_MODE="disabled", BIND_HOST="0.0.0.0"))


@pytest.mark.parametrize(
    "key,match",
    [
        (None, "encryption key is required"),
        ("not-base64", "not valid base64"),
        ("c2hvcnQ=", "decode to 32 bytes"),
    ],
)
def test_runtime_rejects_missing_or_invalid_storage_encryption_key(key, match):
    with pytest.raises(ValueError, match=match):
        create_app(
            Settings(
                APP_ENV="local",
                AUTH_MODE="disabled",
                OBJECT_STORAGE_ENCRYPTION_KEY=key,
            )
        )


def test_production_rejects_default_storage_credentials():
    with pytest.raises(ValueError, match="default MinIO"):
        create_app(
            Settings(
                APP_ENV="production",
                AUTH_MODE="api_key",
                API_KEY_PEPPER=PEPPER,
                RATE_LIMIT_ENABLED=True,
                RATE_LIMIT_BACKEND="redis",
                MINIO_ACCESS_KEY="minioadmin",
                MINIO_SECRET_KEY="minioadmin",
            )
        )


def test_production_requires_redis_backed_rate_limit():
    with pytest.raises(ValueError, match="Redis-backed rate limiting"):
        create_app(
            Settings(
                APP_ENV="production",
                AUTH_MODE="api_key",
                API_KEY_PEPPER=PEPPER,
                DATABASE_URL="postgresql://app:unique-secret@db/context_vault",
                MINIO_ACCESS_KEY="application",
                MINIO_SECRET_KEY="unique-secret",
            )
        )


def test_debug_endpoint_is_closed_to_anonymous_and_non_admin(monkeypatch):
    monkeypatch.setattr("src.main.init_db", lambda: None)
    app = create_app(
        Settings(APP_ENV="test", AUTH_MODE="api_key", API_KEY_PEPPER=PEPPER)
    )
    body = {"query": "diagnose", "project_id": str(uuid.uuid4())}

    with TestClient(app) as client:
        anonymous = client.post("/api/v1/debug/retrieval", json=body)
    assert anonymous.status_code == 401

    app.dependency_overrides[get_principal_context] = lambda: PrincipalContext(
        principal_id=PRINCIPAL_ID,
        workspace_id=WORKSPACE_ID,
        roles=frozenset({"member"}),
        auth_mode="api_key",
    )
    with TestClient(app) as client:
        member = client.post("/api/v1/debug/retrieval", json=body)
    assert member.status_code == 403
    assert member.json() == {"detail": "Administrator role required"}
