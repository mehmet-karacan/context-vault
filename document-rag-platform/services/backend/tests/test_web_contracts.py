"""A10 public schema and diagnostics regression gates; no provider effects."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.v1 import chat, debug, session, ingestion_jobs
from src.api.v1.contracts import ChatResponse
from src.api.v1.projects import ProjectCreate
from src.application.retrieval_service import RetrievalResult
from src.config import Settings
from src.db import get_db
from src.domain.identity import PrincipalContext
from src.infrastructure.security.auth import get_principal_context


def test_session_confirms_scope_without_credentials():
    app = FastAPI()
    app.state.settings = Settings()
    app.include_router(session.router)
    principal = PrincipalContext(uuid4(), uuid4(), frozenset({"member"}), "api_key")
    app.dependency_overrides[get_principal_context] = lambda: principal
    response = TestClient(app).get("/session")
    assert response.status_code == 200
    assert response.json() == {
        "principal_id": str(principal.principal_id),
        "workspace_id": str(principal.workspace_id),
        "auth_mode": "api_key",
        "roles": ["member"],
        "upload_max_bytes": app.state.settings.MAX_DOCUMENT_BYTES,
    }


def test_public_inputs_and_outputs_are_not_untyped_dicts():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProjectCreate(name="  ")
    with pytest.raises(ValidationError):
        ProjectCreate(name="x" * 201)
    with pytest.raises(ValidationError):
        ChatResponse.model_validate({"answer": "unsupported"})


def test_diagnostics_never_returns_raw_query_or_context(monkeypatch):
    principal = PrincipalContext(uuid4(), uuid4(), frozenset({"admin"}), "disabled")
    db = SimpleNamespace(add=lambda row: None, commit=lambda: None)
    monkeypatch.setattr(debug, "require_project_access", lambda *args: None)
    monkeypatch.setattr(
        debug, "_active_embedding_profile", lambda _: SimpleNamespace(id=uuid4())
    )
    monkeypatch.setattr(debug, "_build_resolvers", lambda *args: (None, None))
    monkeypatch.setattr(debug, "DenseVectorRetriever", lambda **kwargs: None)
    monkeypatch.setattr(debug, "LexicalRetriever", lambda **kwargs: None)
    monkeypatch.setattr(debug, "IdentifierRetriever", lambda **kwargs: None)
    result = RetrievalResult(query="DO_NOT_RETURN_RAW_QUERY")
    monkeypatch.setattr(
        debug,
        "RetrievalService",
        lambda **kwargs: SimpleNamespace(retrieve=lambda *args, **kwargs: result),
    )
    response = debug.debug_retrieval(
        debug.RetrievalDebugRequest(query=result.query, project_id=uuid4()),
        db=db,
        principal=principal,
    )
    assert "DO_NOT_RETURN_RAW_QUERY" not in str(response)
    assert "context" not in response
    assert "query" not in response
    assert set(response) == {
        "retrieval_run_id",
        "bundle_hash",
        "stages",
        "fallback_reason",
        "timings_ms",
    }


def test_diagnostics_rejects_member_before_any_retrieval():
    app = FastAPI()
    app.include_router(debug.router)
    app.state.settings = Settings()
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_principal_context] = lambda: PrincipalContext(
        uuid4(), uuid4(), frozenset({"member"}), "api_key"
    )
    response = TestClient(app).post(
        "/debug/retrieval", json={"project_id": str(uuid4()), "query": "test"}
    )
    assert response.status_code == 403


def test_chat_success_commits_answer_transaction_before_return(monkeypatch):
    principal = PrincipalContext(uuid4(), uuid4(), frozenset({"member"}), "api_key")
    transaction = {"pending": [], "durable": []}

    def commit():
        transaction["durable"].extend(transaction["pending"])
        transaction["pending"].clear()

    db = SimpleNamespace(commit=commit)
    monkeypatch.setattr(chat, "require_project_access", lambda *args: None)
    monkeypatch.setattr(
        chat, "ensure_conversation", lambda *args, **kwargs: str(uuid4())
    )
    monkeypatch.setattr(chat, "load_conversation_history", lambda *args, **kwargs: ())
    monkeypatch.setattr(
        chat, "_active_embedding_profile", lambda _: SimpleNamespace(id=uuid4())
    )
    monkeypatch.setattr(chat, "_build_resolvers", lambda *args: (None, None))
    for name in ("DenseVectorRetriever", "LexicalRetriever", "IdentifierRetriever"):
        monkeypatch.setattr(chat, name, lambda **kwargs: None)
    monkeypatch.setattr(
        chat,
        "RetrievalService",
        lambda **kwargs: SimpleNamespace(
            retrieve=lambda *args, **kwargs: RetrievalResult(query="test")
        ),
    )

    def generate(**kwargs):
        transaction["pending"].extend(["user", "assistant", "claim", "citation"])
        return {"answer": "committed"}

    monkeypatch.setattr(chat, "generate_answer", generate)
    chat.query_chat(
        chat.ChatQuery(query="test", project_id=uuid4()), db=db, principal=principal
    )
    assert transaction["pending"] == []
    assert transaction["durable"] == ["user", "assistant", "claim", "citation"]


def test_job_percentage_is_not_a_stale_default_estimate():
    job = SimpleNamespace(
        id=uuid4(),
        version_id=uuid4(),
        version=None,
        status="running",
        stage="parsing",
        progress=0,
        attempt=1,
        error_code=None,
        error_message=None,
        started_at=None,
        finished_at=None,
        created_at=None,
    )
    assert ingestion_jobs._serialize_job(job)["progress"] is None
    job.status = "completed"
    assert ingestion_jobs._serialize_job(job)["progress"] == 100
