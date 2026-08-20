"""Aşama 9 — retrieval evaluation metrics (tests/evals).

Pure functions computing standard ranking metrics from *ranked candidate id* \
mappings, with no network or database access, so they are unit-testable in CI.

Metric definitions (see AKTIF_GOREV.md 9.2):

- ``recall@k``: did at least one expected (relevant) source appear in the top-k
  ranked candidates.
- ``mrr@10``: mean reciprocal rank of the first relevant candidate (capped at
  10).
- ``ndcg@10``: normalized discounted cumulative gain over the top-10 ranked
  candidates with binary (0/1) relevance and the standard
  ``log2(rank + 1)`` discount.
- no-answer FP/FN: a false positive is *system replies with an answer but the
  golden label says no-answer*; a false negative is *system returns no-answer
  but the golden label says answerable*.
- retrieval latency p50/p95 over per-query latency measurements.

The primary entry point is ``compute_retrieval_metrics(predictions, labels)``
which returns the full metric dict; ``evaluate_quality_gate`` tests the
initial quality gate (Recall@5 >= 0.85, MRR@10 >= 0.75).
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Mapping, Optional, Sequence


# --------------------------------------------------------------------------- #
# Individual ranking metrics
# --------------------------------------------------------------------------- #
def recall_at_k(ranked_ids: Sequence[str], relevant_ids: Sequence[str], k: int) -> bool:
    """True if any expected id is present within the top-k ranked ids."""
    top_k = list(ranked_ids)[: max(k, 0)]
    relevant = set(relevant_ids)
    return any(cid in relevant for cid in top_k)


def mrr_at_10(ranked_ids: Sequence[str], relevant_ids: Sequence[str], k: int = 10) -> float:
    """Reciprocal rank of the first relevant id (0 if none or beyond k)."""
    relevant = set(relevant_ids)
    for rank, cid in enumerate(ranked_ids[:max(k, 0)], start=1):
        if cid in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_10(ranked_ids: Sequence[str], relevant_ids: Sequence[str], k: int = 10) -> float:
    """nDCG@10 with binary relevance and log2(rank+1) discount.

    IDCG is the best-possible DCG (the number of relevant ids, each at the
    earliest rank). Returns 0.0 when there is nothing relevant to retrieve.
    """
    relevant = set(relevant_ids)
    top_k = list(ranked_ids)[:max(k, 0)]

    def dcg(sequence: Sequence[str]) -> float:
        total = 0.0
        for rank, cid in enumerate(sequence, start=1):
            if cid in relevant:
                total += 1.0 / math.log2(rank + 1)
        return total

    ideal_gain = sum(1.0 / math.log2(i + 1) for i in range(1, len(relevant) + 1))
    if ideal_gain <= 0.0:
        return 0.0
    return dcg(top_k) / ideal_gain


# --------------------------------------------------------------------------- #
# No-answer metrics
# --------------------------------------------------------------------------- #
def no_answer_fp_fn(
    predicted_answerable: Mapping[str, bool],
    golden_answerable: Mapping[str, bool],
) -> Dict[str, Any]:
    """Compute no-answer false positives / false negatives and their rates.

    Labels boolean per query id. Returns fp/fn counts plus rates over the
    relevant denominators (fp rate over golden no-answer queries; fn rate over
    golden answerable queries) so they are comparable even with skewed data.
    """
    fp = fn = 0
    n_golden_no_answer = n_golden_answerable = 0
    for qid, golden in golden_answerable.items():
        predicted = bool(predicted_answerable.get(qid, False))
        if not golden:
            n_golden_no_answer += 1
            if predicted:
                fp += 1
        else:
            n_golden_answerable += 1
            if not predicted:
                fn += 1
    return {
        "false_positives": fp,              # system answered, golden says no-answer
        "false_negatives": fn,              # system no-answer, golden answerable
        "n_golden_no_answer": n_golden_no_answer,
        "n_golden_answerable": n_golden_answerable,
        "false_positive_rate": (fp / n_golden_no_answer) if n_golden_no_answer else None,
        "false_negative_rate": (fn / n_golden_answerable) if n_golden_answerable else None,
        "overall_error": (
            (fp + fn) / (n_golden_no_answer + n_golden_answerable)
            if (n_golden_no_answer + n_golden_answerable)
            else None
        ),
    }


# --------------------------------------------------------------------------- #
# Latency helpers
# --------------------------------------------------------------------------- #
def latency_percentiles(latencies: Sequence[float], percentiles: Sequence[int] = (50, 95)) -> Dict[str, Optional[float]]:
    """p-th percentiles over the given latencies in ms (deterministic)."""
    values = sorted(float(v) for v in latencies)
    if not values:
        return {f"p{p}_ms": None for p in percentiles}
    out: Dict[str, Optional[float]] = {}
    for p in percentiles:
        if p <= 0:
            out[f"p{p}_ms"] = values[0]
        elif p >= 100:
            out[f"p{p}_ms"] = values[-1]
        else:
            idx = (p / 100.0) * (len(values) - 1)
            lower = int(math.floor(idx))
            upper = int(math.ceil(idx))
            if lower == upper:
                out[f"p{p}_ms"] = float(values[lower])
            else:
                frac = idx - lower
                out[f"p{p}_ms"] = float(values[lower] * (1 - frac) + values[upper] * frac)
    return out


# --------------------------------------------------------------------------- #
# Label / prediction normalization
# --------------------------------------------------------------------------- #
def _normalize_golden(qid: str, label: Any) -> Dict[str, Any]:
    """Normalize a gold label into {'answerable': bool, 'relevant': list}."""
    if isinstance(label, dict):
        relevant = label.get("relevant", label.get("expected_source_ids", label.get("expected_sources", [])))
        answerable = bool(label.get("answerable", bool(relevant)))
        return {"answerable": answerable, "relevant": list(relevant)}
    # A bare list is treated as the relevant ids (answerable => non-empty).
    return {"answerable": bool(label), "relevant": list(label)}


def _predicted_answerability(query_ids: Sequence[str], predictions: Mapping[str, Sequence[str]]) -> Dict[str, bool]:
    """A query is considered "answered" when its ranked candidate list is non-empty."""
    return {qid: bool(predictions.get(qid)) for qid in query_ids}


# --------------------------------------------------------------------------- #
# Aggregated computation
# --------------------------------------------------------------------------- #
def compute_retrieval_metrics(
    predictions: Mapping[str, Sequence[str]],
    labels: Mapping[str, Any],
    *,
    predicted_answerable: Optional[Mapping[str, bool]] = None,
    latencies: Optional[Sequence[float] | Mapping[str, float]] = None,
    ks: Sequence[int] = (1, 3, 5, 10),
) -> Dict[str, Any]:
    """Compute the full retrieval metric set from id mappings.

    ``predictions``:  ``{query_id: [candidate_id, ...]}`` (best first).
    ``labels``:       ``{query_id: {"answerable": bool, "relevant": [ids]}}``
                      (a bare list is treated as the relevant ids).

    Recall/MRR/nDCG are averaged over golden answerable queries with at least
    one expected source. ``latencies`` may be a list of ms values or a
    ``{query_id: ms}`` map. Returns a flat, human/CI-readable dict.
    """
    normalized = {qid: _normalize_golden(qid, lbl) for qid, lbl in labels.items()}

    by_k: Dict[str, List[bool]] = {f"recall@{k}": [] for k in ks}
    mrr_values: List[float] = []
    ndcg_values: List[float] = []
    n_evaluable = 0

    for qid, gold in normalized.items():
        if not gold["answerable"] or not gold["relevant"]:
            continue
        ranked = list(predictions.get(qid, []))
        relevant = gold["relevant"]
        n_evaluable += 1
        for k in ks:
            by_k[f"recall@{k}"].append(recall_at_k(ranked, relevant, k))
        mrr_values.append(mrr_at_10(ranked, relevant))
        ndcg_values.append(ndcg_at_10(ranked, relevant, k=10))

    metrics: Dict[str, Any] = {
        "n_queries": len(labels),
        "n_evaluable": n_evaluable,
        "n_golden_no_answer": sum(1 for g in normalized.values() if not g["answerable"]),
    }
    for k in ks:
        key = f"recall@{k}"
        metrics[key] = (sum(by_k[key]) / n_evaluable) if n_evaluable else None
    metrics["mrr@10"] = (sum(mrr_values) / n_evaluable) if n_evaluable else None
    metrics["ndcg@10"] = (sum(ndcg_values) / n_evaluable) if n_evaluable else None

    # No-answer confusion ----------------------------------------------------
    if predicted_answerable is not None:
        golden_as = {qid: g["answerable"] for qid, g in normalized.items()}
        metrics["no_answer"] = no_answer_fp_fn(predicted_answerable, golden_as)
    else:
        predicted_as = _predicted_answerability(list(normalized.keys()), predictions)
        golden_as = {qid: g["answerable"] for qid, g in normalized.items()}
        metrics["no_answer"] = no_answer_fp_fn(predicted_as, golden_as)

    # Latency ---------------------------------------------------------------
    if isinstance(latencies, Mapping):
        metrics["latency"] = latency_percentiles(list(latencies.values()))
    elif latencies:
        metrics["latency"] = latency_percentiles(list(latencies))
    else:
        metrics["latency"] = latency_percentiles([])

    return metrics


# --------------------------------------------------------------------------- #
# Quality gate
# --------------------------------------------------------------------------- #
def evaluate_quality_gate(
    metrics: Dict[str, Any],
    *,
    recall_k: int = 5,
    recall_threshold: float = 0.85,
    mrr_threshold: float = 0.75,
) -> Dict[str, Any]:
    """Evaluate the initial quality gate from ``compute_retrieval_metrics``.

    Gate: Recall@{recall_k} >= ``recall_threshold`` and MRR@10 >= ``mrr_threshold``.
    Returns ``{"pass": bool, "reasons": [str, ...]}`` where reasons describe
    every unmet gate (plus a reporting note for the no-answer error rate,
    which AKTIF_GOREV.md 9.2 requires to be measured and reported but not as a
    hard gate).
    """
    reasons: List[str] = []

    recall_key = f"recall@{recall_k}"
    recall = metrics.get(recall_key)
    if recall is None:
        reasons.append(f"{recall_key} olculemedi (n_evaluable sinirli)")
    elif recall < recall_threshold:
        reasons.append(
            f"{recall_key}={recall:.3f} < esik {recall_threshold} "
            f"(hedef {recall_key} >= {recall_threshold})"
        )

    mrr = metrics.get("mrr@10")
    if mrr is None:
        reasons.append("mrr@10 olculemedi")
    elif mrr < mrr_threshold:
        reasons.append(
            f"mrr@10={mrr:.3f} < esik {mrr_threshold} (hedef mrr@10 >= {mrr_threshold})"
        )

    no_answer = metrics.get("no_answer")
    if no_answer:
        reasons.append(
            "no-answer hata orani raporlandi: "
            f"fp={no_answer.get('false_positives')}, "
            f"fn={no_answer.get('false_negatives')}, "
            f"overall={no_answer.get('overall_error')}"
        )
    else:
        reasons.append("no-answer metrikleri raporlanmadi (eksik)")

    return {"pass": not any(r.startswith(("recall@", "mrr@10=")) for r in reasons), "reasons": reasons}


def load_jsonl(path) -> List[Dict[str, Any]]:
    """Load a JSONL file of evaluation artifacts (baseline runner reuse)."""
    import json as _json

    from pathlib import Path as _Path
    records: List[Dict[str, Any]] = []
    with _Path(path).open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(_json.loads(line))
            except _json.JSONDecodeError as exc:
                raise ValueError(f"{_Path(path)}:{line_no}: {exc}") from exc
    return records
