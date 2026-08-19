"""Aşama 1 kabul kriteri: "Unit testler ve backend startup testi geçer" /
"Eski endpoint'ler çalışmaya devam eder" (health slice).

Uses FastAPI's ``TestClient`` against the real ``app`` object assembled in
``src.main`` (real middleware, real router wiring) but:

- overrides the ``get_db`` dependency with an in-memory fake session, so no
  real database connection is made and no production data is touched;
- monkeypatches ``src.main.init_db`` (the name the ``startup`` event handler
  actually looks up at call time) to a no-op, so entering the TestClient's
  lifespan doesn't attempt a real Postgres connection either.
"""

from fastapi.testclient import TestClient

from src.db import get_db
from src.main import app


class _FakeCountQuery:
    """Stands in for the `db.query(...).filter(...).scalar()` chain."""

    def __init__(self, value):
        self._value = value

    def filter(self, *args, **kwargs):
        return self

    def scalar(self):
        return self._value


class _FakeDB:
    """Returns pre-seeded counts for the two sequential count-queries that
    ``GET /health`` issues (total, then indexed)."""

    def __init__(self, total: int, indexed: int):
        self._values = iter([total, indexed])

    def query(self, *args, **kwargs):
        return _FakeCountQuery(next(self._values))


def test_root_endpoint_still_works(monkeypatch):
    monkeypatch.setattr("src.main.init_db", lambda: None)
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Document RAG API is running"}


def test_health_endpoint_returns_document_counts(monkeypatch):
    monkeypatch.setattr("src.main.init_db", lambda: None)

    def fake_get_db():
        yield _FakeDB(total=7, indexed=4)

    app.dependency_overrides[get_db] = fake_get_db
    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "documents_count": 7,
        "indexed_count": 4,
    }


def test_health_endpoint_handles_zero_documents(monkeypatch):
    monkeypatch.setattr("src.main.init_db", lambda: None)

    def fake_get_db():
        yield _FakeDB(total=0, indexed=0)

    app.dependency_overrides[get_db] = fake_get_db
    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["documents_count"] == 0
    assert body["indexed_count"] == 0
