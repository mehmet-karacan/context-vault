"""Aşama 0 baseline retrieval metrikleri.

Girdi dosyaları:
  - tests/evals/golden/questions.jsonl      (golden dataset; bkz. AKTIF_GOREV.md 9.1 formatı)
  - tests/evals/baseline/retrieval-top10.jsonl (her soru için mevcut sistemin top-10 retrieval sonucu)

Golden dataset kaydı (beklenen alanlar):
    {
      "id": str,
      "query": str,
      "project_id": str | null,
      "project_fixture": str,
      "scope": str,
      "answerable": bool,
      "expected_sources": [{"document": str, "must_contain": [str, ...]}, ...],
      "tags": [str, ...]
    }

Retrieval kaydı (beklenen alanlar):
    {
      "id": str,                 # questions.jsonl içindeki id ile eşleşir
      "query": str,
      "results": [
        {"rank": int, "chunk_id": str, "document": str, "chunk_index": int,
         "score": float, "content": str},
        ...                      # en fazla 10 eleman, rank artan sırada (1..10)
      ]
    }

Bir retrieval sonucu şu durumda "relevant" (alakalı) sayılır:
  - result["document"] == expected_sources[i]["document"]  VE
  - expected_sources[i]["must_contain"] listesindeki terimlerden EN AZ BİRİ
    (must_contain boşsa bu koşul aranmaz) result["content"] içinde
    büyük/küçük harf duyarsız şekilde geçiyorsa.

Yalnızca answerable=true VE expected_sources dolu olan sorular Recall/MRR
hesabına dahil edilir. Cevapsız (answerable=false) sorular ayrı sayılır ve
raporda "no-answer" bölümünde listelenir; bu görev aşamasında sistemde henüz
answerability sınıflandırması olmadığından bunlar için recall hesaplanmaz.

Bu script hiçbir ağ çağrısı yapmaz, saf JSONL girdisi üzerinde çalışır ve
birim testle doğrulanabilir.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: gecersiz JSON satiri: {exc}") from exc
    return records


def is_relevant(result: dict[str, Any], expected_sources: list[dict[str, Any]]) -> bool:
    """Bir tekil retrieval sonucunun golden expected_sources'tan birine uyup uymadigini doner."""
    doc = (result.get("document") or "").strip()
    content = (result.get("content") or "")
    content_lower = content.lower()
    for source in expected_sources:
        if source.get("document") != doc:
            continue
        must_contain = source.get("must_contain") or []
        if not must_contain:
            return True
        if any(term.lower() in content_lower for term in must_contain):
            return True
    return False


def first_relevant_rank(results: list[dict[str, Any]], expected_sources: list[dict[str, Any]]) -> int | None:
    """1-indexed sirada ilk alakali sonucun rankini doner, yoksa None."""
    ordered = sorted(results, key=lambda r: r.get("rank", 0))
    for r in ordered:
        if is_relevant(r, expected_sources):
            return r.get("rank")
    return None


def recall_at_k(results: list[dict[str, Any]], expected_sources: list[dict[str, Any]], k: int) -> bool:
    ordered = sorted(results, key=lambda r: r.get("rank", 0))
    top_k = [r for r in ordered if r.get("rank", 0) <= k]
    return any(is_relevant(r, expected_sources) for r in top_k)


def mrr_at_k(results: list[dict[str, Any]], expected_sources: list[dict[str, Any]], k: int) -> float:
    rank = first_relevant_rank(results, expected_sources)
    if rank is None or rank > k:
        return 0.0
    return 1.0 / rank


def compute_metrics(
    questions: list[dict[str, Any]],
    retrievals: list[dict[str, Any]],
) -> dict[str, Any]:
    retrieval_by_id = {r["id"]: r for r in retrievals}

    evaluable = []
    skipped_no_answer = []
    skipped_no_retrieval = []

    per_question = []

    for q in questions:
        qid = q["id"]
        if not q.get("answerable", False) or not q.get("expected_sources"):
            skipped_no_answer.append(qid)
            continue
        retrieval = retrieval_by_id.get(qid)
        if retrieval is None:
            skipped_no_retrieval.append(qid)
            continue
        results = retrieval.get("results", [])
        expected_sources = q["expected_sources"]

        r1 = recall_at_k(results, expected_sources, 1)
        r3 = recall_at_k(results, expected_sources, 3)
        r5 = recall_at_k(results, expected_sources, 5)
        rank = first_relevant_rank(results, expected_sources)
        mrr = mrr_at_k(results, expected_sources, 10)

        evaluable.append(qid)
        per_question.append(
            {
                "id": qid,
                "query": q["query"],
                "recall@1": r1,
                "recall@3": r3,
                "recall@5": r5,
                "first_relevant_rank": rank,
                "mrr_contribution": mrr,
                "tags": q.get("tags", []),
            }
        )

    n = len(evaluable)
    aggregate = {
        "n_total_questions": len(questions),
        "n_evaluable": n,
        "n_skipped_no_answer_or_no_expected_sources": len(skipped_no_answer),
        "n_skipped_missing_retrieval": len(skipped_no_retrieval),
        "recall@1": (sum(1 for pq in per_question if pq["recall@1"]) / n) if n else None,
        "recall@3": (sum(1 for pq in per_question if pq["recall@3"]) / n) if n else None,
        "recall@5": (sum(1 for pq in per_question if pq["recall@5"]) / n) if n else None,
        "mrr@10": (sum(pq["mrr_contribution"] for pq in per_question) / n) if n else None,
    }

    return {
        "aggregate": aggregate,
        "per_question": per_question,
        "skipped_no_answer_or_no_expected_sources": skipped_no_answer,
        "skipped_missing_retrieval": skipped_no_retrieval,
    }


def render_markdown_report(metrics: dict[str, Any], golden_path: Path, retrieval_path: Path) -> str:
    agg = metrics["aggregate"]

    def fmt(v):
        return f"{v:.3f}" if isinstance(v, float) else "n/a"

    lines = []
    lines.append("# Baseline Retrieval Metrikleri (Aşama 0)")
    lines.append("")
    lines.append(f"- Golden dataset: `{golden_path.as_posix()}`")
    lines.append(f"- Retrieval sonuçları: `{retrieval_path.as_posix()}`")
    lines.append(f"- Toplam soru sayısı: {agg['n_total_questions']}")
    lines.append(f"- Recall/MRR hesabına dahil edilen soru sayısı: {agg['n_evaluable']}")
    lines.append(
        f"- Hesap dışı bırakılan (answerable=false veya expected_sources boş) soru sayısı: "
        f"{agg['n_skipped_no_answer_or_no_expected_sources']}"
    )
    if agg["n_skipped_missing_retrieval"]:
        lines.append(
            f"- UYARI: retrieval sonucu bulunamayan soru sayısı: {agg['n_skipped_missing_retrieval']}"
        )
    lines.append("")
    lines.append("## Toplam Metrikler")
    lines.append("")
    lines.append("| Metrik | Değer |")
    lines.append("|---|---|")
    lines.append(f"| Recall@1 | {fmt(agg['recall@1'])} |")
    lines.append(f"| Recall@3 | {fmt(agg['recall@3'])} |")
    lines.append(f"| Recall@5 | {fmt(agg['recall@5'])} |")
    lines.append(f"| MRR@10 | {fmt(agg['mrr@10'])} |")
    lines.append("")
    lines.append("## Soru Bazlı Sonuçlar")
    lines.append("")
    lines.append("| id | Recall@1 | Recall@3 | Recall@5 | İlk alakalı rank | MRR katkısı | query |")
    lines.append("|---|---|---|---|---|---|---|")
    for pq in metrics["per_question"]:
        lines.append(
            f"| {pq['id']} | {pq['recall@1']} | {pq['recall@3']} | {pq['recall@5']} | "
            f"{pq['first_relevant_rank']} | {pq['mrr_contribution']:.3f} | {pq['query']} |"
        )
    lines.append("")
    if metrics["skipped_no_answer_or_no_expected_sources"]:
        lines.append("## Recall/MRR Hesabına Dahil Edilmeyen Sorular (answerable=false / no-answer)")
        lines.append("")
        for qid in metrics["skipped_no_answer_or_no_expected_sources"]:
            lines.append(f"- {qid}")
        lines.append("")
    if metrics["skipped_missing_retrieval"]:
        lines.append("## UYARI: Retrieval Sonucu Eksik Sorular")
        lines.append("")
        for qid in metrics["skipped_missing_retrieval"]:
            lines.append(f"- {qid}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_golden = Path(__file__).resolve().parents[1] / "golden" / "questions.jsonl"
    default_retrieval = Path(__file__).resolve().parent / "retrieval-top10.jsonl"
    default_report = Path(__file__).resolve().parent / "metrics-report.md"
    parser.add_argument("--golden", type=Path, default=default_golden)
    parser.add_argument("--retrieval", type=Path, default=default_retrieval)
    parser.add_argument("--report", type=Path, default=default_report)
    args = parser.parse_args()

    questions = load_jsonl(args.golden)
    retrievals = load_jsonl(args.retrieval)
    metrics = compute_metrics(questions, retrievals)

    report_md = render_markdown_report(metrics, args.golden, args.retrieval)
    args.report.write_text(report_md, encoding="utf-8")

    agg = metrics["aggregate"]
    print(f"n_evaluable={agg['n_evaluable']}/{agg['n_total_questions']}")
    print(f"Recall@1={agg['recall@1']}")
    print(f"Recall@3={agg['recall@3']}")
    print(f"Recall@5={agg['recall@5']}")
    print(f"MRR@10={agg['mrr@10']}")
    print(f"Rapor yazildi: {args.report}")


if __name__ == "__main__":
    main()
