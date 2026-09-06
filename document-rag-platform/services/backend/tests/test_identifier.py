"""Aşama 5.2: unit tests for IdentifierRetriever (DB-free).

Verifies candidate count (config default + override), identifier-array / symbol
/ file-path matching inside the generated SQL, filter application, and the
result shape via a fake session.
"""

from __future__ import annotations

import pytest

from src.infrastructure.retrieval import (
    DenseVectorRetriever,
    IdentifierRetriever,
    LexicalRetriever,
    identifier_sql_from_spec,
)

SCOPED_FILTERS = {
    "workspace_id": "workspace",
    "project_id": "project",
    "embedding_profile_id": "profile",
    "active_versions_only": True,
    "data_classifications": ["internal"],
}


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
    retriever.search(
        "PAYMENT_FLAG nasıl set ediliyor?",
        top_k=10,
        filters=SCOPED_FILTERS,
    )
    assert "PAYMENT_FLAG" in session.executed_params["ids"]


def test_search_with_explicit_identifiers_skips_extraction():
    session = FakeSession(rows=[_Row("c-1", 1.0)])
    retriever = IdentifierRetriever(session=session)
    results = retriever.search(
        "totally plain language",
        top_k=10,
        filters=SCOPED_FILTERS,
        identifiers=["PAYMENT_FLAG"],
    )
    assert [c.chunk_id for c in results] == ["c-1"]
    assert results[0].source == "identifier"
    assert session.executed_params["ids"] == ["PAYMENT_FLAG"]


def test_search_with_no_identifiers_returns_empty():
    session = FakeSession(rows=[_Row("c-1", 1.0)])
    retriever = IdentifierRetriever(session=session)
    results = retriever.search("bu bir düz cümle", top_k=10, filters=SCOPED_FILTERS)
    assert results == []


@pytest.mark.parametrize(
    "retriever,args",
    [
        (DenseVectorRetriever(session=FakeSession()), ([0.1] * 1024, 5)),
        (LexicalRetriever(session=FakeSession()), ("query", 5)),
        (IdentifierRetriever(session=FakeSession()), ("PAYMENT_FLAG", 5)),
    ],
)
def test_retrieval_repositories_reject_unscoped_search(retriever, args):
    with pytest.raises(ValueError, match="complete RetrievalScope"):
        retriever.search(*args)


def test_sql_matches_array_symbol_and_file_path_with_filters():
    spec = IdentifierRetriever().build_spec(
        ["PAYMENT_FLAG"], top_k=12, filters={"project_id": "proj-2"}
    )
    sql, params = identifier_sql_from_spec(spec)
    assert "c.identifiers && CAST(:ids AS text[])" in sql
    assert "lower(coalesce(c.symbol_name,'')) = ANY(:eq_ids)" in sql
    assert "sf.relative_path" in sql
    assert "LEFT JOIN source_files AS sf ON sf.id = c.source_file_id" in sql
    assert "d.project_id = :fp0" in sql
    assert params["ids"] == ["PAYMENT_FLAG"]
    assert params["eq_ids"] == ["payment_flag"]
    assert params["prefix_ids"] == ["payment\\_flag%"]
    assert not params["prefix_ids"][0].startswith("%")


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("PAYMENT_FLAG", "payment\\_flag%"),
        ("Rate%Value", "rate\\%value%"),
        (r"path\\name", r"path\\\\name%"),
    ],
)
def test_identifier_prefix_patterns_escape_like_metacharacters(identifier, expected):
    spec = IdentifierRetriever().build_spec([identifier], filters={"project_id": "p"})
    sql, params = identifier_sql_from_spec(spec)
    assert params["prefix_ids"] == [expected]
    assert not params["prefix_ids"][0].startswith("%")
    assert "symbol_qualified_name" in sql
    assert "package_name" in sql
    assert "schema_name" in sql
    assert "table_name" in sql
    assert "column_name" in sql


def test_substring_is_explicit_opt_in_and_has_own_match_type():
    default_sql, default_params = identifier_sql_from_spec(
        IdentifierRetriever().build_spec(["FLAG"], filters={"project_id": "p"})
    )
    opted_sql, opted_params = identifier_sql_from_spec(
        IdentifierRetriever(allow_substring=True).build_spec(
            ["FLAG"], filters={"project_id": "p"}
        )
    )
    assert "THEN 'substring'" not in default_sql
    assert "THEN 'substring'" in opted_sql
    assert default_params["prefix_ids"] == ["flag%"]
    assert opted_params["substring_ids"] == ["%flag%"]
