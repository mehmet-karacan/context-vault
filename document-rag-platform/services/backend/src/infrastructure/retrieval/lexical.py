"""PostgreSQL full-text lexical retrieval (Aşama 5.2).

``LexicalRetriever`` implements ``domain.ports.LexicalRetriever``: full-text
search over ``Chunks.search_vector`` using the ``simple`` text-search config
so technical identifiers are not broken by stemming (AKTIF_GOREV.md 5.2).
Ranking uses Postgres ``ts_rank_cd``; the ``simple`` config also avoids
language-specific thesaurus issues.

DB-free testability mirrors ``dense.py``: ``build_spec`` is a pure function
returning a serializable spec (query text, config, candidate count, filters),
and ``lexical_sql_from_spec`` turns it into a deterministic parameterized SQL
string. ``search`` only executes that SQL against a supplied session.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text

from src.config import settings
from src.infrastructure.retrieval.base import (
    FilterTerm,
    RetrievalCandidate,
    filter_spec,
    render_where,
    to_candidates,
)

DEFAULT_TEXT_SEARCH_CONFIG: str = "simple"


class LexicalRetriever:
    """Full-text retriever over ``chunks.search_vector``.

    ``candidate_k`` defaults to ``settings.LEXICAL_CANDIDATE_K`` (40) and is
    injectable. ``ts_config`` selects the text-search dictionary (default
    ``simple`` so ``PAYMENT_FLAG``-style identifiers survive unstemmed).
    ``scope_key`` is the filters key carrying the ``all|documents|images|code``
    shortcut shared with the other retrievers.
    """

    def __init__(
        self,
        candidate_k: Optional[int] = None,
        ts_config: str = DEFAULT_TEXT_SEARCH_CONFIG,
        session: Any = None,
        scope_key: str = "scope",
    ):
        self.candidate_k = candidate_k if candidate_k is not None else settings.LEXICAL_CANDIDATE_K
        self.ts_config = ts_config
        self.session = session
        self.scope_key = scope_key

    def resolve_k(self, top_k: Optional[int]) -> int:
        if top_k and top_k > 0:
            return top_k
        return self.candidate_k

    def build_spec(
        self,
        query_text: str,
        top_k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Serializable, self-describing lexical search request (pure)."""
        return {
            "kind": "lexical",
            "ts_config": self.ts_config,
            "query_text": query_text,
            "chunk_table": "chunks",
            "search_vector_column": "search_vector",
            "candidate_k": self.resolve_k(top_k),
            "filters": filter_spec(filters, scope_key=self.scope_key),
        }

    def search(
        self,
        query_text: str,
        top_k: int,
        filters: Optional[dict] = None,
        session: Any = None,
    ) -> List[RetrievalCandidate]:
        spec = self.build_spec(query_text, top_k, filters)
        sql, params = lexical_sql_from_spec(spec)
        session = session or self.session
        if session is None:
            raise ValueError("no database session available for lexical search")
        if hasattr(session, "execute"):
            result = session.execute(text(sql), params).fetchall()
        else:
            result = session(sql, params)
        return to_candidates(result, source="lexical")


def lexical_sql_from_spec(spec: Dict[str, Any]) -> "tuple[str, Dict[str, Any]]":
    """Build (sql, params) for a lexical spec (pure, deterministic)."""
    c = spec["chunk_table"]
    sv = spec["search_vector_column"]
    ts_cfg = spec["ts_config"]
    terms = [FilterTerm(**t) for t in spec["filters"]]
    where_sql, params = render_where(terms, prefix="f")
    clauses = [f"query @@ {c}.{sv}"]
    if where_sql:
        clauses.append(where_sql)
    params["query_text"] = spec["query_text"]
    params["candidate_k"] = int(spec["candidate_k"])

    sql = (
        f"SELECT {c}.id AS chunk_id,\n"
        f"       ts_rank_cd({c}.{sv}, query) AS score\n"
        f"FROM {c}\n"
        f"JOIN documents AS d ON d.id = {c}.document_id,\n"
        f"     plainto_tsquery('{ts_cfg}', :query_text) AS query\n"
        f"WHERE {' AND '.join(clauses)}\n"
        f"ORDER BY score DESC, {c}.id\n"
        f"LIMIT :candidate_k"
    )
    return sql, params
