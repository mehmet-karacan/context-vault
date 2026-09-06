"""Reciprocal Rank Fusion (Aşama 5.3).

Pure, deterministic, DB-free. ``reciprocal_rank_fusion`` fuses any number of
ranked id lists — one each from the dense, lexical and identifier retrievers —
into a single unique ordering by RRF score:

    RRF_score(d) = sum over retriever r of  1 / (k + rank_r(d))

where ``rank_r(d)`` is the 1-based position of chunk ``d`` in retriever ``r``
(or infinity if absent). Because it runs on rank rather than raw score, it
remains valid even though dense (cosine) and lexical (ts_rank) scores live on
incompatible scales — that is exactly the point of AKTIF_GOREV.md 5.3
("ham skorları doğrudan toplama; farklı skor ölçeklerini rank üzerinden
birleştir").

Duplicates *across* lists are handled naturally: a chunk appearing in several
retrievers accumulates a higher RRF score, and appearing multiple times *within*
one list is collapsed because we iterate by rank once per position. The output
order is deterministic: descending RRF score, ties broken by ascending
``chunk_id`` (string comparison).

``fuse`` is a thin convenience wrapper that operates on lists of
``RetrievalCandidate`` and yields fused ``RetrievalCandidate`` objects, so it
can be dropped straight into the pipeline while ``reciprocal_rank_fusion``
remains the tiny, unit-testable core.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .base import RetrievalCandidate
from src.domain.retrieval import FusedHit, RetrieverContribution, RetrieverHit


def reciprocal_rank_fusion(
    lists: Sequence[Iterable[Any]],
    k: int = 60,
) -> List[Tuple[Any, float]]:
    """Fuse ranked id lists by Reciprocal Rank Fusion.

    Parameters
    ----------
    lists: any number of ranked sequences; each sequence's items are the
        stable keys (chunk ids) in best-first order. Items need only be
        hashable and comparable (strings are typical).
    k: RRF constant (AKTIF_GOREV.md 5.3, RRF_K=60). Larger ``k`` damps the
        advantage of a high rank in a single list.

    Returns
    -------
    List[(key, rrf_score)] sorted by descending RRF score with deterministic,
    unique keys.
    """
    if k <= 0:
        raise ValueError("k must be positive")

    scores: Dict[Any, float] = {}
    for ranked in lists:
        seen_in_retriever: set[Any] = set()
        for rank, key in enumerate(ranked, start=1):
            if key in seen_in_retriever:
                continue
            seen_in_retriever.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)

    # Deterministic: higher score first; ties broken by ascending key.
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return ordered


def fuse(
    candidate_lists: Sequence[Sequence[RetrievalCandidate]],
    k: int = 60,
) -> List[FusedHit]:
    """Fuse lists of ``RetrievalCandidate`` into one fused, ranked list.

    Re-emits candidates keyed by ``chunk_id``. When a chunk id appears in
    multiple retriever lists its fused score accumulates as the sum of RRF
    contributions; the returned candidate keeps the metadata of its first
    (highest-ranked) occurrence and records every contributing source.
    """
    order = reciprocal_rank_fusion(
        ([c.chunk_id for c in ranked] for ranked in candidate_lists),
        k=k,
    )
    fused_by_key: Dict[Any, RetrieverHit] = {}
    contributions: Dict[Any, Dict[str, RetrieverContribution]] = {}
    for ranked in candidate_lists:
        for c in ranked:
            if c.chunk_id not in fused_by_key:
                fused_by_key[c.chunk_id] = c
            per_hit = contributions.setdefault(c.chunk_id, {})
            if c.source in per_hit:
                continue
            per_hit[c.source] = RetrieverContribution(
                retriever_name=c.source,
                retriever_rank=c.rank,
                raw_score=c.score,
                rrf_contribution=1.0 / (k + c.rank),
                match_type=c.match_type,
                matched_terms=tuple(c.matched_terms),
            )

    result: List[FusedHit] = []
    for key, rrf_score in order:
        result.append(
            FusedHit(
                representative=fused_by_key[key],
                per_retriever_contributions=contributions[key],
                rrf_score=rrf_score,
                fusion_rank=len(result) + 1,
            )
        )
    return result


def dedupe(
    candidates: Sequence[FusedHit],
    content_key: str = "content_hash",
) -> List[FusedHit]:
    """Drop duplicate *content* copies while keeping the highest-ranked one.

    Fusion already dedupes by ``chunk_id``; this handles the distinct-id case
    where two different chunks hold byte-identical content (AKTIF_GOREV.md
    5.3: "aynı içeriğin farklı kopyalarını temizle"). Chunks without a
    ``content_hash`` in metadata are never deduped (each kept on its own).
    Stable order is preserved: the first occurrence of a hash is kept.
    """
    seen: Dict[Any, str] = {}
    out: List[FusedHit] = []
    for c in candidates:
        h = c.content_hash or (c.metadata or {}).get(content_key)
        if h is None:
            out.append(c)
            continue
        if h in seen:
            continue
        seen[h] = c.chunk_id
        out.append(c)
    return out
