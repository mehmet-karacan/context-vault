import json
from pathlib import Path

from golden_spec import load_golden
from run_eval import FakeRetriever, run_eval

GOLDEN = Path(__file__).parent / "datasets" / "golden.jsonl"


def test_runner_produces_report_dict(tmp_path):
    golden = load_golden(GOLDEN)
    subset = [g["id"] for g in golden if g.get("answerable")][:5]
    out_json = tmp_path / "metrics-report.json"
    out_md = tmp_path / "metrics-report.md"

    report = run_eval(golden, subset=subset, output_json=out_json, output_md=out_md)

    assert report["n_records"] == len(subset)
    assert report["retrieval"]["recall@5"] is not None
    assert "quality_gate" in report
    assert "generation" in report


def test_runner_writes_loggable_json(tmp_path):
    golden = load_golden(GOLDEN)
    subset = [g["id"] for g in golden if g.get("answerable")][:3]
    out_json = tmp_path / "metrics-report.json"
    report = run_eval(golden, subset=subset, output_json=out_json, output_md=tmp_path / "r.md")

    assert out_json.is_file()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["retrieval"]["mrr@10"] == report["retrieval"]["mrr@10"]
    assert data["quality_gate"]["pass"] is True


def test_fake_retriever_is_deterministic_and_relevant():
    fake = FakeRetriever()
    expected = [{"document": "rules.docx", "must_contain": ["faturalama"]}]
    results1 = fake("soru", expected_sources=expected)
    results2 = fake("soru", expected_sources=expected)
    assert results1 == results2
    assert any("rules.docx" in r["content"] for r in results1)


def test_fake_retriever_returns_nothing_for_no_answer():
    fake = FakeRetriever()
    assert fake("merhaba", scope="none", expected_sources=[]) == []


def test_full_fake_run_passes_quality_gate(tmp_path):
    golden = load_golden(GOLDEN)
    out_json = tmp_path / "metrics-report.json"
    report = run_eval(golden, output_json=out_json, output_md=tmp_path / "r.md")
    assert report["quality_gate"]["pass"] is True
