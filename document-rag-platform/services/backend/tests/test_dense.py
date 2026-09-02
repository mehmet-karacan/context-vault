"""Aşama 5.1: unit tests for DenseVectorRetriever (DB-free).

Exercises candidate count (config default + injectable override), configurable
HNSW ``ef_search``, filter normalization (project/document/version/source
type/scope) inside the generated spec + SQL, and the result shape via a fake
session — no live PostgreSQL required.
"""

from __future__ import annotations

import pytest

from src.infrastructure.retrieval import DenseVectorRetriever, dense_sql_from_spec
from src.infrastructure.retrieval.base import normalize_filters
from src.models import EMBEDDING_DIMENSION

VECTOR = [0.1] * EMBEDDING_DIMENSION
SCOPED_FILTERS = {
    "workspace_id": "workspace",
    "project_id": "project",
    "embedding_profile_id": "profile",
    "active_versions_only": True,
    "data_classifications": ["internal"],
}


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


class SourceAwareFakeSession(FakeSession):
    """Returns per-source rows based on the table the emitted SQL reads from.

    Distinguishes the canonical source query (``FROM chunk_embeddings``) from
    the legacy query (``FROM chunks``) so a test can hand each one its own rows,
    proving partial-coverage merging.
    """

    def __init__(self, chunk_embeddings_rows, chunks_rows):
        super().__init__([])
        self.chunk_embeddings_rows = chunk_embeddings_rows
        self.chunks_rows = chunks_rows

    def fetchall(self):
        sql = self.executed_sql or ""
        if "FROM chunk_embeddings" in sql:
            return self.chunk_embeddings_rows
        if "FROM chunks" in sql:
            return self.chunks_rows
        return self.rows


def test_candidate_k_defaults_to_config_override_injects():
    assert DenseVectorRetriever().candidate_k == 40  # settings.VECTOR_CANDIDATE_K
    assert DenseVectorRetriever(candidate_k=12).candidate_k == 12


def test_spec_candidate_k_uses_top_k_when_given():
    spec = DenseVectorRetriever().build_spec(VECTOR, top_k=7)
    assert spec["candidate_k"] == 7
    # Without top_k it falls back to the configured candidate_k.
    spec2 = DenseVectorRetriever().build_spec(VECTOR)
    assert spec2["candidate_k"] == 40


def test_ef_search_configurable_hnsw():
    assert DenseVectorRetriever().ef_search == 20
    assert DenseVectorRetriever(ef_search=120).ef_search == 120
    assert DenseVectorRetriever(ef_search=120).build_spec(VECTOR)["hnsw"] == {
        "ef_search": 120
    }


def test_filters_applied_as_terms_in_spec():
    spec = DenseVectorRetriever().build_spec(
        VECTOR,
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
    with pytest.raises(ValueError, match="bogus_key"):
        normalize_filters({"project_id": "p", "bogus_key": 1})


def test_empty_document_ids_produces_deny_all_filter():
    terms = normalize_filters({"document_ids": []})
    assert len(terms) == 1
    assert terms[0].op == "false"


def test_explicit_source_type_overrides_scope():
    terms = normalize_filters({"source_type": "image", "scope": "code"})
    assert [t for t in terms] == [
        t for t in normalize_filters({"source_type": "image"})
    ]
    assert terms[0].op == "eq"


def test_sql_includes_filter_order_limit_and_dense_columns():
    spec = DenseVectorRetriever().build_spec(
        VECTOR, top_k=10, filters={"project_id": "proj-9"}
    )
    sql, params = dense_sql_from_spec(spec)
    assert "JOIN chunks AS c ON c.id = ce.chunk_id" in sql
    assert "JOIN documents AS d ON d.id = c.document_id" in sql
    assert "1 - (ce.embedding <=> CAST(:query_embedding AS vector)) AS score" in sql
    assert "ORDER BY ce.embedding <=> CAST(:query_embedding AS vector)" in sql
    assert "WHERE d.deleted_at IS NULL AND d.project_id = :fp0" in sql
    assert "LIMIT :candidate_k" in sql
    assert params["fp0"] == "proj-9"
    assert params["query_embedding"] == VECTOR
    assert params["candidate_k"] == 10


def test_search_returns_candidate_shape_via_fake_session():
    session = FakeSession(rows=[_Row("chunk-1", 0.93), _Row("chunk-2", 0.71)])
    retriever = DenseVectorRetriever(session=session)
    results = retriever.search(VECTOR, top_k=5, filters=SCOPED_FILTERS)
    assert [c.chunk_id for c in results] == ["chunk-1", "chunk-2"]
    assert [c.rank for c in results] == [1, 2]
    assert [c.score for c in results] == [0.93, 0.71]
    assert {c.source for c in results} == {"dense"}
    assert session.executed_sql is not None


def test_runtime_dense_search_never_reads_legacy_chunk_embedding():
    session = SourceAwareFakeSession(
        chunk_embeddings_rows=[_Row("unrelated-1", 0.50)],
        chunks_rows=[_Row("legacy-nearest-1", 0.95)],
    )
    retriever = DenseVectorRetriever(session=session)
    results = retriever.search(VECTOR, top_k=5, filters=SCOPED_FILTERS)

    assert [c.chunk_id for c in results] == ["unrelated-1"]
    assert results[0].metadata["source"] == "chunk_embeddings"
    assert "FROM chunk_embeddings AS ce" in (session.executed_sql or "")
    assert "chunks.embedding" not in (session.executed_sql or "")


def test_canonical_embedding_is_authoritative_when_legacy_value_also_exists():
    session = SourceAwareFakeSession(
        chunk_embeddings_rows=[_Row("dup-1", 0.70)],
        chunks_rows=[_Row("dup-1", 0.92)],
    )
    retriever = DenseVectorRetriever(session=session)
    results = retriever.search(VECTOR, top_k=5, filters=SCOPED_FILTERS)

    assert [c.chunk_id for c in results] == ["dup-1"]
    assert results[0].score == 0.70
    assert results[0].metadata["source"] == "chunk_embeddings"


def test_wrong_dimension_and_legacy_source_fail_before_sql():
    with pytest.raises(ValueError, match="dimension"):
        DenseVectorRetriever().build_spec([0.1])
    spec = DenseVectorRetriever().build_spec(VECTOR, filters={"project_id": "p"})
    spec["embedding_table"] = "chunks"
    with pytest.raises(ValueError, match="legacy"):
        dense_sql_from_spec(spec)
