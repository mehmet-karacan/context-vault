"""Exact / trigram identifier retrieval (Aşama 5.2).

``IdentifierRetriever`` matches a query's technical tokens against the
``Chunks.identifiers`` array, the ``symbol_name`` column and the source-file
path (via ``source_files.relative_path``). It is the retriever that rescues
exact-technical questions whose low dense score would otherwise be
insufficient evidence (AKTIF_GOREV.md 5.6). It is *not* governed by a domain
Protocol yet — it is a sibling of ``VectorRetriever`` / ``LexicalRetriever``
with a compatible ``search`` signature plus an explicit ``identifiers``
injection point.

``extract_identifiers`` is a deliberately simple regex/heuristic tokenizer —
not NLP — that pulls table / column / class / method / package / error-code
style tokens out of a query so the identifier index can be searched precisely.
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

# Ordered (name, regex) heuristic patterns. Earlier patterns win on ordering of
# returned tokens (first-match-first is preserved through dedupe).
IDENTIFIER_PATTERNS: List[tuple] = [
    # Package / module / qualified paths and Schema.table, Class.method.
    ("qualified", re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?![A-Za-z0-9_])")),
    # UPPER_SNAKE constants / columns, e.g. PAYMENT_FLAG.
    ("screaming_snake", re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")),
    # PascalCase class / type names, e.g. PaymentService.
    ("pascal_class", re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*){1,}\b")),
    # snake_case method / function / column / table, e.g. calculate_total.
    ("snake_case", re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")),
    # Error-code / numeric-tagged identifiers, e.g. E302, HTTP400, ERR_100 (no underscore).
    ("error_code", re.compile(r"\b[A-Z][A-Z0-9]*\d+[A-Z0-9]*\b")),
    # *Error / *Exception class names.
    ("error_class", re.compile(r"\b[A-Za-z_]\w*(?:Error|Exception)\b")),
    # camelCase method invocation, e.g. get_user_by_id(.
    ("camel_method", re.compile(r"\b[a-z_][a-zA-Z0-9]*(?=\()")),
]


def extract_identifiers(text: str) -> List[str]:
    """Heuristically extract technical identifier tokens from ``text``.

    Returns a de-duplicated list of tokens in first-match order. Only
    identifier-shaped tokens survive (uppercase constants, PascalCase classes,
    snake_case methods, dotted package paths, error codes) — plain natural
    language words are not returned. Deterministic for a given input.
    """
    if not text:
        return []
    seen: set = set()
    out: List[str] = []
    for _name, pattern in IDENTIFIER_PATTERNS:
        for match in pattern.finditer(text):
            token = match.group(0)
            if len(token) < 2:
                continue
            if token in seen:
                continue
            seen.add(token)
            out.append(token)
    return out


class IdentifierRetriever:
    """Exact / substring identifier retriever.

    ``candidate_k`` defaults to ``settings.IDENTIFIER_CANDIDATE_K`` (20) and is
    injectable. If ``identifiers`` is not supplied, they are derived from
    ``query_text`` via ``extract_identifiers``.
    """

    def __init__(
        self,
        candidate_k: Optional[int] = None,
        session: Any = None,
        scope_key: str = "scope",
    ):
        self.candidate_k = (
            candidate_k if candidate_k is not None else settings.IDENTIFIER_CANDIDATE_K
        )
        self.session = session
        self.scope_key = scope_key

    def resolve_k(self, top_k: Optional[int]) -> int:
        if top_k and top_k > 0:
            return top_k
        return self.candidate_k

    def build_spec(
        self,
        identifiers: List[str],
        top_k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Serializable, self-describing identifier search request (pure)."""
        ids = [i for i in identifiers if i]
        return {
            "kind": "identifier",
            "chunk_table": "chunks",
            "identifiers_column": "identifiers",
            "symbol_column": "symbol_name",
            "source_files_table": "source_files",
            "candidate_k": self.resolve_k(top_k),
            "identifiers": ids,
            "filters": filter_spec(filters, scope_key=self.scope_key),
        }

    def search(
        self,
        query_text: str,
        top_k: int,
        filters: Optional[dict] = None,
        identifiers: Optional[List[str]] = None,
        session: Any = None,
    ) -> List[RetrievalCandidate]:
        ids = list(identifiers) if identifiers else extract_identifiers(query_text)
        if not ids:
            return []
        spec = self.build_spec(ids, top_k, filters)
        sql, params = identifier_sql_from_spec(spec)
        session = session or self.session
        if session is None:
            raise ValueError("no database session available for identifier search")
        if hasattr(session, "execute"):
            result = session.execute(text(sql), params).fetchall()
        else:
            result = session(sql, params)
        return to_candidates(result, source="identifier")


def identifier_sql_from_spec(spec: Dict[str, Any]) -> "tuple[str, Dict[str, Any]]":
    """Build (sql, params) for an identifier spec (pure, deterministic)."""
    c = spec["chunk_table"]
    ids_col = spec["identifiers_column"]
    sym = spec["symbol_column"]
    sf = spec["source_files_table"]
    terms = [FilterTerm(**t) for t in spec["filters"]]
    where_f, params = render_where(terms, prefix="f")
    ids = [str(i) for i in spec["identifiers"]]

    params["ids"] = ids
    params["eq_ids"] = [i.lower() for i in ids]
    params["like_ids"] = [f"%{i.lower()}%" for i in ids]

    match_pred = (
        f"({c}.{ids_col} && :ids\n"
        f"        OR lower(coalesce({c}.{sym},'')) = ANY(:eq_ids)\n"
        f"        OR lower(coalesce({c}.{sym},'')) LIKE ANY(:like_ids)\n"
        f"        OR lower(coalesce({sf}.relative_path,'')) LIKE ANY(:like_ids))"
    )
    score_expr = (
        f"GREATEST(\n"
        f"    CASE WHEN {c}.{ids_col} && :ids THEN 1.0 ELSE 0.0 END,\n"
        f"    CASE WHEN lower(coalesce({c}.{sym},'')) = ANY(:eq_ids) THEN 0.9 ELSE 0.0 END,\n"
        f"    CASE WHEN lower(coalesce({c}.{sym},'')) LIKE ANY(:like_ids) THEN 0.6 ELSE 0.0 END,\n"
        f"    CASE WHEN lower(coalesce({sf}.relative_path,'')) LIKE ANY(:like_ids) THEN 0.6 ELSE 0.0 END"
        f"\n) AS score"
    )
    clauses = [match_pred]
    if where_f:
        clauses.append(where_f)
    params["candidate_k"] = int(spec["candidate_k"])

    sql = (
        f"SELECT {c}.id AS chunk_id,\n"
        f"       {score_expr}\n"
        f"FROM {c}\n"
        f"JOIN documents AS d ON d.id = {c}.document_id\n"
        f"LEFT JOIN {sf} ON {sf}.id = {c}.source_file_id\n"
        f"WHERE {' AND '.join(clauses)}\n"
        f"ORDER BY score DESC, {c}.id\n"
        f"LIMIT :candidate_k"
    )
    return sql, params
