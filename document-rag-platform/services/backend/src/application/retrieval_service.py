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

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.config import Settings, settings as default_settings
from src.infrastructure.retrieval.base import RetrievalCandidate
from src.infrastructure.retrieval.context_builder import ContextBuilder, ContextBuildResult
from src.infrastructure.retrieval.no_answer import AnswerPolicy, Answerability
from src.infrastructure.retrieval.rrf import dedupe as _dedupe
from src.infrastructure.retrieval.rrf import fuse as _fuse
from src.infrastructure.rerankers import build_reranker

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

#: Identifier score at/above this is treated as an *exact* symbol match
#: (mirrors the score ladder in ``identifier.py``: 1.0 array / 0.9 symbol /
#: 0.6 substring). Used to feed AnswerPolicy's ``exact_identifier`` signal.
_EXACT_IDENTIFIER_SCORE: float = 0.9


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


@dataclass
class RetrievalResult:
    """The coordinated end-to-end result of a single retrieval query."""

    query: str
    filters: Dict[str, Any] = field(default_factory=dict)
    #: Final ranked candidates after fusion + optional rerank.
    ranked_candidates: List[RetrievalCandidate] = field(default_factory=list)
    #: Context built from the ranked candidates (inside the budget).
    context: Optional[ContextBuildResult] = None
    #: No-answer / intent decision (AnswerPolicy.classify result).
    answerability: Optional[Answerability] = None
    #: Human/frontend-friendly citations summary (dicts).
    citations: List[Dict[str, Any]] = field(default_factory=list)
    #: Per-stage candidate lists keyed by stage label ("dense"|"lexical"|
    #: "identifier"|"fusion"|"rerank"). Populated when ``debug=True``.
    stage_candidates: Dict[str, List[RetrievalCandidate]] = field(default_factory=dict)
    #: Reranker descriptor (provider/model) surfaced into debug metadata.
    reranker: Dict[str, Any] = field(default_factory=dict)
    #: Config knobs used for this retrieval (RRF k, budgets, ...).
    config_snapshot: Dict[str, Any] = field(default_factory=dict)

    # --- serialization ----------------------------------------------------

    def to_dict(self, *, debug: bool = False) -> Dict[str, Any]:
        return {
            "query": self.query,
            "filters": dict(self.filters),
            "intent": self.answerability.intent if self.answerability else None,
            "answerable": self.answerability.answerable if self.answerability else None,
            "answerability": self.answerability.to_dict() if self.answerability else None,
            "ranked": [serialize_candidate(c) for c in self.ranked_candidates],
            "context": self.context.to_dict() if self.context else None,
            "citations": list(self.citations),
            "reranker": dict(self.reranker),
            "config": dict(self.config_snapshot),
            "retrieval_debug": self.debug_payload() if debug else None,
        }

    def debug_payload(self) -> Dict[str, Any]:
        """Full retrieval-debug payload: every stage's rank/score/source."""
        return {
            "query": self.query,
            "filters": dict(self.filters),
            "config": dict(self.config_snapshot),
            "reranker": dict(self.reranker),
            "answerability": self.answerability.to_dict() if self.answerability else None,
            "stages": {
                stage: [serialize_candidate(c) for c in candidates]
                for stage, candidates in self.stage_candidates.items()
            },
            "context": self.context.to_dict() if self.context else None,
        }


def serialize_candidate(candidate: RetrievalCandidate) -> Dict[str, Any]:
    """Serialize a RetrievalCandidate into a rank/score/source-debug dict."""
    base = {
        "chunk_id": candidate.chunk_id,
        "rank": candidate.rank,
        "score": candidate.score,
        "source": candidate.source,
        "metadata": dict(candidate.metadata or {}),
    }
    rerank_score = getattr(candidate, "rerank_score", None)
    if rerank_score is not None:
        base["rerank_score"] = rerank_score
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
        self.reranker = reranker if reranker is not None else build_reranker(self.settings)
        self.context_builder = context_builder or ContextBuilder()
        self.policy = policy or AnswerPolicy()

        self._chunk_resolver = (
            chunk_resolver if chunk_resolver is not None else dict_chunk_resolver(chunk_pool)
        )
        self.neighbor_resolver = neighbor_resolver

    # --- public API -------------------------------------------------------

    def retrieve(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        *,
        debug: bool = False,
    ) -> RetrievalResult:
        """Run the coordinated pipeline for ``query`` with ``filters``.

        ``filters`` follows the shared shape (project_id / document_ids /
        scope / source_type / version_id / ...) from ``retrieval.base``.
        """
        filters = dict(filters or {})

        # Each real retriever normalizes its own filters (``filter_spec`` ->
        # ``normalize_filters`` inside ``build_spec``), so we hand off the raw
        # dict. Passing a pre-normalized ``List[FilterTerm]`` would make the
        # retriever's internal ``normalize_filters`` fail, because a List has
        # no ``.items()`` (dense/lexical/identifier all call it on ``filters``).
        # 1) Per-retriever stage ---
        dense = self._run_dense(query, filters)
        lexical = self._run_lexical(query, filters)
        identifier = self._run_identifier(query, filters)

        # 2) RRF fusion + fusion-window truncation ---
        fused = self._fusion_fn([dense, lexical, identifier], k=self.settings.RRF_K)
        fused = fused[: self.settings.FUSION_CANDIDATE_K]

        # 3) Deduplicate identical-content copies ---
        deduped = self._dedupe_fn(fused)
        deduped = deduped[: self.settings.FUSION_CANDIDATE_K]

        # 4) Rerank (feature-gated; Noop/fallback keeps fusion order) ---
        reranked = self.reranker.rerank(
            query, list(deduped), self.settings.RERANK_TOP_K
        )
        reranked = self._assign_ranks(reranked)

        # 5) Build context over the resolved chunks ---
        self._attach_chunks(reranked)
        context = self.context_builder.build(
            reranked,
            chunk_pool=self._resolved_pool(reranked),
            neighbor_resolver=self.neighbor_resolver,
        )

        # 6) No-answer / intent classification ---
        answerability = self.policy.classify(
            query, self._evidence(reranked, dense, lexical, identifier)
        )

        # 7) Citations summary from the final ranked candidates ---
        citations = self._citations(reranked)

        result = RetrievalResult(
            query=query,
            filters=filters,
            ranked_candidates=reranked,
            context=context,
            answerability=answerability,
            citations=citations,
            reranker={
                "provider": getattr(self.reranker, "provider", "unknown"),
                "model": getattr(self.reranker, "model", "unknown"),
            },
            config_snapshot={
                "rrf_k": self.settings.RRF_K,
                "fusion_candidate_k": self.settings.FUSION_CANDIDATE_K,
                "rerank_top_k": self.settings.RERANK_TOP_K,
                "reranker_enabled": bool(
                    self.settings.FEATURE_RERANKER and self.settings.RERANKER_ENABLED
                ),
                "context_max_chunks": self.settings.CONTEXT_MAX_CHUNKS,
                "context_max_tokens": self.settings.CONTEXT_MAX_TOKENS,
            },
        )

        if debug:
            result.stage_candidates = {
                "dense": list(dense),
                "lexical": list(lexical),
                "identifier": list(identifier),
                "fusion": list(fused),
                "rerank": list(reranked),
            }

        return result

    # --- per-retriever runners -------------------------------------------

    def _embed(self, query: str) -> List[float]:
        if self._embedder is not None:
            return self._embedder(query)
        # No explicit embedder injected. For a DB-backed dense retriever the
        # caller must provide one (see the debug endpoint); a fake/test
        # retriever typically ignores this value.
        return []

    def _run_dense(
        self, query: str, filters: Optional[Dict[str, Any]]
    ) -> List[RetrievalCandidate]:
        k = self.dense_retriever.resolve_k(None)
        try:
            return list(self.dense_retriever.search(self._embed(query), k, filters))
        except TypeError:
            # Defensive: some thin fakes accept filters positionally differently.
            return list(self.dense_retriever.search(self._embed(query), k))

    def _run_lexical(
        self, query: str, filters: Optional[Dict[str, Any]]
    ) -> List[RetrievalCandidate]:
        k = self.lexical_retriever.resolve_k(None)
        try:
            return list(self.lexical_retriever.search(query, k, filters))
        except TypeError:
            return list(self.lexical_retriever.search(query, k))

    def _run_identifier(
        self, query: str, filters: Optional[Dict[str, Any]]
    ) -> List[RetrievalCandidate]:
        k = self.identifier_retriever.resolve_k(None)
        try:
            return list(self.identifier_retriever.search(query, k, filters))
        except TypeError:
            return list(self.identifier_retriever.search(query, k))

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _assign_ranks(candidates: List[RetrievalCandidate]) -> List[RetrievalCandidate]:
        """Reassign 1-based ranks while preserving each candidate's source.

        ``RetrievalCandidate`` only accepts ``dense``/``lexical``/``identifier``
        as ``source`` (see ``retrieval/base.py``), so the rerank-stage label
        lives in the debug ``stages["rerank"]`` key (and fusion metadata)
        rather than in the source field itself.
        """
        out: List[RetrievalCandidate] = []
        for i, c in enumerate(candidates or [], start=1):
            out.append(
                RetrievalCandidate(
                    chunk_id=c.chunk_id,
                    rank=i,
                    score=c.score,
                    source=c.source,
                    metadata=dict(c.metadata or {}),
                )
            )
        return out

    def _attach_chunks(self, candidates: List[RetrievalCandidate]) -> None:
        for c in candidates or []:
            chunk = self._chunk_resolver(c.chunk_id)
            try:
                c.chunk = chunk  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                pass

    def _resolved_pool(self, candidates: List[RetrievalCandidate]) -> Dict[str, Any]:
        pool: Dict[str, Any] = {}
        for c in candidates or []:
            chunk = getattr(c, "chunk", None)
            if chunk is not None:
                pool[str(c.chunk_id)] = chunk
        return pool

    def _evidence(
        self,
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
        """
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
                        ident_score is not None and ident_score >= _EXACT_IDENTIFIER_SCORE
                    ),
                }
            )
        return evidence

    def _citations(self, ranked: List[RetrievalCandidate]) -> List[Dict[str, Any]]:
        citations: List[Dict[str, Any]] = []
        for i, c in enumerate(ranked or [], start=1):
            chunk = getattr(c, "chunk", None)
            meta = dict(c.metadata or {})
            chunk_meta = dict(getattr(chunk, "metadata", None) or {}) if chunk is not None else {}
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
                    "document_name": meta.get("document_name") or chunk_meta.get("document_name"),
                    "source_type": meta.get("source_type"),
                    "heading_path": heading,
                    "locator": locator,
                    "snippet": (getattr(chunk, "content", "") or "")[:200],
                }
            )
        return citations
