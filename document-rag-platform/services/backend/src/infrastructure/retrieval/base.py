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

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

VALID_SOURCES = ("dense", "lexical", "identifier")


@dataclass
class RetrievalCandidate:
    """A single retrieval hit shared across dense/lexical/identifier + RRF.

    Attributes
    ----------
    chunk_id: stable key RRF fuses on (a Chunk primary key string).
    rank: 1-based position within its own retriever's result list.
    score: raw retriever score (dense cosine, lexical rank, identifier match).
        Not directly comparable across sources — RRF works on rank instead.
    source: one of ``("dense", "lexical", "identifier")``.
    metadata: free-form extras (document_id, version_id, symbol_name, ...).
    """

    chunk_id: str
    rank: int
    score: float
    source: str = "dense"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source not in VALID_SOURCES:
            raise ValueError(f"invalid source: {self.source!r} (expected one of {VALID_SOURCES})")


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

# Aliases accepted on the input filters dict: {"document_ids" -> document_id IN}.
ALIASES: Dict[str, Dict[str, Any]] = {
    "document_ids": {"field": "document_id", "op": "in_"},
}

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

    Recognized keys: the CHUNK_FILTER_FIELDS / DOCUMENT_FILTER_FIELDS set plus
    the ``ALIASES`` and a ``scope`` shortcut (``all | documents | images |
    code``). Unknown keys are ignored so future filters never break existing
    retrievers. Empty ``document_ids`` collapses to no filter (an empty ``IN``
    would be meaningless). An explicit ``source_type`` wins over ``scope``.
    """
    terms: List[FilterTerm] = []
    if not filters:
        return terms

    explicit_source_type = None
    for key in ("source_type", ALIASES.get("document_ids", {}).get("field")):
        pass  # handled below via canonical iteration

    def _push(table: str, field: str, op: str, value: Any) -> None:
        # Skip no-op / empty values so we never emit a vacuous predicate.
        if op in ("eq", "in_"):
            if isinstance(value, (list, tuple, set)) and not value:
                return
            if value is None:
                return
        terms.append(FilterTerm(table=table, field=field, op=op, value=value))

    for key, value in filters.items():
        if value is None:
            continue
        if key in ALIASES:
            alias = ALIASES[key]
            _push("chunk", alias["field"], alias["op"], value)
            continue
        if key in CHUNK_FILTER_FIELDS:
            _push("chunk", key, CHUNK_FILTER_FIELDS[key], value)
            continue
        if key in DOCUMENT_FILTER_FIELDS:
            if key == "source_type":
                explicit_source_type = value
            _push("document", key, DOCUMENT_FILTER_FIELDS[key], value)
            continue
        # Unknown keys intentionally ignored.

    scope = filters.get(scope_key)
    if (
        scope
        and scope != "all"
        and explicit_source_type is None
        and scope in SCOPE_SOURCE_TYPES
    ):
        _push("document", "source_type", "in_", SCOPE_SOURCE_TYPES[scope])

    return terms


def filter_spec(filters: Optional[Dict[str, Any]], scope_key: str = "scope") -> List[Dict[str, Any]]:
    """Serializable form of ``normalize_filters`` (for embedding in a spec)."""
    return [asdict(t) for t in normalize_filters(filters, scope_key=scope_key)]


def render_where(
    terms: List[FilterTerm],
    prefix: str = "",
    chunk_alias: str = "c",
    document_alias: str = "d",
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
        alias = chunk_alias if term.table == "chunk" else document_alias
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
            RetrievalCandidate(
                chunk_id=chunk_id,
                rank=rank,
                score=score,
                source=source,
                metadata=meta,
            )
        )
    return out
