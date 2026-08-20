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

import re
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

#: Cross-lingual filler / question / connector tokens that are almost never
#: meaningful content terms in the (largely Turkish) document corpus. They are
#: stripped from the FTS query so a real content term is not silently vetoed by
#: an English/Turkish filler word that can never occur in the source text.
#: Example: "what is stp?" -> plainto_tsquery('simple', 'what is stp') would be
#: ``'what' & 'is' & 'stp'`` — all three must co-occur, but "what"/"is" never
#: appear in Turkish content, so the meaningful term "stp" never matches even
#: though it is present in the corpus. Dropping the fillers keeps the remaining
#: significant terms (here just "stp") as the FTS query.
LEXICAL_STOPWORDS: frozenset = frozenset({
    # English function / question / auxiliary filler words.
    "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does",
    "for", "from", "how", "in", "into", "is", "it", "its", "of", "on", "or",
    "that", "the", "this", "to", "was", "were", "what", "when", "where",
    "which", "who", "why", "with",
    # Turkish question / connector / filler words.
    "nedir", "nasil", "nasıl", "neler", "nelerdir", "neden", "hangi", "kac", "kaç",
    "icin", "için", "ile", "ve", "bir", "bu", "su", "şu", "ne",
})


def filter_query_terms(query_text: str) -> str:
    """Return ``query_text`` with cross-lingual filler tokens removed (pure).

    Tokenizes on word characters, keeps only tokens that are not
    :data:`LEXICAL_STOPWORDS` and are longer than one character, and rejoins
    with a single space. Non-filler input is returned effectively unchanged
    (e.g. ``"PAYMENT_FLAG"`` -> ``"PAYMENT_FLAG"``). Empty result collapses to
    the original text so a stopword-only query still follows the same path.
    """
    if not query_text:
        return query_text
    tokens = [
        t for t in re.findall(r"\w+", query_text)
        if len(t) > 1 and t.lower() not in LEXICAL_STOPWORDS
    ]
    if not tokens:
        return query_text
    return " ".join(tokens)


def significant_query_terms(query_text: str) -> "List[str]":
    """Significant content terms of a query, lower-cased and de-duplicated.

    Returns the stopword-filtered word tokens (see :data:`LEXICAL_STOPWORDS`)
    plus the space-collapsed joins of adjacent letter-only token pairs, so a
    split acronym in the query ("ttnet sis") also yields the contiguous form
    that actually appears in the source text ("ttnetsis"). Pure and
    deterministic; used by the retrieval layer for the content-verified term
    presence signal.
    """
    if not query_text:
        return []
    tokens = [
        t for t in re.findall(r"\w+", query_text.lower())
        if len(t) > 1 and t not in LEXICAL_STOPWORDS
    ]
    if not tokens:
        return []
    terms: "List[str]" = list(dict.fromkeys(tokens))
    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        if a.isalpha() and b.isalpha():
            joined = a + b
            if joined not in terms:
                terms.append(joined)
    return [t for t in terms if len(t) > 1]


def content_has_any_term(content: str, terms: "List[str]") -> bool:
    """True if ``content`` (case-insensitive) contains any of ``terms`` (pure).

    A blunt but content-verified substring presence check: if a significant
    query term literally appears in the chunk text, the chunk is genuine
    evidence for that term — regardless of how the AND-based full-text query
    tokenized/acronym-split the caller's phrasing. Empty content or an empty
    term list yields ``False``.
    """
    if not content or not terms:
        return False
    lowered = content.lower()
    return any(t in lowered for t in terms)


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
        """Serializable, self-describing lexical search request (pure).

        ``query_text`` is first stripped of cross-lingual filler tokens
        (``filter_query_terms``) so meaningful content terms survive even when
        surrounded by English/Turkish question fillers that the ``simple``
        text-search config would otherwise AND into the query and block.
        """
        cleaned = filter_query_terms(query_text)
        return {
            "kind": "lexical",
            "ts_config": self.ts_config,
            "query_text": cleaned,
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
