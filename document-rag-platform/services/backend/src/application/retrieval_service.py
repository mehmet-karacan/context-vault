"""Aşama 5 coordinated retrieval service (application layer).

``RetrievalService`` is the single application-level entry point that runs the
whole Aşama 5 retrieval pipeline end-to-end (AKTIF_GOREV.md 5):

    query + filters
        -> dense + lexical + identifier retrieval (RetrievalCandidate lists)
        -> Reciprocal Rank Fusion (rrf.fuse)
        -> dedupe identical-content copies (rrf.dedupe)
        -> optional rerank (build_reranker / injectable)
        -> context building (ContextBuilder, injectable parent/neighbour resolver)
        -> no-answer / intent classification (AnswerPolicy)
        -> result: final ranked candidates, context items, answerable/intent
           decision, citations summary and (when debug=True) a full
           ``retrieval_debug`` payload listing every candidate's rank/score/
           source across the dense/lexical/identifier/fusion/rerank stages
           (kabul kriteri #5: "Retrieval debug endpoint'i bütün candidate rank
           ve skorlarını gösterebilir").

Design goals

- **Constructor-injected collaborators with config-driven defaults.** Every
  dependency (the three retrievers, an ``embedder`` for dense queries, the
  fusion/dedupe callables, the reranker, the context builder, the no-answer
  policy, a ``chunk_resolver`` and a ``neighbor_resolver``) can be supplied by
  the caller. Defaults are derived from ``Settings``. This makes the whole
  service deterministic and unit-testable **without a database or network** —
  tests simply inject fake retrievers returning canned candidates.
- **DB-free context resolution.** The retrievers return ``RetrievalCandidate``
  objects that carry ``chunk_id`` + scores but not chunk bodies. The service
  therefore accepts an injectable ``chunk_resolver`` (``chunk_id -> chunk``)
  with which it attaches the real chunk to each candidate before the
  ``ContextBuilder`` consumes it, and a ``neighbor_resolver`` for controlled
  parent/adjacent expansion. A real DB-backed resolver is trivially injected by
  the API layer; unit tests inject a dict-backed one.
- **Non-breaking and additive.** The existing ``chat.py`` answer generation
  (Aşama 6) is untouched. This module only adds the coordinated retrieval flow
  and a debug surface.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.config import Settings, settings as default_settings
from src.domain.retrieval_scope import RetrievalScope
from src.domain.retrieval import (
    ContextBundle,
    FusedHit,
    RejectedContextItem,
    RerankedHit,
)
from src.infrastructure.retrieval.base import RetrievalCandidate
from src.infrastructure.retrieval.context_builder import (
    ContextBuilder,
    ContextBuildResult,
)
from src.infrastructure.retrieval.lexical import (
    content_has_any_term,
    significant_query_terms,
)
from src.infrastructure.retrieval.no_answer import AnswerPolicy, Answerability
from src.infrastructure.retrieval.rrf import dedupe as _dedupe
from src.infrastructure.retrieval.rrf import fuse as _fuse
from src.infrastructure.rerankers import build_reranker
from src.infrastructure.observability import log_structured, metrics, traced
from src.models import RetrievalRun
from src.domain.clock import utc_now

__all__ = [
    "RetrievalService",
    "RetrievalResult",
    "default_chunk_resolver",
    "dict_chunk_resolver",
]

# A chunk resolver maps a chunk_id to the resolved chunk object (any shape the
# ContextBuilder can read: ChunkCandidate, sqlalchemy model, dict, ...).
ChunkResolver = Callable[[str], Optional[Any]]
# A neighbour resolver has the ContextBuilder's shape:
#   (source_id, sequence_no) -> chunk-or-None (see context_builder.py).
NeighborResolver = Callable[[str, int], Optional[Any]]


class QueryEmbeddingError(RuntimeError):
    """Typed fail-closed outcome for empty/invalid/provider-failed embeddings."""


#: Identifier score at/above this is treated as an *exact* symbol match
#: (mirrors the score ladder in ``identifier.py``: 1.0 array / 0.9 symbol /
#: 0.6 substring). Used to feed AnswerPolicy's ``exact_identifier`` signal.
_EXACT_IDENTIFIER_SCORE: float = 0.9


def _candidate_lexical_presence(c, terms: "List[str]") -> bool:
    """Content-verified term presence for a candidate (chunk text check)."""
    if not terms:
        return False
    chunk = getattr(c, "chunk", None)
    if chunk is None:
        return False
    if isinstance(chunk, dict):
        content = chunk.get("content") or ""
    else:
        content = getattr(chunk, "content", None) or ""
    return content_has_any_term(content, terms)


def default_chunk_resolver() -> None:
    """Default chunk resolver: unknown chunks are not resolved.

    Real deployments inject a DB-backed resolver. Returning ``None`` simply
    means a candidate contributes a citation row but no context item (the
    ContextBuilder skips chunks without content).
    """
    return None


def dict_chunk_resolver(chunk_pool: Dict[str, Any]) -> ChunkResolver:
    """Build a ``ChunkResolver`` from a ``{chunk_id: chunk}`` dict (tests)."""
    return lambda chunk_id: chunk_pool.get(str(chunk_id)) if chunk_pool else None


@dataclass(frozen=True)
class RetrievalResult:
    """The coordinated end-to-end result of a single retrieval query."""

    query: str
    scope: RetrievalScope | None = None
    #: Final ranked candidates after fusion + optional rerank.
    ranked_candidates: tuple[RerankedHit, ...] = ()
    #: Context built from the ranked candidates (inside the budget).
    context: Optional[ContextBundle] = None
    #: No-answer / intent decision (AnswerPolicy.classify result).
    answerability: Optional[Answerability] = None
    #: Human/frontend-friendly citations summary (dicts).
    citations: tuple[Dict[str, Any], ...] = ()
    #: Per-stage candidate lists keyed by stage label ("dense"|"lexical"|
    #: "identifier"|"fusion"|"rerank"). Populated when ``debug=True``.
    stage_candidates: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)
    #: Reranker descriptor (provider/model) surfaced into debug metadata.
    reranker: Dict[str, Any] = field(default_factory=dict)
    #: Config knobs used for this retrieval (RRF k, budgets, ...).
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    retrieval_run_id: str | None = None
    bundle_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ranked_candidates", tuple(self.ranked_candidates))
        object.__setattr__(self, "citations", tuple(dict(c) for c in self.citations))
        object.__setattr__(
            self,
            "stage_candidates",
            MappingProxyType({k: tuple(v) for k, v in self.stage_candidates.items()}),
        )

    # --- serialization ----------------------------------------------------

    def to_dict(self, *, debug: bool = False) -> Dict[str, Any]:
        return {
            "query": self.query,
            "scope": self.scope.model_dump(mode="json") if self.scope else None,
            "intent": self.answerability.intent if self.answerability else None,
            "answerable": self.answerability.answerable if self.answerability else None,
            "answerability": self.answerability.to_dict()
            if self.answerability
            else None,
            "ranked": [serialize_candidate(c) for c in self.ranked_candidates],
            "context": self.context.to_dict() if self.context else None,
            "citations": [dict(c) for c in self.citations],
            "reranker": dict(self.reranker),
            "config": dict(self.config_snapshot),
            "retrieval_run_id": self.retrieval_run_id,
            "bundle_hash": self.bundle_hash,
            "retrieval_debug": self.debug_payload() if debug else None,
        }

    def debug_payload(self) -> Dict[str, Any]:
        """Full retrieval-debug payload: every stage's rank/score/source."""
        return {
            "query_hash": hashlib.sha256(
                " ".join(self.query.split()).casefold().encode("utf-8")
            ).hexdigest(),
            "scope": self.scope.model_dump(mode="json") if self.scope else None,
            "config": dict(self.config_snapshot),
            "reranker": dict(self.reranker),
            "answerability": self.answerability.to_dict()
            if self.answerability
            else None,
            "stages": {
                stage: [serialize_candidate(c) for c in candidates]
                for stage, candidates in self.stage_candidates.items()
            },
            "context": _redacted_debug_context(self.context),
        }

    def public_diagnostics(
        self, *, timings_ms: Dict[str, float] | None = None
    ) -> Dict[str, Any]:
        """Allowlisted external view: never query, content, metadata or matched terms."""
        return {
            "retrieval_run_id": self.retrieval_run_id,
            "bundle_hash": self.bundle_hash,
            "stages": {
                stage: [
                    {
                        "chunk_id": str(hit.chunk_id),
                        "rank": hit.rank,
                        "score": hit.score,
                    }
                    for hit in hits
                ]
                for stage, hits in self.stage_candidates.items()
                if stage in {"dense", "lexical", "identifier", "fusion", "rerank"}
            },
            "fallback_reason": "reranker_fallback"
            if any(
                getattr(hit, "fallback_reason", None) for hit in self.ranked_candidates
            )
            else None,
            "timings_ms": timings_ms or {},
        }


def _redacted_debug_context(
    context: Optional[ContextBuildResult],
) -> Dict[str, Any] | None:
    if context is None:
        return None
    return {
        "selected_items": [
            {
                "chunk_id": item.chunk_id,
                "source_id": item.source_id,
                "chunk_type": item.chunk_type,
                "content_hash": item.content_hash,
                "token_count": item.token_count,
                "rank": item.rank,
                "relation": item.relation,
                "locator": dict(item.locator),
            }
            for item in context.selected_items
        ],
        "rejected_items": [
            {
                "chunk_id": item.chunk_id,
                "rank": item.rank,
                "reason": item.reason,
                "content_hash": item.content_hash,
            }
            for item in context.rejected_items
        ],
        "total_tokens": context.total_tokens,
        "max_tokens": context.max_tokens,
        "max_chunks": context.max_chunks,
        "truncated": context.truncated,
    }


def serialize_candidate(candidate: RetrievalCandidate) -> Dict[str, Any]:
    """Serialize a RetrievalCandidate into a rank/score/source-debug dict.

    The payload is consumed directly by the A�Yama 6 frontend retrieval-debug
    panel, which renders each stage's ranked list. We therefore surface
    ``label`` (falls back to ``chunk_id``) and ``document_name`` (when it can be
    resolved from the candidate metadata or the attached chunk) so the UI never
    renders bare "—" placeholders.
    """
    meta = dict(candidate.metadata or {})
    chunk = getattr(candidate, "chunk", None)
    chunk_meta: Dict[str, Any] = {}
    chunk_document_name: Any = None
    if chunk is not None and isinstance(chunk, dict):
        chunk_meta = dict(chunk.get("metadata") or {})
        chunk_document_name = chunk.get("document_name")
    elif chunk is not None:
        chunk_meta = dict(getattr(chunk, "metadata", None) or {})
        chunk_document_name = getattr(chunk, "document_name", None)
    document_name = (
        meta.get("document_name")
        or chunk_meta.get("document_name")
        or chunk_document_name
    )
    base = {
        "chunk_id": candidate.chunk_id,
        "label": str(candidate.chunk_id),
        "rank": candidate.rank,
        "score": candidate.score,
        "source": candidate.source,
        "metadata": dict(meta),
    }
    if document_name:
        base["document_name"] = document_name
    rerank_score = getattr(candidate, "rerank_score", None)
    if rerank_score is not None:
        base["rerank_score"] = rerank_score
    fused = candidate.fused_hit if isinstance(candidate, RerankedHit) else candidate
    if isinstance(fused, FusedHit):
        base["fusion_rank"] = fused.fusion_rank
        base["rrf_score"] = fused.rrf_score
        base["rrf_contributions"] = {
            name: {
                "rank": value.retriever_rank,
                "raw_score": value.raw_score,
                "contribution": value.rrf_contribution,
                "match_type": value.match_type,
                "matched_terms": list(value.matched_terms),
            }
            for name, value in fused.per_retriever_contributions.items()
        }
    if isinstance(candidate, RerankedHit):
        base["reranker_model"] = candidate.reranker_model
        base["reranker_profile"] = candidate.reranker_profile
        base["fallback_reason"] = candidate.fallback_reason
    return base


class RetrievalService:
    """Orchestrates the full Aşama 5 hybrid retrieval flow.

    All collaborators are injectable for unit testing (no DB/network required);
    defaults come from ``Settings``.
    """

    def __init__(
        self,
        *,
        dense_retriever: Any = None,
        lexical_retriever: Any = None,
        identifier_retriever: Any = None,
        embedder: Optional[Callable[[str], List[float]]] = None,
        fusion_fn: Optional[Callable[..., List[RetrievalCandidate]]] = None,
        dedupe_fn: Optional[Callable[..., List[RetrievalCandidate]]] = None,
        reranker: Any = None,
        context_builder: Optional[ContextBuilder] = None,
        policy: Optional[AnswerPolicy] = None,
        chunk_resolver: Optional[ChunkResolver] = None,
        neighbor_resolver: Optional[NeighborResolver] = None,
        chunk_pool: Optional[Dict[str, Any]] = None,
        session: Any = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or default_settings

        # Lazily built concrete retrievers (no session until search is called),
        # so the service can be constructed as a default without a DB.
        from src.infrastructure.retrieval.dense import DenseVectorRetriever
        from src.infrastructure.retrieval.identifier import IdentifierRetriever
        from src.infrastructure.retrieval.lexical import LexicalRetriever

        self.dense_retriever = dense_retriever or DenseVectorRetriever()
        self.lexical_retriever = lexical_retriever or LexicalRetriever()
        self.identifier_retriever = identifier_retriever or IdentifierRetriever()

        self._embedder = embedder
        self._fusion_fn = fusion_fn or _fuse
        self._dedupe_fn = dedupe_fn or _dedupe
        self.reranker = (
            reranker if reranker is not None else build_reranker(self.settings)
        )
        self.context_builder = context_builder or ContextBuilder()
        self.policy = policy or AnswerPolicy()

        self._chunk_resolver = (
            chunk_resolver
            if chunk_resolver is not None
            else dict_chunk_resolver(chunk_pool)
        )
        self.neighbor_resolver = neighbor_resolver
        self.session = session

    # --- public API -------------------------------------------------------

    @traced("retrieval.pipeline")
    def retrieve(
        self,
        query: str,
        scope: RetrievalScope,
        *,
        debug: bool = False,
    ) -> RetrievalResult:
        """Run the coordinated pipeline for ``query`` with ``filters``.

        ``scope`` is mandatory and validated before any retriever runs.
        """
        if not isinstance(scope, RetrievalScope):
            raise TypeError("scope must be a validated RetrievalScope")
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise QueryEmbeddingError("query is empty after normalization")
        filters = scope.retrieval_filters()
        run_id = uuid.uuid4()
        query_id = hashlib.sha256(
            (str(run_id) + normalized_query.casefold()).encode("utf-8")
        ).hexdigest()[:24]
        run = None
        if self.session is not None:
            run = RetrievalRun(
                id=run_id,
                principal_id=scope.principal_id,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                embedding_profile_id=scope.embedding_profile_id,
                query_hash=hashlib.sha256(
                    normalized_query.casefold().encode("utf-8")
                ).hexdigest(),
                retriever_versions={
                    "dense": "pgvector-hnsw-v1",
                    "lexical": "simple-websearch-v1",
                    "identifier": "exact-prefix-trgm-v1",
                    "fusion": "rrf-v1",
                },
                config_json={
                    "rrf_k": self.settings.RRF_K,
                    "fusion_candidate_k": self.settings.FUSION_CANDIDATE_K,
                    "rerank_top_k": self.settings.RERANK_TOP_K,
                },
                candidate_count=0,
                selected_count=0,
                stage_latency_ms={},
                created_at=utc_now(),
            )
            self.session.add(run)
            self.session.commit()
        try:
            # Each repository receives the complete validated scope projection.
            stage_latency: dict[str, float] = {}
            started = time.perf_counter()
            dense = self._run_dense(normalized_query, filters)
            stage_latency["dense"] = round((time.perf_counter() - started) * 1000, 3)
            self._observe_stage(query_id, "dense", len(dense), stage_latency["dense"])
            started = time.perf_counter()
            lexical = self._run_lexical(normalized_query, filters)
            stage_latency["lexical"] = round((time.perf_counter() - started) * 1000, 3)
            self._observe_stage(
                query_id, "lexical", len(lexical), stage_latency["lexical"]
            )
            started = time.perf_counter()
            identifier = self._run_identifier(normalized_query, filters)
            stage_latency["identifier"] = round(
                (time.perf_counter() - started) * 1000, 3
            )
            self._observe_stage(
                query_id, "identifier", len(identifier), stage_latency["identifier"]
            )

            started = time.perf_counter()
            all_fused = self._fusion_fn(
                [dense, lexical, identifier], k=self.settings.RRF_K
            )
            fused = all_fused[: self.settings.FUSION_CANDIDATE_K]
            fusion_rejected = tuple(
                RejectedContextItem(
                    hit.chunk_id,
                    hit.fusion_rank,
                    "fusion_candidate_budget",
                    hit.content_hash,
                )
                for hit in all_fused[self.settings.FUSION_CANDIDATE_K :]
            )
            deduped = self._dedupe_fn(fused)[: self.settings.FUSION_CANDIDATE_K]
            stage_latency["fusion"] = round((time.perf_counter() - started) * 1000, 3)
            self._observe_stage(query_id, "fusion", len(fused), stage_latency["fusion"])

            started = time.perf_counter()
            resolved_fused = self._attach_fused_chunks(deduped)
            rerank_input, budget_rejected = self._rerank_input(resolved_fused)
            raw_reranked = self.reranker.rerank(
                normalized_query, list(rerank_input), self.settings.RERANK_TOP_K
            )
            fallback_reason = self._reranker_fallback_reason()
            reranked = self._assign_ranks(raw_reranked, rerank_input, fallback_reason)
            reranked = self._attach_chunks(reranked)
            stage_latency["rerank"] = round((time.perf_counter() - started) * 1000, 3)
            self._observe_stage(
                query_id, "rerank", len(reranked), stage_latency["rerank"]
            )

            started = time.perf_counter()
            context = self.context_builder.build(
                reranked,
                chunk_pool=self._resolved_pool(reranked),
                neighbor_resolver=self.neighbor_resolver,
                parent_resolver=lambda resolver_scope, chunk_id: self._chunk_resolver(
                    chunk_id
                )
                if resolver_scope == scope
                else None,
                scope=scope,
                query_id=query_id,
                retrieval_run_id=str(run_id),
                reranker_profile=self._reranker_profile(),
            )
            selected_ids = {hit.chunk_id for hit in reranked}
            rerank_rejected = tuple(
                RejectedContextItem(
                    hit.chunk_id,
                    hit.fusion_rank,
                    "rerank_not_selected",
                    hit.content_hash,
                )
                for hit in rerank_input
                if hit.chunk_id not in selected_ids
            )
            all_upstream_rejected = (
                fusion_rejected + tuple(budget_rejected) + rerank_rejected
            )
            if all_upstream_rejected:
                summary = dict(context.truncation_summary)
                for rejected_item in all_upstream_rejected:
                    summary[rejected_item.reason] = (
                        summary.get(rejected_item.reason, 0) + 1
                    )
                context = replace(
                    context,
                    rejected_items=context.rejected_items + all_upstream_rejected,
                    truncation_summary=summary,
                )
            stage_latency["context"] = round((time.perf_counter() - started) * 1000, 3)
            self._observe_stage(
                query_id,
                "context",
                len(context.selected_items),
                stage_latency["context"],
            )

            answerability = self.policy.classify(
                normalized_query,
                self._evidence(normalized_query, reranked, dense, lexical, identifier),
            )
            citations = self._citations(reranked)
            bundle_hash = hashlib.sha256(
                json.dumps(
                    context.to_dict(include_content=False),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()

            stage_candidates = (
                {
                    "dense": tuple(dense),
                    "lexical": tuple(lexical),
                    "identifier": tuple(identifier),
                    "fusion": tuple(fused),
                    "rerank": tuple(reranked),
                }
                if debug
                else {}
            )
            result = RetrievalResult(
                query=query,
                scope=scope,
                ranked_candidates=tuple(reranked),
                context=context,
                answerability=answerability,
                citations=tuple(citations),
                reranker={
                    "provider": getattr(self.reranker, "provider", "unknown"),
                    "model": getattr(self.reranker, "model", "unknown"),
                    "fallback_reason": fallback_reason,
                },
                config_snapshot={
                    "rrf_k": self.settings.RRF_K,
                    "fusion_candidate_k": self.settings.FUSION_CANDIDATE_K,
                    "rerank_top_k": self.settings.RERANK_TOP_K,
                    "rerank_max_candidates": self.settings.RERANK_MAX_CANDIDATES,
                    "rerank_max_tokens": self.settings.RERANK_MAX_TOKENS,
                    "reranker_enabled": bool(
                        self.settings.FEATURE_RERANKER
                        and self.settings.RERANKER_ENABLED
                    ),
                    "context_max_chunks": self.settings.CONTEXT_MAX_CHUNKS,
                    "context_max_tokens": self.settings.CONTEXT_MAX_TOKENS,
                },
                retrieval_run_id=str(run_id),
                bundle_hash=bundle_hash,
                stage_candidates=stage_candidates,
            )

            if run is not None:
                run.candidate_count = len(fused)
                run.selected_count = len(context.selected_items)
                run.stage_latency_ms = stage_latency
                run.no_answer_reason = (
                    answerability.reason if not answerability.answerable else None
                )
                run.fallback_reason = fallback_reason
                run.bundle_hash = bundle_hash
                run.finished_at = utc_now()
                self.session.commit()
            return result
        except Exception as exc:
            self._record_failed_run(run_id, run, exc)
            raise

    # --- per-retriever runners -------------------------------------------

    def _embed(self, query: str) -> List[float]:
        if self._embedder is not None:
            try:
                vector = self._embedder(query)
            except Exception as exc:
                raise QueryEmbeddingError("query embedding provider failed") from exc
            if not vector:
                raise QueryEmbeddingError(
                    "query embedding provider returned empty vector"
                )
            return vector
        # No explicit embedder injected. For a DB-backed dense retriever the
        # caller must provide one (see the debug endpoint); a fake/test
        # retriever typically ignores this value.
        raise QueryEmbeddingError("query embedder is not configured")

    def _run_dense(
        self, query: str, filters: Optional[Dict[str, Any]]
    ) -> List[RetrievalCandidate]:
        k = self.dense_retriever.resolve_k(None)
        return list(self.dense_retriever.search(self._embed(query), k, filters))

    def _run_lexical(
        self, query: str, filters: Optional[Dict[str, Any]]
    ) -> List[RetrievalCandidate]:
        k = self.lexical_retriever.resolve_k(None)
        return list(self.lexical_retriever.search(query, k, filters))

    def _run_identifier(
        self, query: str, filters: Optional[Dict[str, Any]]
    ) -> List[RetrievalCandidate]:
        k = self.identifier_retriever.resolve_k(None)
        return list(self.identifier_retriever.search(query, k, filters))

    # --- helpers ----------------------------------------------------------

    def _assign_ranks(
        self,
        candidates: List[Any],
        fused_candidates: List[FusedHit],
        fallback_reason: str | None,
    ) -> List[RerankedHit]:
        """Assign final ranks without rebuilding or losing earlier scores."""
        fused_by_id = {candidate.chunk_id: candidate for candidate in fused_candidates}
        out: List[RerankedHit] = []
        for i, candidate in enumerate(candidates or [], start=1):
            fused = (
                candidate.fused_hit
                if isinstance(candidate, RerankedHit)
                else candidate
                if isinstance(candidate, FusedHit)
                else fused_by_id.get(str(getattr(candidate, "chunk_id", "")))
            )
            if fused is None:
                raise TypeError(
                    "reranker returned a candidate outside the fused window"
                )
            score = getattr(candidate, "rerank_score", None)
            out.append(
                RerankedHit(
                    fused_hit=fused,
                    reranker_model=str(getattr(self.reranker, "model", "unknown")),
                    reranker_profile=self._reranker_profile(),
                    reranker_score=float(score) if score is not None else None,
                    final_rank=i,
                    fallback_reason=fallback_reason,
                )
            )
        return out

    def _attach_chunks(self, candidates: List[RerankedHit]) -> List[RerankedHit]:
        return [
            candidate.with_chunk(self._chunk_resolver(candidate.chunk_id))
            for candidate in candidates or []
        ]

    def _attach_fused_chunks(self, candidates: List[FusedHit]) -> List[FusedHit]:
        return [
            candidate.with_chunk(self._chunk_resolver(candidate.chunk_id))
            for candidate in candidates or []
        ]

    def _rerank_input(
        self, candidates: List[FusedHit]
    ) -> tuple[List[FusedHit], List[RejectedContextItem]]:
        accepted: List[FusedHit] = []
        rejected: List[RejectedContextItem] = []
        tokens = 0
        for candidate in candidates:
            if len(accepted) >= self.settings.RERANK_MAX_CANDIDATES:
                rejected.append(
                    RejectedContextItem(
                        candidate.chunk_id,
                        candidate.fusion_rank,
                        "rerank_candidate_budget",
                        candidate.content_hash,
                    )
                )
                continue
            chunk = candidate.chunk
            content = (
                (chunk.get("content") or "")
                if isinstance(chunk, dict)
                else (getattr(chunk, "content", "") or "")
            )
            count = self.context_builder._count(content)
            if tokens + count > self.settings.RERANK_MAX_TOKENS:
                rejected.append(
                    RejectedContextItem(
                        candidate.chunk_id,
                        candidate.fusion_rank,
                        "rerank_token_budget",
                        candidate.content_hash,
                    )
                )
                continue
            accepted.append(candidate)
            tokens += count
        return accepted, rejected

    def _reranker_profile(self) -> str:
        return ":".join(
            (
                str(getattr(self.reranker, "provider", "unknown")),
                str(getattr(self.reranker, "model", "unknown")),
            )
        )

    def _reranker_fallback_reason(self) -> str | None:
        if getattr(self.reranker, "last_error", None) is not None:
            return f"reranker_error:{type(self.reranker.last_error).__name__}"
        if getattr(self.reranker, "provider", "none") == "none":
            return "reranker_disabled"
        return None

    def _record_failed_run(
        self, run_id: uuid.UUID, run: RetrievalRun | None, exc: Exception
    ) -> None:
        if self.session is None or run is None:
            return
        try:
            self.session.rollback()
            failed = (
                self.session.get(RetrievalRun, run_id)
                if hasattr(self.session, "get")
                else run
            )
            if failed is None:
                return
            failed.fallback_reason = f"pipeline_error:{type(exc).__name__}"
            failed.finished_at = utc_now()
            self.session.commit()
        except Exception:  # noqa: BLE001 - never mask the original retrieval error
            try:
                self.session.rollback()
            except Exception:  # noqa: BLE001
                pass

    def _observe_stage(
        self, query_id: str, stage: str, candidate_count: int, latency_ms: float
    ) -> None:
        # Fixed metric names only: document/chunk/project ids are never labels.
        metrics.record_duration(f"retrieval.{stage}", latency_ms / 1000)
        metrics.incr("retrieval.candidates", candidate_count)
        slow = latency_ms >= self.settings.RETRIEVAL_SLOW_QUERY_MS
        log_structured(
            logging.WARNING if slow else logging.INFO,
            "retrieval stage slow" if slow else "retrieval stage completed",
            query_id=query_id,
            retrieval_stage=stage,
            candidate_count=candidate_count,
            latency_ms=latency_ms,
            error_code="slow_query" if slow else None,
        )

    def _resolved_pool(self, candidates: List[RerankedHit]) -> Dict[str, Any]:
        pool: Dict[str, Any] = {}
        for c in candidates or []:
            chunk = getattr(c, "chunk", None)
            if chunk is not None:
                pool[str(c.chunk_id)] = chunk
        return pool

    def _evidence(
        self,
        query: str,
        ranked: List[RetrievalCandidate],
        dense: List[RetrievalCandidate],
        lexical: List[RetrievalCandidate],
        identifier: List[RetrievalCandidate],
    ) -> List[Dict[str, Any]]:
        """Build AnswerPolicy evidence signals from the final ranked list.

        Per candidate we surface the dense score, the strong-lexical score and
        whether the identifier retriever produced an exact (or any) match so a
        low dense score can be overridden by lexical/identifier evidence
        (AKTIF_GOREV.md 5.6).

        ``lexical_presence`` is a content-verified boolean computed from each
        ranked candidate's attached chunk *text*: True when a significant query
        term (see ``significant_query_terms``) literally appears in that chunk.
        It grounds AnswerPolicy's term-presence rescue even for multi-term /
        split-acronym / comparative queries whose terms the AND-based lexical
        retriever cannot surface (e.g. "ttnet sis" -> content lexeme
        "ttnetsis"), while still never fabricating — presence is verified
        against the real source text.
        """
        presence_terms = significant_query_terms(query)
        dense_scores: Dict[str, float] = {c.chunk_id: c.score for c in dense}
        lexical_scores: Dict[str, float] = {c.chunk_id: c.score for c in lexical}
        identifier_scores: Dict[str, float] = {c.chunk_id: c.score for c in identifier}

        evidence: List[Dict[str, Any]] = []
        for c in ranked or []:
            ident_score = identifier_scores.get(c.chunk_id)
            evidence.append(
                {
                    "dense_score": dense_scores.get(c.chunk_id),
                    "lexical_score": lexical_scores.get(c.chunk_id),
                    "identifier": ident_score is not None,
                    "exact_identifier": (
                        ident_score is not None
                        and ident_score >= _EXACT_IDENTIFIER_SCORE
                    ),
                    "lexical_presence": _candidate_lexical_presence(c, presence_terms),
                }
            )
        return evidence

    def _citations(self, ranked: List[RetrievalCandidate]) -> List[Dict[str, Any]]:
        citations: List[Dict[str, Any]] = []
        for i, c in enumerate(ranked or [], start=1):
            chunk = getattr(c, "chunk", None)
            meta = dict(c.metadata or {})
            chunk_meta = (
                dict(getattr(chunk, "metadata", None) or {})
                if chunk is not None
                else {}
            )
            heading = list(getattr(chunk, "heading_path", None) or [])
            locator = dict(getattr(chunk, "locator", None) or {})
            citations.append(
                {
                    "label": f"S{i}",
                    "rank": c.rank,
                    "score": c.score,
                    "source": c.source,
                    "chunk_id": c.chunk_id,
                    "document_id": meta.get("document_id"),
                    "document_name": meta.get("document_name")
                    or chunk_meta.get("document_name"),
                    "source_type": meta.get("source_type"),
                    "heading_path": heading,
                    "locator": locator,
                    "snippet": (getattr(chunk, "content", "") or "")[:200],
                }
            )
        return citations
