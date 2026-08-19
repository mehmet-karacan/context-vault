"""Aşama 5.2: unit tests for IdentifierRetriever (DB-free).

Verifies candidate count (config default + override), identifier-array / symbol
/ file-path matching inside the generated SQL, filter application, and the
result shape via a fake session.
"""

from __future__ import annotations

from src.infrastructure.retrieval import IdentifierRetriever, identifier_sql_from_spec


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
    assert IdentifierRetriever().candidate_k == 20  # settings.IDENTIFIER_CANDIDATE_K
    assert IdentifierRetriever(candidate_k=6).candidate_k == 6


def test_spec_holds_extracted_or_explicit_identifiers():
    spec = IdentifierRetriever().build_spec(["PAYMENT_FLAG"], top_k=9)
    assert spec["identifiers"] == ["PAYMENT_FLAG"]
    assert spec["candidate_k"] == 9


def test_search_derives_identifiers_from_query_text():
    session = FakeSession()
    retriever = IdentifierRetriever(session=session)
    retriever.search("PAYMENT_FLAG nasıl set ediliyor?", top_k=10)
    assert "PAYMENT_FLAG" in session.executed_params["ids"]


def test_search_with_explicit_identifiers_skips_extraction():
    session = FakeSession(rows=[_Row("c-1", 1.0)])
    retriever = IdentifierRetriever(session=session)
    results = retriever.search(
        "totally plain language", top_k=10, identifiers=["PAYMENT_FLAG"]
    )
    assert [c.chunk_id for c in results] == ["c-1"]
    assert results[0].source == "identifier"
    assert session.executed_params["ids"] == ["PAYMENT_FLAG"]


def test_search_with_no_identifiers_returns_empty():
    session = FakeSession(rows=[_Row("c-1", 1.0)])
    retriever = IdentifierRetriever(session=session)
    results = retriever.search("bu bir düz cümle", top_k=10)
    assert results == []


def test_sql_matches_array_symbol_and_file_path_with_filters():
    spec = IdentifierRetriever().build_spec(
        ["PAYMENT_FLAG"], top_k=12, filters={"project_id": "proj-2"}
    )
    sql, params = identifier_sql_from_spec(spec)
    assert "chunks.identifiers && :ids" in sql
    assert "lower(coalesce(chunks.symbol_name,'')) = ANY(:eq_ids)" in sql
    assert "source_files.relative_path" in sql
    assert "LEFT JOIN source_files ON source_files.id = chunks.source_file_id" in sql
    assert "d.project_id = :fp0" in sql
    assert params["ids"] == ["PAYMENT_FLAG"]
    assert params["eq_ids"] == ["payment_flag"]
    assert params["like_ids"] == ["%payment_flag%"]
