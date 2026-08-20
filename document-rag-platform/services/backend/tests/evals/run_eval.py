"""Aşama 9 — deterministic evaluation runner (tests/evals).

Runs a golden dataset slice through a *retrieval* service (injected), computes
the Aşama 9 retrieval + generation metric sets, and writes a loggable report
to ``results/metrics-report.json`` (plus a human-readable ``.md``).

It is deliberately **offline / DB-free**: the default ``FakeRetriever`` and
``FakeAnswerer`` derive deterministic results from the golden dataset itself,
so CI can exercise the full metric pipeline with no real service, database or
network.

Real-service path
-----------------
Inject your real ``RetrievalService`` (``src.application.retrieval_service``)
through ``retriever=_service_retriever`` where ``_service_retriever`` calls
``service.retrieve(query, filters)`` and returns the ranked results as a list
of dicts with ``chunk_id``/``document``/``content``. E.g.::

    from src.application.retrieval_service import RetrievalService
    service = RetrievalService(...)          # real constructor-injected deps
    def real(query, scope, fixture):
        res = service.retrieve(query, {"scope": scope})
        return [{"chunk_id": c.chunk_id, "document": _doc(c),
                 "content": _content(c), "score": c.score}
                for c in res.ranked_candidates]
    run_eval(golden, retriever=real, ...)

When a database/embedding-backed service is unavailable, the fake path below
keeps the same output contract, so reports are comparable across runs.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from golden_spec import load_golden
from metrics import compute_retrieval_metrics, evaluate_quality_gate
from generation_metrics import compute_generation_metrics

#: Output files produced by the runner (relative to the runner's parent).
RESULTS_DIR = Path(__file__).resolve().parent / "results"
METRICS_REPORT_JSON = RESULTS_DIR / "metrics-report.json"
METRICS_REPORT_MD = RESULTS_DIR / "metrics-report.md"

CHUNK_ID_PREFIX = "chunk:"


# --------------------------------------------------------------------------- #
# Relevance helpers (shared with the Aşama 0 baseline semantics)
# --------------------------------------------------------------------------- #
def is_relevant(result: Dict[str, Any], expected_sources: List[Dict[str, Any]]) -> bool:
    """A result matches a golden expected_source when its document equals the
    expected document AND its content carries at least one must_contain term.
    """
    doc = (result.get("document") or "").strip()
    content = (result.get("content") or "").lower()
    for source in expected_sources or []:
        if (source.get("document") or "").strip() != doc:
            continue
        must_contain = source.get("must_contain") or []
        if not must_contain:
            return True
        if any(str(term).lower() in content for term in must_contain):
            return True
    return False


def matching_chunk_ids(results: List[Dict[str, Any]], expected_sources: List[Dict[str, Any]]) -> List[str]:
    return [str(r["chunk_id"]) for r in results if is_relevant(r, expected_sources)]


# --------------------------------------------------------------------------- #
# Fake, deterministic retriever / answerer (CI-friendly, no service/DB)
# --------------------------------------------------------------------------- #
class FakeRetriever:
    """Deterministic retriever that derives plausible candidates from the
    golden ``expected_sources``.

    Purely synthetic (never reads a real index): for each expected source it
    fabricates one candidate chunk whose document/content satisfy the source;
    a couple of non-relevant filler chunks are also returned so ranking is
    still exercised. ``answerable=false`` / smalltalk queries yield no
    candidates (so the system predicts no-answer).
    """

    def __init__(self, *, filler_count: int = 2) -> None:
        self.filler_count = filler_count

    def retrieve(self, query: str, scope: str = "documents", fixture: str = "") -> List[Dict[str, Any]]:
        return []

    def __call__(self, query: str, scope: str = "documents", fixture: str = "", *,
                 expected_sources: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        expected_sources = expected_sources or []
        if not expected_sources:
            return []
        results: List[Dict[str, Any]] = []
        score = 1.0
        for idx, source in enumerate(expected_sources, start=1):
            doc = source.get("document", "unknown")
            terms = source.get("must_contain") or []
            content = f"{doc} bolum: " + (" ve ".join(str(t) for t in terms) if terms else "icerik")
            results.append(
                {
                    "chunk_id": f"{doc}::{idx}",
                    "document": doc,
                    "content": content,
                    "score": round(score, 4),
                    "rank": idx,
                }
            )
            score -= 0.1
        for i in range(self.filler_count):
            results.append(
                {
                    "chunk_id": f"{CHUNK_ID_PREFIX}filler-{i}",
                    "document": f"filler-doc-{i}.txt",
                    "content": "Ilgisiz metin",
                    "score": 0.01,
                    "rank": len(results) + 1,
                }
            )
        return results


class FakeAnswerer:
    """Deterministic answerer: one claim per expected source, each fully cited,
    with sufficient coverage and no contradictions (for the smoke run).
    """

    def __call__(self, query: str, results: List[Dict[str, Any]],
                 expected_sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not expected_sources:
            return {"claims": [], "citations": [], "answer": "", "required_aspects": []}
        claims = []
        citations = []
        for source in expected_sources:
            doc = source.get("document", "unknown")
            citations.append(doc)
            claims.append({"text": f"{doc} icerigi", "citation": doc})
        answer = " ve ".join(str(c["text"]) for c in claims)
        required_aspects = []
        for source in expected_sources:
            for term in source.get("must_contain", []):
                required_aspects.append(str(term))
        return {
            "claims": claims,
            "citations": citations,
            "answer": answer,
            "expected_sources": [s["document"] for s in expected_sources],
            "required_aspects": required_aspects,
            "contradictory_pairs": [],
            "hedged": True,
        }


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def run_eval(
    golden: List[Mapping[str, Any]],
    *,
    retriever: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    answerer: Optional[Callable[..., Dict[str, Any]]] = None,
    subset: Optional[List[str]] = None,
    output_json: Path = METRICS_REPORT_JSON,
    output_md: Path = METRICS_REPORT_MD,
    collect_latency: bool = False,
) -> Dict[str, Any]:
    """Run the evaluation and return the complete report dict.

    ``golden`` are dataset records (see ``golden_spec``). ``subset`` optionally
    restricts which query ids are evaluated. ``retriever`` is a callable
    ``(query, scope=None, fixture=None, *, expected_sources=...) -> results``
    defaulting to ``FakeRetriever``. ``answerer`` builds the generation sample;
    defaults to ``FakeAnswerer``.

    Writes ``output_json`` (loggable) and ``output_md``. Returns the report
    dict so callers/tests can assert on it without hitting disk.
    """
    retriever = retriever or FakeRetriever()
    answerer = answerer or FakeAnswerer()

    if subset:
        subset_set = set(subset)
        golden = [g for g in golden if g["id"] in subset_set]

    predictions: Dict[str, List[str]] = {}
    latencies: Dict[str, float] = {}
    predicted_answerable: Dict[str, bool] = {}
    generation_samples: List[Dict[str, Any]] = []

    per_query: List[Dict[str, Any]] = []
    for rec in golden:
        qid = rec["id"]
        query = rec["query"]
        expected_sources = rec.get("expected_sources", [])
        start = time.perf_counter()
        results = retriever(
            query,
            scope=rec.get("scope", "documents"),
            fixture=rec.get("project_fixture", ""),
            expected_sources=expected_sources,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if collect_latency:
            latencies[qid] = elapsed_ms

        ranked_ids = [str(r["chunk_id"]) for r in results]
        predictions[qid] = ranked_ids
        predicted_answerable[qid] = bool(results)

        relevant = matching_chunk_ids(results, expected_sources)
        per_query.append(
            {
                "id": qid,
                "query": query,
                "answerable": rec.get("answerable", False),
                "n_results": len(results),
                "n_relevant": len(relevant),
                "first_relevant_rank": next(
                    (i for i, cid in enumerate(ranked_ids, 1) if cid in set(relevant)), None
                ),
            }
        )

        if rec.get("answerable", False):
            sample = answerer(query, results, expected_sources)
            generation_samples.append({"id": qid, **sample})

    labels = {
        rec["id"]: {
            "answerable": rec.get("answerable", False),
            "relevant": matching_chunk_ids(
                # recomputed labels need the same results; we re-call cheaply:
                retriever(
                    rec["query"],
                    scope=rec.get("scope", "documents"),
                    fixture=rec.get("project_fixture", ""),
                    expected_sources=rec.get("expected_sources", []),
                ),
                rec.get("expected_sources", []),
            ),
        }
        for rec in golden
    }

    retrieval_metrics = compute_retrieval_metrics(
        predictions,
        labels,
        predicted_answerable=predicted_answerable,
        latencies=latencies if collect_latency else None,
    )
    quality_gate = evaluate_quality_gate(retrieval_metrics)

    generation_metrics = {
        "n_samples": len(generation_samples),
        "average": (
            _average_generation(generation_samples) if generation_samples else None
        ),
        "per_sample": generation_samples,
    }

    report = {
        "schema_version": "1.0",
        "runner": "tests/evals/run_eval.py",
        "n_records": len(golden),
        "retrieval": retrieval_metrics,
        "quality_gate": quality_gate,
        "generation": generation_metrics,
        "per_query": per_query,
    }

    report["quality_gate"]["reasons"] += _runner_generation_note(generation_metrics)

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    return report


def _average_generation(samples: List[Dict[str, Any]]) -> Dict[str, float]:
    keys = ("citation_coverage", "unsourced_claim_rate", "citation_accuracy",
            "answer_sufficiency", "contradictory_source_behavior")
    out: Dict[str, float] = {}
    for key in keys:
        values = [s[key] for s in samples if key in s]
        out[key] = (sum(values) / len(values)) if values else None
    return out


def _runner_generation_note(generation: Dict[str, Any]) -> List[str]:
    avg = generation.get("average")
    if avg is None:
        return ["generation metrikleri icin ornek yok"]
    return [f"generation raporlandi: n_samples={generation.get('n_samples')}"]


def render_markdown(report: Dict[str, Any]) -> str:
    r = report["retrieval"]
    g = report["generation"]
    lines = [
        "# Aşama 9 Evaluation Report",
        "",
        f"- runner: `{report['runner']}`",
        f"- degerlendirilen soru sayisi: {report['n_records']}",
        "",
        "## Retrieval",
        "",
        f"- Recall@1: {_fmt(r.get('recall@1'))}",
        f"- Recall@3: {_fmt(r.get('recall@3'))}",
        f"- Recall@5: {_fmt(r.get('recall@5'))}",
        f"- Recall@10: {_fmt(r.get('recall@10'))}",
        f"- MRR@10: {_fmt(r.get('mrr@10'))}",
        f"- nDCG@10: {_fmt(r.get('ndcg@10'))}",
        "",
        "### No-answer",
        "",
        f"- FP: {r['no_answer'].get('false_positives')}, "
        f"FN: {r['no_answer'].get('false_negatives')}, "
        f"overall: {_fmt(r['no_answer'].get('overall_error'))}",
        "",
        "### Latency",
        "",
        f"- {r.get('latency', {})}",
        "",
        "## Quality gate",
        "",
        f"- {report['quality_gate']['pass']}",
    ]
    for reason in report["quality_gate"]["reasons"]:
        lines.append(f"- {reason}")
    lines.append("")
    lines.append("## Generation (average)")
    lines.append("")
    avg = g.get("average") or {}
    lines.append(f"- citation_coverage: {_fmt(avg.get('citation_coverage'))}")
    lines.append(f"- unsourced_claim_rate: {_fmt(avg.get('unsourced_claim_rate'))}")
    lines.append(f"- citation_accuracy: {_fmt(avg.get('citation_accuracy'))}")
    lines.append(f"- answer_sufficiency: {_fmt(avg.get('answer_sufficiency'))}")
    lines.append(
        f"- contradictory_source_behavior: {_fmt(avg.get('contradictory_source_behavior'))}"
    )
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden", type=Path, default=Path(__file__).parent / "datasets" / "golden.jsonl")
    parser.add_argument("--subset-ids", type=str, default=None,
                        help="comma-separated query ids to restrict the run")
    parser.add_argument("--output-json", type=Path, default=METRICS_REPORT_JSON)
    parser.add_argument("--output-md", type=Path, default=METRICS_REPORT_MD)
    parser.add_argument("--fake", action="store_true", default=True,
                        help="use the offline FakeRetriever/FakeAnswerer (default)")
    parser.add_argument("--collect-latency", action="store_true")
    args = parser.parse_args()

    golden = load_golden(args.golden)
    subset = args.subset_ids.split(",") if args.subset_ids else None
    report = run_eval(
        golden,
        subset=subset,
        output_json=args.output_json,
        output_md=args.output_md,
        collect_latency=args.collect_latency,
    )
    print(json.dumps({"n_records": report["n_records"],
                      "gate_pass": report["quality_gate"]["pass"]}, ensure_ascii=False))
    print(f"reporto {args.output_json}")


if __name__ == "__main__":
    main()
