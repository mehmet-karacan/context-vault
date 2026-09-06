"""Immutable value objects for the scoped retrieval pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping


def _frozen_map(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True)
class RetrieverHit:
    """One immutable, provenance-bearing retriever result.

    ``rank``/``score``/``source`` remain the compatibility spelling used by
    the ports.  The canonical stage names are exposed by read-only aliases.
    """

    chunk_id: str
    rank: int
    score: float
    source: str = "dense"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    chunk: Any = field(default=None, compare=False, repr=False)
    document_id: str | None = None
    version_id: str | None = None
    source_file_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    embedding_profile_id: str | None = None
    match_type: str = "unknown"
    matched_terms: tuple[str, ...] = ()
    locator: Mapping[str, Any] = field(default_factory=dict)
    content_hash: str | None = None
    # Transitional adapter field for callers that still pass a scored hit
    # directly to AnswerService. The coordinated pipeline uses RerankedHit.
    rerank_score: float | None = None

    def __post_init__(self) -> None:
        if self.source not in {"dense", "lexical", "identifier"}:
            raise ValueError(f"invalid retriever source: {self.source!r}")
        if self.rank <= 0:
            raise ValueError("retriever rank must be positive")
        meta = dict(self.metadata or {})
        object.__setattr__(self, "metadata", _frozen_map(meta))
        object.__setattr__(
            self, "locator", _frozen_map(self.locator or meta.get("locator"))
        )
        object.__setattr__(
            self,
            "matched_terms",
            tuple(self.matched_terms or meta.get("matched_terms") or ()),
        )
        for name in (
            "document_id",
            "version_id",
            "source_file_id",
            "workspace_id",
            "project_id",
            "embedding_profile_id",
            "content_hash",
        ):
            if getattr(self, name) is None and meta.get(name) is not None:
                object.__setattr__(self, name, str(meta[name]))
        if self.match_type == "unknown" and meta.get("match_type"):
            object.__setattr__(self, "match_type", str(meta["match_type"]))

    @property
    def retriever_name(self) -> str:
        return self.source

    @property
    def retriever_rank(self) -> int:
        return self.rank

    @property
    def raw_score(self) -> float:
        return self.score

    def with_chunk(self, chunk: Any) -> "RetrieverHit":
        return replace(self, chunk=chunk)


# Compatibility import used by older application modules and public tests.
RetrievalCandidate = RetrieverHit


@dataclass(frozen=True)
class RetrieverContribution:
    retriever_name: str
    retriever_rank: int
    raw_score: float
    rrf_contribution: float
    match_type: str
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class FusedHit:
    """A unique chunk plus each retriever's independent RRF contribution."""

    representative: RetrieverHit
    per_retriever_contributions: Mapping[str, RetrieverContribution]
    rrf_score: float
    fusion_rank: int
    rerank_score: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "per_retriever_contributions",
            _frozen_map(self.per_retriever_contributions),
        )

    @property
    def chunk_id(self) -> str:
        return self.representative.chunk_id

    @property
    def rank(self) -> int:
        return self.fusion_rank

    @property
    def score(self) -> float:
        return self.rrf_score

    @property
    def source(self) -> str:
        return self.representative.source

    @property
    def metadata(self) -> Mapping[str, Any]:
        return _frozen_map(
            {
                **dict(self.representative.metadata),
                "sources": tuple(self.per_retriever_contributions),
                "rrf_contributions": {
                    name: contribution.rrf_contribution
                    for name, contribution in self.per_retriever_contributions.items()
                },
            }
        )

    @property
    def chunk(self) -> Any:
        return self.representative.chunk

    @property
    def content_hash(self) -> str | None:
        return self.representative.content_hash

    def with_chunk(self, chunk: Any) -> "FusedHit":
        return replace(self, representative=self.representative.with_chunk(chunk))


@dataclass(frozen=True)
class RerankedHit:
    """Final immutable rank that retains the complete fused stage."""

    fused_hit: FusedHit
    reranker_model: str
    reranker_profile: str
    reranker_score: float | None
    final_rank: int
    fallback_reason: str | None = None

    @property
    def chunk_id(self) -> str:
        return self.fused_hit.chunk_id

    @property
    def rank(self) -> int:
        return self.final_rank

    @property
    def score(self) -> float:
        return self.fused_hit.rrf_score

    @property
    def source(self) -> str:
        return self.fused_hit.source

    @property
    def metadata(self) -> Mapping[str, Any]:
        return self.fused_hit.metadata

    @property
    def chunk(self) -> Any:
        return self.fused_hit.chunk

    def with_chunk(self, chunk: Any) -> "RerankedHit":
        return replace(self, fused_hit=self.fused_hit.with_chunk(chunk))


@dataclass(frozen=True)
class ScopedNeighborKey:
    workspace_id: str
    project_id: str
    document_id: str
    version_id: str
    source_file_id: str | None
    sequence_no: int


@dataclass(frozen=True)
class ContextItem:
    chunk_id: str
    workspace_id: str | None
    project_id: str | None
    document_id: str | None
    version_id: str | None
    source_file_id: str | None
    source_id: str
    chunk_type: str
    content: str
    heading_path: tuple[str, ...] = ()
    locator: Mapping[str, Any] = field(default_factory=dict)
    policy_classification: str = "internal"
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    content_hash: str = ""
    evidence_hash: str = ""
    token_count: int = 0
    rank: int = 0
    sequence_no: int = 0
    relation: str = "selected"

    def __post_init__(self) -> None:
        object.__setattr__(self, "heading_path", tuple(self.heading_path or ()))
        object.__setattr__(self, "locator", _frozen_map(self.locator))
        object.__setattr__(self, "metadata", _frozen_map(self.metadata))

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        value = {
            "chunk_id": self.chunk_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "source_file_id": self.source_file_id,
            "source_id": self.source_id,
            "chunk_type": self.chunk_type,
            "heading_path": list(self.heading_path),
            "locator": dict(self.locator),
            "policy_classification": self.policy_classification,
            "content_hash": self.content_hash,
            "evidence_hash": self.evidence_hash,
            "token_count": self.token_count,
            "rank": self.rank,
            "sequence_no": self.sequence_no,
            "relation": self.relation,
        }
        if include_content:
            value["content"] = self.content
        return value


@dataclass(frozen=True)
class RejectedContextItem:
    chunk_id: str
    rank: int
    reason: str
    content_hash: str | None = None


@dataclass(frozen=True)
class ContextBundle:
    query_id: str
    scope: Any
    retrieval_run_id: str
    embedding_profile_id: str | None
    reranker_profile: str
    selected_items: tuple[ContextItem, ...]
    rejected_items: tuple[RejectedContextItem, ...]
    token_budget: int
    chunk_budget: int
    total_tokens: int
    truncated: bool
    truncation_summary: Mapping[str, int]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_items", tuple(self.selected_items))
        object.__setattr__(self, "rejected_items", tuple(self.rejected_items))
        object.__setattr__(
            self, "truncation_summary", _frozen_map(self.truncation_summary)
        )
        object.__setattr__(self, "provenance", _frozen_map(self.provenance))
        if self.total_tokens > self.token_budget:
            raise ValueError("context bundle exceeds token budget")
        if len(self.selected_items) > self.chunk_budget:
            raise ValueError("context bundle exceeds chunk budget")

    # Compatibility views used by the existing answer/presentation layer.
    @property
    def items(self) -> tuple[ContextItem, ...]:
        return self.selected_items

    @property
    def max_tokens(self) -> int:
        return self.token_budget

    @property
    def max_chunks(self) -> int:
        return self.chunk_budget

    @property
    def selected_chunk_ids(self) -> tuple[str, ...]:
        return tuple(
            i.chunk_id for i in self.selected_items if i.relation == "selected"
        )

    @property
    def expanded_chunk_ids(self) -> tuple[str, ...]:
        return tuple(
            i.chunk_id for i in self.selected_items if i.relation != "selected"
        )

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "scope": self.scope.model_dump(mode="json")
            if hasattr(self.scope, "model_dump")
            else self.scope,
            "retrieval_run_id": self.retrieval_run_id,
            "embedding_profile_id": self.embedding_profile_id,
            "reranker_profile": self.reranker_profile,
            "selected_items": [
                i.to_dict(include_content=include_content) for i in self.selected_items
            ],
            "rejected_items": [
                {
                    "chunk_id": i.chunk_id,
                    "rank": i.rank,
                    "reason": i.reason,
                    "content_hash": i.content_hash,
                }
                for i in self.rejected_items
            ],
            "total_tokens": self.total_tokens,
            "max_tokens": self.token_budget,
            "max_chunks": self.chunk_budget,
            "truncated": self.truncated,
            "truncation_summary": dict(self.truncation_summary),
            "provenance": dict(self.provenance),
            # Temporary response compatibility; AnswerService uses
            # ``selected_items`` directly.
            "items": [
                i.to_dict(include_content=include_content) for i in self.selected_items
            ],
            "selected_chunk_ids": list(self.selected_chunk_ids),
            "expanded_chunk_ids": list(self.expanded_chunk_ids),
        }
