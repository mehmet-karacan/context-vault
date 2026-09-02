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

# Ordered (name, regex) heuristic patterns. Earlier patterns win on ordering of
# returned tokens (first-match-first is preserved through dedupe).
IDENTIFIER_PATTERNS: List[tuple] = [
    # Package / module / qualified paths and Schema.table, Class.method.
    (
        "qualified",
        re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?![A-Za-z0-9_])"),
    ),
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
        allow_substring: bool = False,
    ):
        self.candidate_k = (
            candidate_k if candidate_k is not None else settings.IDENTIFIER_CANDIDATE_K
        )
        self.session = session
        self.scope_key = scope_key
        self.allow_substring = allow_substring

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
            "allow_substring": self.allow_substring,
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
        filters = require_scoped_filters(filters)
        ids = list(identifiers) if identifiers else extract_identifiers(query_text)
        if not ids:
            return []
        spec = self.build_spec(ids, top_k, filters)
        sql, params = identifier_sql_from_spec(spec)
        session = session or self.session
        if session is None:
            raise ValueError("no database session available for identifier search")
        if hasattr(session, "execute"):
            result = execute_retrieval_query(
                session,
                sql,
                params,
                stage="identifier",
                expected_index="ix_chunks_symbol_name_trgm",
            )
        else:
            result = session(sql, params)
        return to_candidates(result, source="identifier")


def identifier_sql_from_spec(spec: Dict[str, Any]) -> "tuple[str, Dict[str, Any]]":
    """Build (sql, params) for an identifier spec (pure, deterministic)."""
    chunk_table = spec["chunk_table"]
    c = "c"
    ids_col = spec["identifiers_column"]
    sym = spec["symbol_column"]
    source_files_table = spec["source_files_table"]
    sf = "sf"
    qualified_columns = (
        f"{c}.symbol_qualified_name",
        f"{c}.package_name",
        f"{c}.schema_name",
        f"{c}.table_name",
        f"{c}.column_name",
    )
    terms = [FilterTerm(**t) for t in spec["filters"]]
    where_f, params = render_where(terms, prefix="f")
    ids = [str(i) for i in spec["identifiers"]]

    params["ids"] = ids
    params["eq_ids"] = [i.lower() for i in ids]
    escaped = [
        i.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        for i in ids
    ]
    params["prefix_ids"] = [f"{i}%" for i in escaped]
    params["substring_ids"] = [f"%{i}%" for i in escaped]
    params["fuzzy_id"] = ids[0].lower()
    normalized_array_exact = (
        f"EXISTS (SELECT 1 FROM unnest(coalesce({c}.{ids_col}, ARRAY[]::text[])) AS ident "
        f"WHERE lower(ident) = ANY(:eq_ids))"
    )
    qualified_exact = " OR ".join(
        f"lower(coalesce({column},'')) = ANY(:eq_ids)" for column in qualified_columns
    )
    qualified_prefix = " OR ".join(
        f"lower(coalesce({column},'')) LIKE ANY(:prefix_ids)"
        for column in qualified_columns
    )
    substring_pred = (
        f"lower(coalesce({c}.{sym},'')) LIKE ANY(:substring_ids)"
        if spec.get("allow_substring")
        else "FALSE"
    )

    match_parts = [
        f"{c}.{ids_col} && CAST(:ids AS text[])",
        normalized_array_exact,
        f"lower(coalesce({c}.{sym},'')) = ANY(:eq_ids)",
        qualified_exact,
        f"lower(coalesce({c}.{sym},'')) LIKE ANY(:prefix_ids)",
        qualified_prefix,
        f"lower(coalesce({sf}.relative_path,'')) LIKE ANY(:prefix_ids)",
        f"similarity(lower(coalesce({c}.{sym},'')), :fuzzy_id) >= 0.35",
        f"similarity(lower(coalesce({sf}.relative_path,'')), :fuzzy_id) >= 0.35",
    ]
    if spec.get("allow_substring"):
        match_parts.append(substring_pred)
    match_pred = "(\n        " + "\n        OR ".join(match_parts) + ")"
    score_parts = [
        f"CASE WHEN {c}.{ids_col} && CAST(:ids AS text[]) THEN 1.0 ELSE 0.0 END",
        f"CASE WHEN {normalized_array_exact} THEN 0.95 ELSE 0.0 END",
        f"CASE WHEN lower(coalesce({c}.{sym},'')) = ANY(:eq_ids) THEN 0.9 ELSE 0.0 END",
        f"CASE WHEN {qualified_exact} THEN 0.85 ELSE 0.0 END",
        f"CASE WHEN lower(coalesce({c}.{sym},'')) LIKE ANY(:prefix_ids) THEN 0.75 ELSE 0.0 END",
        f"CASE WHEN {qualified_prefix} THEN 0.72 ELSE 0.0 END",
        f"CASE WHEN lower(coalesce({sf}.relative_path,'')) LIKE ANY(:prefix_ids) THEN 0.70 ELSE 0.0 END",
        f"similarity(lower(coalesce({c}.{sym},'')), :fuzzy_id) * 0.65",
        f"similarity(lower(coalesce({sf}.relative_path,'')), :fuzzy_id) * 0.60",
    ]
    if spec.get("allow_substring"):
        score_parts.append(f"CASE WHEN {substring_pred} THEN 0.50 ELSE 0.0 END")
    score_expr = "GREATEST(\n    " + ",\n    ".join(score_parts) + "\n) AS score"
    match_type_cases = (
        f"           WHEN {c}.{ids_col} && CAST(:ids AS text[]) THEN 'exact'\n"
        f"           WHEN {normalized_array_exact} THEN 'normalized_exact'\n"
        f"           WHEN lower(coalesce({c}.{sym},'')) = ANY(:eq_ids) THEN 'normalized_exact'\n"
        f"           WHEN {qualified_exact} THEN 'qualified_exact'\n"
        f"           WHEN lower(coalesce({c}.{sym},'')) LIKE ANY(:prefix_ids) THEN 'prefix'\n"
        f"           WHEN {qualified_prefix} THEN 'qualified_prefix'\n"
        f"           WHEN lower(coalesce({sf}.relative_path,'')) LIKE ANY(:prefix_ids) THEN 'path_prefix'\n"
    )
    if spec.get("allow_substring"):
        match_type_cases += f"           WHEN {substring_pred} THEN 'substring'\n"
    clauses = [match_pred, "d.deleted_at IS NULL"]
    if where_f:
        clauses.append(where_f)
    params["candidate_k"] = int(spec["candidate_k"])

    sql = (
        f"SELECT {c}.id AS chunk_id,\n"
        f"       {score_expr},\n"
        f"       jsonb_build_object(\n"
        f"         'document_id', {c}.document_id, 'version_id', {c}.version_id,\n"
        f"         'source_file_id', {c}.source_file_id, 'workspace_id', p.workspace_id,\n"
        f"         'project_id', d.project_id, 'embedding_profile_id', v.embedding_profile_id,\n"
        f"         'content_hash', {c}.content_hash, 'classification', d.data_classification,\n"
        f"         'search_profile', {c}.search_profile,\n"
        f"         'match_type', CASE\n"
        f"{match_type_cases}"
        f"           ELSE 'trigram' END,\n"
        f"         'matched_terms', to_jsonb(CAST(:ids AS text[])),\n"
        f"         'locator', jsonb_strip_nulls(jsonb_build_object(\n"
        f"           'file_path', {sf}.relative_path, 'line_start', {c}.line_start,\n"
        f"           'line_end', {c}.line_end, 'symbol_name', {c}.symbol_name,\n"
        f"           'symbol_qualified_name', {c}.symbol_qualified_name,\n"
        f"           'package_name', {c}.package_name, 'schema_name', {c}.schema_name,\n"
        f"           'table_name', {c}.table_name, 'column_name', {c}.column_name))) AS metadata\n"
        f"FROM {chunk_table} AS {c}\n"
        f"JOIN documents AS d ON d.id = {c}.document_id\n"
        f"JOIN projects AS p ON p.id = d.project_id\n"
        f"JOIN document_versions AS v ON v.id = {c}.version_id\n"
        f"JOIN content_policy_decisions AS cp ON cp.id = v.content_policy_decision_id\n"
        f"LEFT JOIN {source_files_table} AS {sf} ON {sf}.id = {c}.source_file_id\n"
        f"WHERE {' AND '.join(clauses)}\n"
        f"  AND d.active_version_id = {c}.version_id\n"
        f"  AND v.status IN ('ready','completed')\n"
        f"  AND cp.permit_local_generation IS TRUE\n"
        f"ORDER BY score DESC, {c}.id\n"
        f"LIMIT :candidate_k"
    )
    return sql, params
