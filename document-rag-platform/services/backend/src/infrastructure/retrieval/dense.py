"""pgvector dense retrieval (Aşama 5.1).

``DenseVectorRetriever`` implements ``domain.ports.VectorRetriever``: cosine
vector search over the versioned ``ChunkEmbedding.embedding`` table.

DB-free testability: everything that matters for correctness — candidate count
(``candidate_k``, injectable override), HNSW ``ef_search``, distance metric and
the applied filters — is captured by the pure ``build_spec`` /
``dense_sql_from_spec`` / ``to_candidates`` functions. ``search`` is a thin
adapter that turns the spec into a parameterized SQL string and executes it
against a supplied session; unit tests assert on the spec + generated SQL
without any live database.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from src.config import settings
from src.models import EMBEDDING_DIMENSION
from src.infrastructure.retrieval.base import (
    FilterTerm,
    RetrievalCandidate,
    filter_spec,
    execute_retrieval_query,
    render_where,
    require_scoped_filters,
    to_candidates,
)

# pgvector's HNSW ``ef_search`` GUC is a per-session knob, not a per-query
# parameter; we surface it in the spec so an adapter may set it on the session
# before execution. Default matching a sensible recall/latency trade-off.
DEFAULT_HNSW_EF_SEARCH: int = 20


class DenseVectorRetriever:
    """Cosine pgvector retriever.

    ``candidate_k`` defaults to ``settings.VECTOR_CANDIDATE_K`` (40) and is
    injectable (constructor override). ``ef_search`` configures the HNSW
    ``ef_search`` knob (surfaced in the spec). The only embedding authority is
    profile-bound ``chunk_embeddings.embedding`` (Bölüm 8.9).
    """

    def __init__(
        self,
        candidate_k: Optional[int] = None,
        ef_search: Optional[int] = None,
        session: Any = None,
        distance: str = "cosine",
        scope_key: str = "scope",
    ):
        self.candidate_k = (
            candidate_k if candidate_k is not None else settings.VECTOR_CANDIDATE_K
        )
        self.ef_search = ef_search if ef_search is not None else DEFAULT_HNSW_EF_SEARCH
        self.session = session
        self.table = "chunk_embeddings"
        self.join_table = "chunks"
        self.chunk_id_column = "chunk_id"
        self.vector_column = "embedding"
        self.distance = distance
        self.scope_key = scope_key

    # --- pure query construction (unit test surface) -------------------------

    def resolve_k(self, top_k: Optional[int]) -> int:
        """Effective candidate count: caller ``top_k`` if given, else config."""
        if top_k and top_k > 0:
            return top_k
        return self.candidate_k

    def build_spec(
        self,
        query_embedding: List[float],
        top_k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Serializable, self-describing dense search request (pure)."""
        if len(query_embedding) != EMBEDDING_DIMENSION:
            raise ValueError(f"query embedding dimension must be {EMBEDDING_DIMENSION}")
        k = self.resolve_k(top_k)
        return {
            "kind": "dense",
            "embedding_table": self.table,
            "join_table": self.join_table,
            "chunk_id_column": self.chunk_id_column,
            "vector_column": self.vector_column,
            "distance_metric": self.distance,
            "query_embedding": list(query_embedding),
            "candidate_k": k,
            "hnsw": {"ef_search": self.ef_search},
            "filters": filter_spec(filters, scope_key=self.scope_key),
        }

    # --- protocol conformance -------------------------------------------------

    def search(
        self,
        query_embedding: List[float],
        top_k: int,
        filters: Optional[dict] = None,
        session: Any = None,
    ) -> List[RetrievalCandidate]:
        filters = require_scoped_filters(filters)
        spec = self.build_spec(query_embedding, top_k, filters)
        # The removed legacy ``chunks.embedding`` column is never queried.
        return self._search_spec(spec, session, source_tag="chunk_embeddings")

    def _search_spec(
        self,
        spec: Dict[str, Any],
        session: Any = None,
        source_tag: Optional[str] = None,
    ) -> List[RetrievalCandidate]:
        sql, params = dense_sql_from_spec(spec)
        session = session or self.session
        if session is None:
            raise ValueError("no database session available for dense search")
        if hasattr(session, "execute"):
            session.execute(
                text("SELECT set_config('hnsw.ef_search', :ef, true)"),
                {"ef": str(int(spec["hnsw"]["ef_search"]))},
            )
            result = execute_retrieval_query(
                session,
                sql,
                params,
                stage="dense",
                expected_index="ix_chunk_embeddings_embedding_hnsw",
            )
        else:
            result = session(sql, params)
        candidates = to_candidates(result, source="dense")
        if source_tag:
            candidates = [
                replace(c, metadata={**dict(c.metadata), "source": source_tag})
                for c in candidates
            ]
        return candidates


def dense_sql_from_spec(spec: Dict[str, Any]) -> "tuple[str, Dict[str, Any]]":
    """Build (sql, params) for a dense spec (pure, deterministic)."""
    if spec.get("embedding_table") != "chunk_embeddings":
        raise ValueError("legacy dense embedding sources are not supported")
    terms = [FilterTerm(**t) for t in spec["filters"]]
    where_sql, params = render_where(terms, prefix="f")
    where_sql = (
        f"WHERE d.deleted_at IS NULL AND {where_sql}"
        if where_sql
        else "WHERE d.deleted_at IS NULL"
    )
    params["query_embedding"] = list(spec["query_embedding"])
    params["candidate_k"] = int(spec["candidate_k"])

    sql = (
        "SELECT c.id AS chunk_id,\n"
        "       1 - (ce.embedding <=> CAST(:query_embedding AS vector)) AS score,\n"
        "       jsonb_build_object(\n"
        "         'document_id', c.document_id, 'version_id', c.version_id,\n"
        "         'source_file_id', c.source_file_id, 'workspace_id', p.workspace_id,\n"
        "         'project_id', d.project_id, 'embedding_profile_id', ce.embedding_profile_id,\n"
        "         'content_hash', c.content_hash, 'classification', d.data_classification,\n"
        "         'search_profile', c.search_profile, 'match_type', 'semantic',\n"
        "         'matched_terms', '[]'::jsonb,\n"
        "         'locator', jsonb_strip_nulls(jsonb_build_object(\n"
        "           'page_start', c.page_start, 'page_end', c.page_end,\n"
        "           'line_start', c.line_start, 'line_end', c.line_end,\n"
        "           'symbol_name', c.symbol_name))) AS metadata\n"
        "FROM chunk_embeddings AS ce\n"
        "JOIN chunks AS c ON c.id = ce.chunk_id\n"
        "JOIN documents AS d ON d.id = c.document_id\n"
        "JOIN projects AS p ON p.id = d.project_id\n"
        "JOIN document_versions AS v ON v.id = c.version_id\n"
        "JOIN content_policy_decisions AS cp ON cp.id = v.content_policy_decision_id\n"
        f"{where_sql} AND d.active_version_id = c.version_id\n"
        "  AND v.status IN ('ready','completed')\n"
        "  AND cp.permit_local_generation IS TRUE\n"
        "  AND ce.embedding_profile_id = v.embedding_profile_id\n"
        "ORDER BY ce.embedding <=> CAST(:query_embedding AS vector)\n"
        "LIMIT :candidate_k"
    )
    return sql, params
