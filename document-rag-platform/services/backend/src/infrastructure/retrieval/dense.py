"""pgvector dense retrieval (Aşama 5.1).

``DenseVectorRetriever`` implements ``domain.ports.VectorRetriever``: cosine
vector search over ``ChunkEmbedding.embedding`` (or, configurably, the legacy
``Chunks.embedding`` column), returning candidate chunks with a dense score.

DB-free testability: everything that matters for correctness — candidate count
(``candidate_k``, injectable override), HNSW ``ef_search``, distance metric and
the applied filters — is captured by the pure ``build_spec`` /
``dense_sql_from_spec`` / ``to_candidates`` functions. ``search`` is a thin
adapter that turns the spec into a parameterized SQL string and executes it
against a supplied session; unit tests assert on the spec + generated SQL
without any live database.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from sqlalchemy import text

from src.config import settings
from src.infrastructure.retrieval.base import (
    FilterTerm,
    RetrievalCandidate,
    filter_spec,
    render_where,
    to_candidates,
)

# pgvector's HNSW ``ef_search`` GUC is a per-session knob, not a per-query
# parameter; we surface it in the spec so an adapter may set it on the session
# before execution. Default matching a sensible recall/latency trade-off.
DEFAULT_HNSW_EF_SEARCH: int = 40


class DenseVectorRetriever:
    """Cosine pgvector retriever.

    ``candidate_k`` defaults to ``settings.VECTOR_CANDIDATE_K`` (40) and is
    injectable (constructor override). ``ef_search`` configures the HNSW
    ``ef_search`` knob (surfaced in the spec). The embedding source table is
    ``chunk_embeddings``/``embedding`` by default (Bölüm 8.9) and can point at
    the legacy ``chunks.embedding`` for transition periods.
    """

    def __init__(
        self,
        candidate_k: Optional[int] = None,
        ef_search: Optional[int] = None,
        session: Any = None,
        table: str = "chunk_embeddings",
        join_table: str = "chunks",
        chunk_id_column: str = "chunk_id",
        vector_column: str = "embedding",
        distance: str = "cosine",
        scope_key: str = "scope",
    ):
        self.candidate_k = candidate_k if candidate_k is not None else settings.VECTOR_CANDIDATE_K
        self.ef_search = ef_search if ef_search is not None else DEFAULT_HNSW_EF_SEARCH
        self.session = session
        self.table = table
        self.join_table = join_table
        self.chunk_id_column = chunk_id_column
        self.vector_column = vector_column
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
        spec = self.build_spec(query_embedding, top_k, filters)
        primary = self._search_spec(spec, session, source_tag=spec["embedding_table"])

        # Legacy dense source merge. The canonical source is the versioned
        # ``chunk_embeddings`` table (Bölüm 8.9), but the synchronous upload
        # path (and any deployment before the versioned schema) writes the
        # dense vector into the HNSW-indexed ``chunks.embedding`` column and
        # leaves ``chunk_embeddings`` empty for those chunks. Coverage can be
        # PARTIAL across the two sources (some documents in one, some in the
        # other), so a mere empty-result fallback would silently mask chunks
        # that live only in ``chunks.embedding``. Instead we query BOTH sources
        # and merge their candidate sets by ``chunk_id`` (union + dedup, keeping
        # the higher score), tagging which physical source produced each hit.
        # This does NOT run when the retriever is already pointed at ``chunks``
        # (avoids an infinite self-merge).
        if self.table != "chunks":
            legacy = legacy_spec_from_spec(spec)
            legacy_candidates = self._search_spec(legacy, session, source_tag="chunks.embedding")
            return merge_dense_candidates(
                primary, legacy_candidates, int(spec["candidate_k"])
            )
        return primary

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
            result = session.execute(text(sql), params).fetchall()
        else:
            result = session(sql, params)
        candidates = to_candidates(result, source="dense")
        if source_tag:
            for c in candidates:
                c.metadata["source"] = source_tag
        return candidates


def dense_sql_from_spec(spec: Dict[str, Any]) -> "tuple[str, Dict[str, Any]]":
    """Build (sql, params) for a dense spec (pure, deterministic)."""
    ce = spec["embedding_table"]
    c = spec["join_table"]
    vec = spec["vector_column"]
    cid = spec["chunk_id_column"]
    terms = [FilterTerm(**t) for t in spec["filters"]]
    where_sql, params = render_where(terms, prefix="f")
    if where_sql:
        where_sql = f"WHERE {where_sql}"
    params["query_embedding"] = list(spec["query_embedding"])
    params["candidate_k"] = int(spec["candidate_k"])

    if ce == c:
        # Legacy layout: the dense vector lives directly on the chunk row
        # (``chunks.embedding``), so there is no separate embedding table to
        # join; the chunk table carries BOTH the id and the vector. The chunk
        # table is aliased ``c`` so chunk-level filters (rendered against the
        # ``c`` alias) resolve, and rows without a legacy embedding are
        # excluded (sync-path chunks may leave ``chunks.embedding`` NULL).
        filter_sql, filter_params = render_where(terms, prefix="f")
        params = {
            "query_embedding": list(spec["query_embedding"]),
            "candidate_k": int(spec["candidate_k"]),
        }
        params.update(filter_params)
        where_parts = ["c.embedding IS NOT NULL"]
        if filter_sql:
            where_parts.append(filter_sql)
        sql = (
            f"SELECT c.{cid} AS chunk_id,\n"
            f"       1 - (c.{vec} <=> CAST(:query_embedding AS vector)) AS score\n"
            f"FROM {c} AS c\n"
            f"JOIN documents AS d ON d.id = c.document_id\n"
            f"WHERE {' AND '.join(where_parts)}\n"
            f"ORDER BY c.{vec} <=> CAST(:query_embedding AS vector)\n"
            f"LIMIT :candidate_k"
        )
    else:
        sql = (
            f"SELECT {ce}.{cid} AS chunk_id,\n"
            f"       1 - ({ce}.{vec} <=> CAST(:query_embedding AS vector)) AS score\n"
            f"FROM {ce}\n"
            f"JOIN {c} ON {c}.id = {ce}.{cid}\n"
            f"JOIN documents AS d ON d.id = {c}.document_id\n"
            f"{where_sql}\n"
            f"ORDER BY {ce}.{vec} <=> CAST(:query_embedding AS vector)\n"
            f"LIMIT :candidate_k"
        )
    return sql, params


def legacy_spec_from_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Derive the legacy ``chunks.embedding`` spec from a primary spec (pure).

    Mirrors the mapping ``search`` applies so it can be asserted DB-free: the
    dense vector is read straight off the ``chunks`` row via the HNSW-indexed
    ``chunks.embedding`` column.
    """
    legacy = dict(spec)
    legacy.update(
        {
            "embedding_table": "chunks",
            "join_table": "chunks",
            "chunk_id_column": "id",
            "vector_column": "embedding",
        }
    )
    return legacy


def merge_dense_candidates(
    primary: List[RetrievalCandidate],
    legacy: List[RetrievalCandidate],
    candidate_k: int,
) -> List[RetrievalCandidate]:
    """Merge primary + legacy dense candidate sets by ``chunk_id`` (pure).

    The two sources may each expose a different subset of chunks (partial
    coverage), so both must be unioned — never masked. Dedup keeps a single
    entry per ``chunk_id`` and, when a chunk is present in both, the higher
    score (and the metadata/source tag of whichever source produced it)
    deterministically. The merged list is re-ranked by descending score and
    truncated to ``candidate_k`` so the returned count stays bounded.
    """
    best: Dict[str, RetrievalCandidate] = {}
    for cand in list(primary) + list(legacy):
        existing = best.get(cand.chunk_id)
        if existing is None or cand.score > existing.score:
            best[cand.chunk_id] = cand
    ranked = sorted(best.values(), key=lambda c: c.score, reverse=True)[:candidate_k]
    return [
        RetrievalCandidate(
            chunk_id=c.chunk_id,
            rank=rank,
            score=c.score,
            source=c.source,
            metadata=dict(c.metadata),
        )
        for rank, c in enumerate(ranked, start=1)
    ]
