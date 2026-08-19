"""Aşama 5.2: unit tests for LexicalRetriever (DB-free).

Verifies candidate count (config default + override), the ``simple`` text-search
config choice, filter application inside the spec and generated SQL, and the
result shape via a fake session.
"""

from __future__ import annotations

from src.infrastructure.retrieval import LexicalRetriever, lexical_sql_from_spec


class _Row:
    def __init__(self, chunk_id, score):
        self.chunk_id = chunk_id
        self.score = score


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed_sql = None
        self.executed_params = None

    def execute(self, stmt, params=None):
        self.executed_sql = str(stmt)
        self.executed_params = dict(params or {})
        return self

    def fetchall(self):
        return self.rows


def test_candidate_k_defaults_and_override():
    assert LexicalRetriever().candidate_k == 40  # settings.LEXICAL_CANDIDATE_K
    assert LexicalRetriever(candidate_k=10).candidate_k == 10


def test_simple_ts_config_by_default_and_override():
    assert LexicalRetriever().ts_config == "simple"
    assert LexicalRetriever(ts_config="turkish").ts_config == "turkish"


def test_spec_candidate_k_and_config():
    spec = LexicalRetriever().build_spec("hello", top_k=8)
    assert spec["candidate_k"] == 8
    assert spec["ts_config"] == "simple"
    assert spec["query_text"] == "hello"


def test_sql_uses_simple_config_and_filters():
    spec = LexicalRetriever().build_spec(
        "PAYMENT_FLAG", top_k=15, filters={"project_id": "proj-1"}
    )
    sql, params = lexical_sql_from_spec(spec)
    assert "plainto_tsquery('simple', :query_text)" in sql
    assert "query @@ chunks.search_vector" in sql
    assert "ts_rank_cd(chunks.search_vector, query) AS score" in sql
    assert "WHERE query @@ chunks.search_vector AND d.project_id = :fp0" in sql
    assert params["query_text"] == "PAYMENT_FLAG"
    assert params["fp0"] == "proj-1"
    assert params["candidate_k"] == 15


def test_search_returns_candidate_shape_via_fake_session():
    session = FakeSession(rows=[_Row("chunk-a", 0.88), _Row("chunk-b", 0.6)])
    retriever = LexicalRetriever(session=session)
    results = retriever.search("billing", top_k=5)
    assert [c.chunk_id for c in results] == ["chunk-a", "chunk-b"]
    assert {c.source for c in results} == {"lexical"}
    assert results[0].rank == 1
    assert session.executed_sql is not None
