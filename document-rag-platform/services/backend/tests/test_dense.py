"""Aşama 5.1: unit tests for DenseVectorRetriever (DB-free).

Exercises candidate count (config default + injectable override), configurable
HNSW ``ef_search``, filter normalization (project/document/version/source
type/scope) inside the generated spec + SQL, and the result shape via a fake
session — no live PostgreSQL required.
"""

from __future__ import annotations

from src.infrastructure.retrieval import DenseVectorRetriever, dense_sql_from_spec
from src.infrastructure.retrieval.base import normalize_filters


class _Row:
    def __init__(self, chunk_id, score, metadata=None):
        self.chunk_id = chunk_id
        self.score = score
        self.metadata = metadata


class FakeSession:
    """Minimal SQLAlchemy-session lookalike recording the executed statement."""

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


def test_candidate_k_defaults_to_config_override_injects():
    assert DenseVectorRetriever().candidate_k == 40  # settings.VECTOR_CANDIDATE_K
    assert DenseVectorRetriever(candidate_k=12).candidate_k == 12


def test_spec_candidate_k_uses_top_k_when_given():
    spec = DenseVectorRetriever().build_spec([0.1, 0.2], top_k=7)
    assert spec["candidate_k"] == 7
    # Without top_k it falls back to the configured candidate_k.
    spec2 = DenseVectorRetriever().build_spec([0.1, 0.2])
    assert spec2["candidate_k"] == 40


def test_ef_search_configurable_hnsw():
    assert DenseVectorRetriever().ef_search == 40
    assert DenseVectorRetriever(ef_search=120).ef_search == 120
    assert DenseVectorRetriever(ef_search=120).build_spec([0.1])["hnsw"] == {"ef_search": 120}


def test_filters_applied_as_terms_in_spec():
    spec = DenseVectorRetriever().build_spec(
        [0.5],
        filters={
            "project_id": "proj-1",
            "document_ids": ["docA", "docB"],
            "version_id": "ver-1",
            "scope": "code",
        },
    )
    terms = {t["field"]: t for t in spec["filters"]}
    assert terms["project_id"]["table"] == "document"
    assert terms["document_id"]["op"] == "in_"
    assert terms["document_id"]["value"] == ["docA", "docB"]
    assert terms["version_id"]["table"] == "chunk"
    assert terms["source_type"]["op"] == "in_"
    assert terms["source_type"]["value"] == ["repository", "directory", "archive"]


def test_normalize_filters_ignores_unknown_keys():
    terms = normalize_filters({"project_id": "p", "bogus_key": 1})
    fields = [t.field for t in terms]
    assert fields == ["project_id"]


def test_empty_document_ids_produces_no_filter():
    terms = normalize_filters({"document_ids": []})
    assert terms == []


def test_explicit_source_type_overrides_scope():
    terms = normalize_filters({"source_type": "image", "scope": "code"})
    assert [t for t in terms] == [t for t in normalize_filters({"source_type": "image"})]
    assert terms[0].op == "eq"


def test_sql_includes_filter_order_limit_and_dense_columns():
    spec = DenseVectorRetriever().build_spec(
        [0.1, 0.2, 0.3], top_k=10, filters={"project_id": "proj-9"}
    )
    sql, params = dense_sql_from_spec(spec)
    assert "JOIN chunks ON chunks.id = chunk_embeddings.chunk_id" in sql
    assert "JOIN documents AS d ON d.id = chunks.document_id" in sql
    assert "1 - (chunk_embeddings.embedding <=> :query_embedding) AS score" in sql
    assert "ORDER BY chunk_embeddings.embedding <=> :query_embedding" in sql
    assert "WHERE d.project_id = :fp0" in sql
    assert "LIMIT :candidate_k" in sql
    assert params["fp0"] == "proj-9"
    assert params["query_embedding"] == [0.1, 0.2, 0.3]
    assert params["candidate_k"] == 10


def test_search_returns_candidate_shape_via_fake_session():
    session = FakeSession(rows=[_Row("chunk-1", 0.93), _Row("chunk-2", 0.71)])
    retriever = DenseVectorRetriever(session=session)
    results = retriever.search([0.1, 0.2], top_k=5, filters={"project_id": "p"})
    assert [c.chunk_id for c in results] == ["chunk-1", "chunk-2"]
    assert [c.rank for c in results] == [1, 2]
    assert [c.score for c in results] == [0.93, 0.71]
    assert {c.source for c in results} == {"dense"}
    assert session.executed_sql is not None
