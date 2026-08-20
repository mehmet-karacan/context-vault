import pytest

from generation_metrics import (
    answer_sufficiency,
    citation_accuracy,
    citation_coverage,
    compute_generation_metrics,
    contradictory_source_behavior,
    unsourced_claim_rate,
)


def test_citation_coverage():
    claims = [
        {"text": "a", "citation": "rules.docx"},
        {"text": "b", "citation": None},
        {"text": "c", "citation": "rules.docx"},
    ]
    assert citation_coverage(claims) == pytest.approx(2 / 3)
    assert citation_coverage([]) == 0.0


def test_unsourced_claim_rate_is_complement():
    claims = [
        {"text": "a", "citation": "x"},
        {"text": "b", "citation": None},
        {"text": "c", "citation": None},
    ]
    assert unsourced_claim_rate(claims) == pytest.approx(2 / 3)
    assert unsourced_claim_rate(claims) == pytest.approx(1 - citation_coverage(claims))


def test_citation_accuracy_no_fabrication():
    citations = ["rules.docx", "pricing-table.docx"]
    expected = ["rules.docx", "pricing-table.docx"]
    assert citation_accuracy(citations, expected) == 1.0


def test_citation_accuracy_fabrication_penalized():
    citations = [{"source": "rules.docx"}, {"source": "fabricated.docx"}]
    expected = ["rules.docx"]
    assert citation_accuracy(citations, expected) == 0.5


def test_citation_accuracy_none_cited():
    assert citation_accuracy([], ["rules.docx"]) == 0.0
    assert citation_accuracy([], []) == 1.0


def test_answer_sufficiency():
    assert answer_sufficiency("Faturalama 30 gundur ve iade 14 gunde yapilir.",
                              ["faturalama", "iade"]) == 1.0
    assert answer_sufficiency("Yalnizca faturalama hakkinda.", ["faturalama", "iade"]) == 0.5
    assert answer_sufficiency("", ["faturalama"]) == 0.0


def test_contradictory_source_behavior_clean():
    # Only one member of the conflicting pair cited -> handled safely.
    assert contradictory_source_behavior(["sla-v1.pdf"], [("sla-v1.pdf", "sla-v2.pdf")]) == 1.0


def test_contradictory_source_behavior_both_cited_requires_hedge():
    cited = ["sla-v1.pdf", "sla-v2.pdf"]
    pairs = [("sla-v1.pdf", "sla-v2.pdf")]
    assert contradictory_source_behavior(cited, pairs, hedged=False) == 0.0
    assert contradictory_source_behavior(cited, pairs, hedged=True) == 1.0


def test_compute_generation_metrics():
    sample = {
        "claims": [
            {"text": "Faturalama 30 gundur", "citation": "rules.docx"},
            {"text": "Iade 14 gunde yapilir", "citation": None},
        ],
        "citations": ["rules.docx", "fabricated.docx"],
        "expected_sources": ["rules.docx"],
        "answer": "Faturalama 30 gundur.",
        "required_aspects": ["faturalama", "iade"],
        "contradictory_pairs": [],
        "hedged": True,
    }
    out = compute_generation_metrics(sample)
    assert out["citation_coverage"] == 0.5
    assert out["unsourced_claim_rate"] == 0.5
    assert out["citation_accuracy"] == 0.5
    assert out["answer_sufficiency"] == 0.5
    assert out["contradictory_source_behavior"] == 1.0
