"""Shared retrieval primitives (Aşama 5).

``RetrievalCandidate`` is the single stable shape every retriever
(``dense``/``lexical``/``identifier``) emits and that ``rrf`` consumes. Its
``chunk_id`` is the stable key RRF fuses across the three ranked lists; the
``source`` tag records which retriever produced it so retrieval-debug and
fused metadata can attribute scores.

``FilterTerm`` + ``normalize_filters`` form the DB-free, serializable filter
layer shared by all three retrievers. A retriever's SQL spec carries a list
of these terms; the actual DB adapter turns each into a WHERE predicate on
the appropriate table (``chunk`` vs ``document``). Keeping normalization pure
here lets unit tests assert exactly which filters are applied without a real
PostgreSQL connection.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import logging
import time
from typing import Any, Dict, List, Optional

from src.domain.retrieval import RetrievalCandidate, RetrieverHit
from sqlalchemy import text
from src.config import settings
from src.infrastructure.observability import log_structured


@dataclass(frozen=True)
class FilterTerm:
    """A single WHERE predicate in retriever-agnostic form.

    ``table`` is the physical join alias the predicate targets (``"chunk"``
    or ``"document"``); ``field`` its column; ``op`` one of
    ``("eq", "in_", "is_")``; ``value`` the bound value.
    """

    table: str
    field: str
    op: str
    value: Any


# Filters that live on the chunk table (reachable directly from a Chunk row).
CHUNK_FILTER_FIELDS: Dict[str, str] = {
    "version_id": "eq",
    "document_id": "eq",
    "symbol_name": "eq",
    "chunk_type": "eq",
}

# Filters that live on the joined document table.
DOCUMENT_FILTER_FIELDS: Dict[str, str] = {
    "project_id": "eq",
    "source_type": "eq",
}

PROJECT_FILTER_FIELDS: Dict[str, str] = {"workspace_id": "eq"}

# Aliases accepted on the input filters dict: {"document_ids" -> document_id IN}.
ALIASES: Dict[str, Dict[str, Any]] = {
    "document_ids": {"field": "document_id", "op": "in_"},
    "source_types": {"field": "source_type", "op": "in_", "table": "document"},
    "data_classifications": {
        "field": "data_classification",
        "op": "in_",
        "table": "document",
    },
}

ALLOWED_FILTER_KEYS = {
    *CHUNK_FILTER_FIELDS,
    *DOCUMENT_FILTER_FIELDS,
    *PROJECT_FILTER_FIELDS,
    *ALIASES,
    "scope",
    "active_versions_only",
    "embedding_profile_id",
}


def require_scoped_filters(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Reject calls that lack any mandatory RetrievalScope predicate."""
    required = (
        "workspace_id",
        "project_id",
        "embedding_profile_id",
        "data_classifications",
    )
    missing = [name for name in required if not filters or not filters.get(name)]
    if not filters or filters.get("active_versions_only") is not True:
        missing.append("active_versions_only")
    if missing:
        raise ValueError(
            "retrieval repository requires complete RetrievalScope: "
            + ", ".join(sorted(set(missing)))
        )
    return filters


# scope -> set of source_type values (AKTIF_GOREV.md §5.6 / §6).
SCOPE_SOURCE_TYPES: Dict[str, List[str]] = {
    "documents": ["document"],
    "images": ["image"],
    "code": ["repository", "directory", "archive"],
}


def normalize_filters(
    filters: Optional[Dict[str, Any]],
    scope_key: str = "scope",
) -> List[FilterTerm]:
    """Flatten a caller-supplied filters dict into a deterministic FilterTerm list.

    Unknown keys fail closed. Empty ``document_ids`` and ``source_types`` emit
    a constant-false predicate rather than collapsing into an unscoped query.
    """
    terms: List[FilterTerm] = []
    if not filters:
        return terms

    unknown = sorted(set(filters) - ALLOWED_FILTER_KEYS)
    if unknown:
        raise ValueError(f"unknown retrieval filter(s): {', '.join(unknown)}")

    explicit_source_type = None
    for key in ("source_type", ALIASES.get("document_ids", {}).get("field")):
        pass  # handled below via canonical iteration

    def _push(table: str, field: str, op: str, value: Any) -> None:
        # Empty membership is an explicit deny-all scope, never a no-op.
        if op in ("eq", "in_"):
            if isinstance(value, (list, tuple, set)) and not value:
                terms.append(FilterTerm(table=table, field=field, op="false", value=[]))
                return
            if value is None:
                return
        terms.append(FilterTerm(table=table, field=field, op=op, value=value))

    for key, value in filters.items():
        if value is None:
            continue
        if key in ALIASES:
            alias = ALIASES[key]
            _push(alias.get("table", "chunk"), alias["field"], alias["op"], value)
            continue
        if key in CHUNK_FILTER_FIELDS:
            _push("chunk", key, CHUNK_FILTER_FIELDS[key], value)
            continue
        if key in DOCUMENT_FILTER_FIELDS:
            if key == "source_type":
                explicit_source_type = value
            _push("document", key, DOCUMENT_FILTER_FIELDS[key], value)
            continue
        if key in PROJECT_FILTER_FIELDS:
            _push("project", key, PROJECT_FILTER_FIELDS[key], value)
            continue
        if key == "active_versions_only":
            if value is True:
                terms.append(
                    FilterTerm(
                        table="chunk",
                        field="version_id",
                        op="active_version",
                        value=True,
                    )
                )
            continue
        if key == "embedding_profile_id":
            _push("version", key, "eq", value)
            continue

    scope = filters.get(scope_key)
    if (
        scope
        and scope != "all"
        and explicit_source_type is None
        and scope in SCOPE_SOURCE_TYPES
    ):
        _push("document", "source_type", "in_", SCOPE_SOURCE_TYPES[scope])

    return terms


def filter_spec(
    filters: Optional[Dict[str, Any]], scope_key: str = "scope"
) -> List[Dict[str, Any]]:
    """Serializable form of ``normalize_filters`` (for embedding in a spec)."""
    return [asdict(t) for t in normalize_filters(filters, scope_key=scope_key)]


def render_where(
    terms: List[FilterTerm],
    prefix: str = "",
    chunk_alias: str = "c",
    document_alias: str = "d",
    project_alias: str = "p",
    version_alias: str = "v",
) -> "tuple[str, Dict[str, Any]]":
    """Render a FilterTerm list into a (sql_clause, params) fragment (pure).

    ``prefix`` is prepended to every parameter name to avoid collisions when
    several fragments share one statement. ``chunk`` terms map to
    ``<chunk_alias>.<col>`` and ``document`` terms to ``<document_alias>.<col>``
    — the SQL emitted by dense/lexical/identifier specs all join the document
    row under the alias ``d`` and the chunk under ``c`` (or a retriever alias).
    """
    clauses: List[str] = []
    params: Dict[str, Any] = {}
    for i, term in enumerate(terms):
        aliases = {
            "chunk": chunk_alias,
            "document": document_alias,
            "project": project_alias,
            "version": version_alias,
        }
        alias = aliases[term.table]
        column = f"{alias}.{term.field}"
        pname = f"{prefix}p{i}"
        if term.op == "eq":
            clauses.append(f"{column} = :{pname}")
            params[pname] = term.value
        elif term.op == "in_":
            values = list(term.value)
            placeholders = ", ".join(f":{prefix}in{i}_{j}" for j in range(len(values)))
            clauses.append(f"{column} IN ({placeholders})")
            for j, v in enumerate(values):
                params[f"{prefix}in{i}_{j}"] = v
        elif term.op == "is_":
            clauses.append(f"{column} IS :{pname}")
            params[pname] = term.value
        elif term.op == "false":
            clauses.append("FALSE")
        elif term.op == "active_version":
            clauses.append(
                f"{chunk_alias}.version_id = {document_alias}.active_version_id"
            )
        else:
            raise ValueError(f"unsupported filter op: {term.op!r}")
    return (" AND ".join(clauses), params)


def to_candidates(rows, source: str = "dense") -> List[RetrievalCandidate]:
    """Convert raw DB rows (chunk_id, score[, metadata dict]) to candidates.

    Accepts tuples/lists, dict-like rows, or objects with ``chunk_id`` /
    ``score`` attributes. Rank is assigned from the row's 1-based position.
    """
    out: List[RetrievalCandidate] = []
    for rank, row in enumerate(rows, start=1):
        if isinstance(row, dict):
            chunk_id = str(row.get("chunk_id", row.get("id")))
            score = float(row.get("score", row.get("rank_score", 0.0)))
            meta = dict(row.get("metadata") or {})
        elif isinstance(row, (tuple, list)):
            chunk_id = str(row[0])
            score = float(row[1])
            meta = dict(row[2]) if len(row) > 2 and isinstance(row[2], dict) else {}
        else:
            chunk_id = str(getattr(row, "chunk_id"))
            score = float(getattr(row, "score"))
            meta = dict(getattr(row, "metadata", None) or {})
        out.append(
            RetrieverHit(
                chunk_id=chunk_id,
                rank=rank,
                score=score,
                source=source,
                metadata=meta,
                match_type=str(meta.get("match_type") or source),
                matched_terms=tuple(meta.get("matched_terms") or ()),
                locator=dict(meta.get("locator") or {}),
            )
        )
    return out


def execute_retrieval_query(
    session: Any,
    sql: str,
    params: Dict[str, Any],
    *,
    stage: str,
    expected_index: str,
):
    """Execute and, only when slow, trace a content-free index-miss signal."""
    started = time.perf_counter()
    rows = session.execute(text(sql), params).fetchall()
    latency_ms = (time.perf_counter() - started) * 1000
    if (
        settings.RETRIEVAL_TRACE_QUERY_PLANS
        and latency_ms >= settings.RETRIEVAL_SLOW_QUERY_MS
        and getattr(getattr(session, "bind", None), "dialect", None) is not None
    ):
        plan_rows = session.execute(
            text("EXPLAIN (FORMAT JSON) " + sql), params
        ).fetchall()
        if expected_index not in str(plan_rows):
            log_structured(
                logging.WARNING,
                "retrieval query index miss",
                retrieval_stage=stage,
                candidate_count=len(rows),
                latency_ms=round(latency_ms, 3),
                error_code="index_miss",
            )
    return rows
