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

from src.config import settings
from src.infrastructure.retrieval.base import (
    FilterTerm,
    RetrievalCandidate,
    filter_spec,
    execute_retrieval_query,
    render_where,
    require_scoped_filters,
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
LEXICAL_STOPWORDS: frozenset = frozenset(
    {
        # English function / question / auxiliary filler words.
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        # Turkish question / connector / filler words.
        "nedir",
        "nasil",
        "nasıl",
        "neler",
        "nelerdir",
        "neden",
        "hangi",
        "kac",
        "kaç",
        "icin",
        "için",
        "ile",
        "ve",
        "bir",
        "bu",
        "su",
        "şu",
        "ne",
    }
)


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
        t
        for t in re.findall(r"\w+", query_text)
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
        t
        for t in re.findall(r"\w+", query_text.lower())
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
        self.candidate_k = (
            candidate_k if candidate_k is not None else settings.LEXICAL_CANDIDATE_K
        )
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
        if '"' in query_text:
            query_function = "phraseto_tsquery"
        elif re.search(r"\bOR\b", query_text, re.IGNORECASE):
            query_function = "websearch_to_tsquery"
        else:
            query_function = "websearch_to_tsquery"
        return {
            "kind": "lexical",
            "ts_config": self.ts_config,
            "query_text": cleaned,
            "query_function": query_function,
            "search_profile": "simple-websearch-v1",
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
        filters = require_scoped_filters(filters)
        spec = self.build_spec(query_text, top_k, filters)
        sql, params = lexical_sql_from_spec(spec)
        session = session or self.session
        if session is None:
            raise ValueError("no database session available for lexical search")
        if hasattr(session, "execute"):
            result = execute_retrieval_query(
                session,
                sql,
                params,
                stage="lexical",
                expected_index="ix_chunks_search_vector_gin",
            )
        else:
            result = session(sql, params)
        return to_candidates(result, source="lexical")


def lexical_sql_from_spec(spec: Dict[str, Any]) -> "tuple[str, Dict[str, Any]]":
    """Build (sql, params) for a lexical spec (pure, deterministic)."""
    chunk_table = spec["chunk_table"]
    c = "c"
    sv = spec["search_vector_column"]
    ts_cfg = spec["ts_config"]
    query_function = spec.get("query_function", "websearch_to_tsquery")
    terms = [FilterTerm(**t) for t in spec["filters"]]
    where_sql, params = render_where(terms, prefix="f")
    clauses = [f"q.query @@ {c}.{sv}", "d.deleted_at IS NULL"]
    if where_sql:
        clauses.append(where_sql)
    params["query_text"] = spec["query_text"]
    params["candidate_k"] = int(spec["candidate_k"])
    params["search_profile"] = spec["search_profile"]

    sql = (
        f"SELECT {c}.id AS chunk_id,\n"
        f"       ts_rank_cd({c}.{sv}, q.query) AS score,\n"
        f"       jsonb_build_object(\n"
        f"         'document_id', {c}.document_id, 'version_id', {c}.version_id,\n"
        f"         'source_file_id', {c}.source_file_id, 'workspace_id', p.workspace_id,\n"
        f"         'project_id', d.project_id, 'embedding_profile_id', v.embedding_profile_id,\n"
        f"         'content_hash', {c}.content_hash, 'classification', d.data_classification,\n"
        f"         'search_profile', {c}.search_profile, 'match_type', :query_form,\n"
        f"         'matched_terms', to_jsonb(regexp_split_to_array(:query_text, '\\s+')),\n"
        f"         'locator', jsonb_strip_nulls(jsonb_build_object(\n"
        f"           'page_start', {c}.page_start, 'page_end', {c}.page_end,\n"
        f"           'line_start', {c}.line_start, 'line_end', {c}.line_end,\n"
        f"           'symbol_name', {c}.symbol_name))) AS metadata\n"
        f"FROM {chunk_table} AS {c}\n"
        f"JOIN documents AS d ON d.id = {c}.document_id\n"
        f"JOIN projects AS p ON p.id = d.project_id\n"
        f"JOIN document_versions AS v ON v.id = {c}.version_id\n"
        f"JOIN content_policy_decisions AS cp ON cp.id = v.content_policy_decision_id\n"
        f"CROSS JOIN LATERAL {query_function}('{ts_cfg}', :query_text) AS q(query)\n"
        f"WHERE {' AND '.join(clauses)}\n"
        f"  AND d.active_version_id = {c}.version_id\n"
        f"  AND v.status IN ('ready','completed')\n"
        f"  AND {c}.search_profile = :search_profile\n"
        f"  AND cp.permit_local_generation IS TRUE\n"
        f"ORDER BY score DESC, {c}.id\n"
        f"LIMIT :candidate_k"
    )
    params["query_form"] = query_function.removesuffix("_to_tsquery")
    return sql, params
