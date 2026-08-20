"""Aşama 9 — golden dataset loader and validator (tests/evals).

Provides loading and structural validation for the golden evaluation dataset
(``datasets/golden.jsonl``). See ``AKTIF_GOREV.md`` section 9.1 for the
required JSONL schema and the list of categories that a >= 50-item dataset
must cover:

    DOCX heading, DOCX table, PDF page, scanned-PDF OCR, PNG OCR, exact
    identifier, paraphrase, multi-doc synthesis, code file/symbol, PL/SQL
    package, no-answer, smalltalk, prompt injection, contradictory version.

This module is pure tooling: it never calls a network or touches a database,
so it is fully testable in CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

#: Canonical dataset path: sibling ``datasets/golden.jsonl`` of this module.
DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "golden.jsonl"

#: Every record must carry at least these keys (see AKTIF_GOREV.md 9.1).
REQUIRED_FIELDS = ("id", "query", "scope", "answerable", "expected_sources", "tags")

#: Category tags that the dataset must cover (one representative item each).
REQUIRED_CATEGORIES = (
    "docx-heading",
    "docx-table",
    "pdf-page",
    "ocr",
    "identifier",
    "paraphrase",
    "multi-doc",
    "code",
    "plsql",
    "no-answer",
    "smalltalk",
    "prompt-injection",
    "contradictory-version",
)

#: Use these canonical tags when validating category coverage. They are a
#: superset of the granular tags used inside the dataset so a record is
#: counted against every category it is tagged with.
_CANONICAL_TAGS = REQUIRED_CATEGORIES + ("sql", "symbol", "file", "synthesis", "ocr")


def load_golden(path: Path | str = DEFAULT_DATASET_PATH) -> List[Dict[str, Any]]:
    """Load a golden dataset from a JSONL file.

    Blank lines are skipped; malformed JSON raises ``ValueError`` with the
    offending line number.
    """
    path = Path(path)
    records: List[Dict[str, Any]] = []
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


def validate_dataset(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate a loaded golden dataset.

    Returns a dict with ``ok`` (bool), ``errors`` (list of human-readable
    problems) and ``categories`` (dict of tag -> count). Structural checks:

    - every record has all ``REQUIRED_FIELDS``;
    - ``answerable`` is a bool;
    - non-answerable records must have empty ``expected_sources``;
    - answerable records must have a non-empty ``expected_sources`` list
      (so they are actually evaluable for retrieval metrics);
    - ``expected_sources`` entries are dicts with a ``document`` key;
    - every id is unique and non-empty.
    """
    errors: List[str] = []
    seen_ids: Dict[str, int] = {}
    category_counts: Dict[str, int] = {tag: 0 for tag in REQUIRED_CATEGORIES}

    for idx, rec in enumerate(records, start=1):
        label = f"kayit#{idx}"
        missing = [k for k in REQUIRED_FIELDS if k not in rec]
        if missing:
            errors.append(f"{label}: eksik alan(lar): {', '.join(missing)}")
            continue
        qid = rec["id"]
        if not isinstance(qid, str) or not qid.strip():
            errors.append(f"{label}: 'id' bos veya string degil")
        else:
            seen_ids[qid] = seen_ids.get(qid, 0) + 1
        if not isinstance(rec["answerable"], bool):
            errors.append(f"{label}: 'answerable' bool degil")
        answerable = rec["answerable"]
        sources = rec["expected_sources"]
        if not isinstance(sources, list):
            errors.append(f"{label}: 'expected_sources' liste degil")
            sources = []
        if not answerable and sources:
            errors.append(f"{label}: answerable=false ama expected_sources dolu")
        if answerable and not sources:
            errors.append(f"{label}: answerable=true ama expected_sources bos")
        for s in sources:
            if not isinstance(s, dict) or "document" not in s:
                errors.append(f"{label}: expected_source 'document' iceriyor")
        tags = rec.get("tags", [])
        for tag in tags:
            if tag in category_counts:
                category_counts[tag] += 1

    for qid, count in seen_ids.items():
        if count > 1:
            errors.append(f"tekrarlanan id: {qid} ({count}x)")

    return {
        "ok": not errors,
        "errors": errors,
        "categories": category_counts,
        "n_records": len(records),
    }


def category_coverage(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """Return {canonical_category_tag: count} across the dataset."""
    counts: Dict[str, int] = {tag: 0 for tag in REQUIRED_CATEGORIES}
    for rec in records:
        for tag in rec.get("tags", []):
            if tag in counts:
                counts[tag] += 1
    return counts


def missing_categories(records: List[Dict[str, Any]]) -> List[str]:
    """Categories from ``REQUIRED_CATEGORIES`` with zero items in the data."""
    return [tag for tag, count in category_coverage(records).items() if count == 0]
