import math

import pytest

from metrics import (
    compute_retrieval_metrics,
    evaluate_quality_gate,
    latency_percentiles,
    mrr_at_10,
    ndcg_at_10,
    no_answer_fp_fn,
    recall_at_k,
)


def test_recall_at_k():
    ranked = ["a", "b", "c"]
    assert recall_at_k(ranked, ["a"], 1) is True
    assert recall_at_k(ranked, ["c"], 1) is False
    assert recall_at_k(ranked, ["c"], 3) is True
    assert recall_at_k(ranked, ["z"], 3) is False


def test_mrr_at_10():
    assert mrr_at_10(["x", "y", "z"], ["y"]) == 0.5
    assert mrr_at_10(["x", "y", "z"], ["z"]) == pytest.approx(1 / 3)
    assert mrr_at_10(["x"], ["y"]) == 0.0
    # relevant beyond k is not credited
    ranked = ["n1"] * 10 + ["target"]
    assert mrr_at_10(ranked, ["target"]) == 0.0


def test_ndcg_at_10_perfect():
    # Perfect ordering -> nDCG = 1.0
    assert math.isclose(ndcg_at_10(["a", "b", "c"], ["a", "b"]), 1.0, abs_tol=1e-9)


def test_ndcg_at_10_known_value():
    ranked = ["c", "a", "d", "b"]
    relevant = ["a", "b"]
    # DCG = 1/log2(3) + 1/log2(5) ; IDCG = 1/log2(2) + 1/log2(3)
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert math.isclose(ndcg_at_10(ranked, relevant), dcg / idcg, abs_tol=1e-9)


def test_ndcg_at_10_no_relevant_is_zero():
    assert ndcg_at_10(["a", "b"], []) == 0.0


def test_no_answer_fp_fn():
    golden = {"q1": False, "q2": True, "q3": True}
    predicted = {"q1": True, "q2": True, "q3": False}
    out = no_answer_fp_fn(predicted, golden)
    assert out["false_positives"] == 1
    assert out["false_negatives"] == 1
    assert out["n_golden_no_answer"] == 1
    assert out["n_golden_answerable"] == 2
    assert out["false_positive_rate"] == 1.0
    assert out["false_negative_rate"] == 0.5
    assert out["overall_error"] == pytest.approx(2 / 3)


def test_latency_percentiles():
    out = latency_percentiles([10, 20, 30, 40], (50, 95))
    assert out["p50_ms"] == 25.0
    assert out["p95_ms"] == pytest.approx(38.5)


def test_latency_percentiles_single():
    assert latency_percentiles([7])["p50_ms"] == 7.0
    assert latency_percentiles([7])["p95_ms"] == 7.0


def test_latency_empty():
    assert latency_percentiles([])["p50_ms"] is None


def test_compute_retrieval_metrics_aggregate():
    predictions = {
        "q1": ["a", "b", "c", "d"],
        "q2": ["x", "y"],
        "q3": ["1", "2", "3"],
    }
    labels = {
        "q1": {"answerable": True, "relevant": ["b", "d"]},
        "q2": {"answerable": True, "relevant": ["z"]},  # never found
        "q3": {"answerable": False, "relevant": []},    # excluded
    }
    m = compute_retrieval_metrics(predictions, labels)
    # only q1 and q2 are evaluable
    assert m["n_evaluable"] == 2
    assert m["recall@1"] == 0.0   # neither has a relevant at rank 1
    assert m["recall@3"] == 0.5   # q1 hits at rank2, q2 misses entirely
    assert m["recall@5"] == 0.5
    assert m["recall@10"] == 0.5
    # q1 mrr = 1/rank2 = 0.5, q2 mrr = 0 -> avg 0.25
    assert math.isclose(m["mrr@10"], 0.25, abs_tol=1e-9)


def test_evaluate_quality_gate_pass():
    metrics = {
        "recall@5": 0.9,
        "mrr@10": 0.8,
        "no_answer": {"false_positives": 1, "false_negatives": 0, "overall_error": 0.02},
    }
    gate = evaluate_quality_gate(metrics)
    assert gate["pass"] is True


def test_evaluate_quality_gate_fail_recall():
    metrics = {"recall@5": 0.7, "mrr@10": 0.8, "no_answer": {"overall_error": 0.0}}
    gate = evaluate_quality_gate(metrics)
    assert gate["pass"] is False
    assert any("recall@5" in r and "< " in r for r in gate["reasons"])


def test_evaluate_quality_gate_fail_mrr():
    metrics = {"recall@5": 0.9, "mrr@10": 0.5, "no_answer": {"overall_error": 0.0}}
    gate = evaluate_quality_gate(metrics)
    assert gate["pass"] is False
    assert any("mrr@10=" in r for r in gate["reasons"])
