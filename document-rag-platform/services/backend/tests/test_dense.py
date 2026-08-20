"""Aşama 5.1: unit tests for DenseVectorRetriever (DB-free).

Exercises candidate count (config default + injectable override), configurable
HNSW ``ef_search``, filter normalization (project/document/version/source
type/scope) inside the generated spec + SQL, and the result shape via a fake
session — no live PostgreSQL required.
"""

from __future__ import annotations

from src.infrastructure.retrieval import DenseVectorRetriever, dense_sql_from_spec
from src.infrastructure.retrieval.base import normalize_filters
from src.infrastructure.retrieval.dense import legacy_spec_from_spec, merge_dense_candidates
from src.infrastructure.retrieval.base import RetrievalCandidate


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
    assert "1 - (chunk_embeddings.embedding <=> CAST(:query_embedding AS vector)) AS score" in sql
    assert "ORDER BY chunk_embeddings.embedding <=> CAST(:query_embedding AS vector)" in sql
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


def test_chunks_embedding_only_chunk_is_not_masked_by_unrelated_primary_row():
    # The masking bug: chunk_embeddings has an unrelated single row (so the
    # primary is never empty), while the query's nearest chunk lives ONLY in the
    # legacy chunks.embedding column. Before hardening, the empty-only fallback
    # never fired and the legacy chunk was silently masked -> no dense evidence.
    session = SourceAwareFakeSession(
        chunk_embeddings_rows=[_Row("unrelated-1", 0.50)],
        chunks_rows=[_Row("legacy-nearest-1", 0.95)],
    )
    retriever = DenseVectorRetriever(session=session)
    results = retriever.search([0.1, 0.2], top_k=5)

    ids = [c.chunk_id for c in results]
    assert "legacy-nearest-1" in ids, "legacy-only chunk must not be masked"
    # Highest-scoring (the legacy nearest) ranks first; both sources merged.
    assert ids[0] == "legacy-nearest-1"
    by_id = {c.chunk_id: c for c in results}
    assert by_id["legacy-nearest-1"].metadata["source"] == "chunks.embedding"
    assert by_id["unrelated-1"].metadata["source"] == "chunk_embeddings"
    assert by_id["legacy-nearest-1"].score == 0.95


def test_chunk_in_both_sources_merged_once_keeps_higher_score():
    # A chunk present in BOTH sources must appear exactly once, retaining the
    # higher score and the source tag of whichever source produced that score.
    session = SourceAwareFakeSession(
        chunk_embeddings_rows=[_Row("dup-1", 0.70)],
        chunks_rows=[_Row("dup-1", 0.92)],
    )
    retriever = DenseVectorRetriever(session=session)
    results = retriever.search([0.1, 0.2], top_k=5)

    assert [c.chunk_id for c in results] == ["dup-1"]
    assert results[0].score == 0.92
    assert results[0].metadata["source"] == "chunks.embedding"


def test_candidate_k_bounds_merged_result():
    # Merging both sources must never exceed candidate_k, and the kept set is
    # the highest-scoring union (dedup by chunk_id).
    primary = [RetrievalCandidate(chunk_id=f"p{i}", rank=i, score=0.9 - i * 0.01) for i in range(5)]
    legacy = [RetrievalCandidate(chunk_id=f"l{i}", rank=i, score=0.5 - i * 0.01) for i in range(5)]
    merged = merge_dense_candidates(primary, legacy, candidate_k=7)
    assert len(merged) <= 7
    assert [c.chunk_id for c in merged] == [
        "p0", "p1", "p2", "p3", "p4", "l0", "l1"
    ]
    assert [c.rank for c in merged] == list(range(1, 8))
    # Solely legacy overlaps (candidate appears in both) still dedupes to one.
    dup = [
        RetrievalCandidate(chunk_id="x", rank=1, score=0.8),
        RetrievalCandidate(chunk_id="x", rank=1, score=0.9),
    ]
    one = merge_dense_candidates([dup[0]], [dup[1]], candidate_k=5)
    assert len(one) == 1 and one[0].chunk_id == "x" and one[0].score == 0.9


def test_legacy_spec_and_sql_carry_both_sources():
    # The spec carries the canonical source and derives a legacy spec; the
    # emitted legacy SQL reads the HNSW-indexed chunks.embedding column, joined
    # to documents, with legacy-only rows (NULL embedding) excluded.
    spec = DenseVectorRetriever().build_spec(
        [0.1, 0.2], top_k=3, filters={"document_ids": ["docA"]}
    )
    assert spec["embedding_table"] == "chunk_embeddings"
    legacy = legacy_spec_from_spec(spec)
    assert legacy["embedding_table"] == "chunks"
    assert legacy["vector_column"] == "embedding"
    assert legacy["chunk_id_column"] == "id"

    sql, params = dense_sql_from_spec(legacy)
    assert "FROM chunks AS c" in sql
    # After ``FROM chunks AS c`` the original table name is unusable, so the
    # SELECT-list and ORDER BY must reference the ``c`` alias for id/embedding.
    assert "SELECT c.id AS chunk_id," in sql
    assert "1 - (c.embedding <=> CAST(:query_embedding AS vector)) AS score" in sql
    assert "ORDER BY c.embedding <=> CAST(:query_embedding AS vector)" in sql
    assert "chunks.embedding" not in sql, "SELECT/ORDER BY must use alias c, not chunks."
    assert "JOIN documents AS d ON d.id = c.document_id" in sql
    assert "c.embedding IS NOT NULL" in sql
    assert "c.document_id IN" in sql
    assert "LIMIT :candidate_k" in sql
    assert params["candidate_k"] == 3
