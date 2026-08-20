"""Aşama 9 — generation evaluation metrics (tests/evals).

Pure functions scoring an *answer* against its *citations* and the golden
*expected sources* for a query (AKTIF_GOREV.md 9.3). No network / DB access,
fully unit-testable.

Roles
-----
``claims``     : the answer broken into atomic claims. Each claim is a dict
                 ``{"text": str, "citation": <source_id|None>}``. A ``citation``
                 of ``None`` means the claim is stated without any source.
``citations``  : the set of source ids the model cited (list of ids, or list of
                 dicts carrying a ``source`` key).
``expected_sources`` : the ids of sources the golden dataset says legitimately
                 answer the query (any other cited id is a fabrication).
``answer``     : the plain answer text (used for sufficiency).
``required_aspects`` : keywords/phrases the answer must cover to be sufficient.
``contradictory_pairs`` : source-id pairs that disagree; a citation that covers
                 BOTH members of a pair without hedging is a contradiction.

Metric definitions
------------------
- citation coverage: fraction of claims that cite at least one source.
- unsourced-claim rate: 1 - citation coverage (claims with no source).
- citation accuracy: fraction of cited sources present in expected_sources
  (penalizes fabricated / off-list citations).
- answer sufficiency: fraction of required aspects found in the answer text.
- contradictory-source behavior: 1.0 when no fully-cited contradictory pair is
  asserted as a single authoritative answer (i.e. version conflicts are handled
  — hedged or a single definitive version chosen); 0.0 when a contradiction is
  present without hedging.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


# --------------------------------------------------------------------------- #
# Individual generation metrics
# --------------------------------------------------------------------------- #
def _cited_sources(citations: Sequence[Any]) -> List[Any]:
    """Normalize a citations collection into a list of source ids."""
    out: List[Any] = []
    for c in citations or []:
        if isinstance(c, dict):
            out.append(c.get("source", c.get("source_id", c.get("id"))))
        else:
            out.append(c)
    return [c for c in out if c is not None]


def citation_coverage(claims: Sequence[Mapping[str, Any]]) -> float:
    """Fraction of claims that cite at least one source (0..1)."""
    claims = list(claims or [])
    if not claims:
        return 0.0
    covered = sum(1 for c in claims if c.get("citation") is not None)
    return covered / len(claims)


def unsourced_claim_rate(claims: Sequence[Mapping[str, Any]]) -> float:
    """Fraction of claims without any citation (1 - citation coverage)."""
    return 1.0 - citation_coverage(claims)


def citation_accuracy(citations: Sequence[Any], expected_sources: Sequence[Any]) -> float:
    """Fraction of cited sources that belong to the expected (golden) set."""
    cited = _cited_sources(citations)
    if not cited:
        return 1.0 if not expected_sources else 0.0
    expected = set(expected_sources)
    accurate = sum(1 for c in cited if c in expected)
    return accurate / len(cited)


def answer_sufficiency(answer: str, required_aspects: Sequence[str]) -> float:
    """Fraction of required aspects present in the answer (case-insensitive)."""
    required = list(required_aspects or [])
    if not required:
        return 1.0
    text = (answer or "").lower()
    covered = sum(1 for aspect in required if (aspect or "").lower() in text)
    return covered / len(required)


def contradictory_source_behavior(
    cited_sources: Sequence[Any],
    contradictory_pairs: Sequence[Tuple[Any, Any]],
    *,
    hedged: bool = False,
) -> float:
    """1.0 if contradictory sources are safely handled, else 0.0.

    A contradiction is flagged when any contradictory pair has *both* members
    present among the cited sources and the response is not hedged. Hedging
    (``hedged=True``) — e.g. the answer explicitly notes the version conflict —
    is treated as correct handling, so it returns 1.0.
    """
    cited = set(_cited_sources(cited_sources))
    for left, right in contradictory_pairs or []:
        if left in cited and right in cited:
            return 1.0 if hedged else 0.0
    return 1.0


# --------------------------------------------------------------------------- #
# Aggregated computation
# --------------------------------------------------------------------------- #
def compute_generation_metrics(sample: Mapping[str, Any]) -> Dict[str, Any]:
    """Compute the full generation metric set for one label/answer sample.

    ``sample`` supports keys: ``claims``, ``citations``, ``expected_sources``,
    ``answer``, ``required_aspects``, ``contradictory_pairs``, ``hedged``.
    """
    claims = list(sample.get("claims", []) or [])
    citations = sample.get("citations", [])
    expected_sources = list(sample.get("expected_sources", []) or [])
    answer = sample.get("answer", "")
    required_aspects = list(sample.get("required_aspects", []) or [])
    contradictory_pairs = list(sample.get("contradictory_pairs", []) or [])
    hedged = bool(sample.get("hedged", False))

    coverage = citation_coverage(claims)
    unsourced = unsourced_claim_rate(claims)
    accuracy = citation_accuracy(citations, expected_sources)
    sufficiency = answer_sufficiency(answer, required_aspects)
    contradiction = contradictory_source_behavior(citations, contradictory_pairs, hedged=hedged)

    return {
        "citation_coverage": coverage,
        "unsourced_claim_rate": unsourced,
        "citation_accuracy": accuracy,
        "answer_sufficiency": sufficiency,
        "contradictory_source_behavior": contradiction,
        "n_claims": len(claims),
        "n_citations": len(_cited_sources(citations)),
        "n_expected_sources": len(expected_sources),
    }
