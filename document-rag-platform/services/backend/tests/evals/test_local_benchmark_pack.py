"""Fail-closed tests for the private local benchmark pack boundary."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[5]
SCRIPT = REPO / "scripts/local_benchmark_pack.py"


def _module():
    spec = importlib.util.spec_from_file_location("cv_local_benchmark_pack", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _execution(record_id: str = "case-001") -> dict:
    return {
        "id": record_id,
        "query": "What is the approved retention period?",
        "intent": "lookup",
        "workspace_fixture": "private-a9",
        "project_fixture": "policy-fixture",
        "scope": "documents",
        "permission_persona": "project-reader",
        "query_type": "document-fact",
        "language": "en",
        "documents": [
            {
                "document_id": "policy-current",
                "filename": "policy-current.txt",
                "mime_type": "text/plain",
                "source_type": "document",
                "content_base64": base64.b64encode(
                    b"The approved retention period is 30 days."
                ).decode("ascii"),
                "classification": "restricted",
            },
            {
                "document_id": "policy-obsolete",
                "filename": "policy-obsolete.txt",
                "mime_type": "text/plain",
                "source_type": "document",
                "content_base64": base64.b64encode(
                    b"The obsolete retention period was 90 days."
                ).decode("ascii"),
                "classification": "restricted",
            },
        ],
    }


def _label(record_id: str = "case-001") -> dict:
    return {
        "id": record_id,
        "answerable": True,
        "expected_facts": ["The approved period is 30 days."],
        "expected_source_constraints": [
            {
                "document_id": "policy-current",
                "active_version": True,
                "locator": "line:1",
                "must_contain": ["30 days"],
            }
        ],
        "forbidden_sources": ["policy-obsolete"],
        "adversarial_tags": [],
        "notes": "Owner-reviewed retention case.",
        "reviewer": "owner-review-2026-09",
        "dataset_version": "2.0.0",
        "split": "holdout",
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _pack(tmp_path: Path, *, executions=None, labels=None) -> Path:
    root = tmp_path / "private-pack"
    _write_jsonl(root / "execution/cases.jsonl", executions or [_execution()])
    _write_jsonl(root / "labels/golden.jsonl", labels or [_label()])
    return root


def _open(module, root: Path):
    bundle = module.bundle_descriptor(root)
    return module.LocalBenchmarkPack.open(root, expected_bundle_sha256=bundle["sha256"])


def _ledger(module, pack) -> list[dict[str, str]]:
    return [
        {"id": case["id"], "request_sha256": digest}
        for case, digest in zip(
            pack.execution_projection(),
            [module.aggregate_request_hashes([item]) for item in pack.request_hashes()],
            strict=True,
        )
    ]


def test_bundle_hash_is_deterministic_domain_separated_and_byte_sensitive(tmp_path):
    module = _module()
    root = _pack(tmp_path)
    first = module.bundle_descriptor(root)
    assert first == module.bundle_descriptor(root)
    assert first["files"] == 2
    assert first["bytes"] > 0
    assert len(first["sha256"]) == 64
    assert set(first["file_sha256"]) == {
        "execution/cases.jsonl",
        "labels/golden.jsonl",
    }
    assert (
        first["file_sha256"]["execution/cases.jsonl"]
        == hashlib.sha256((root / "execution/cases.jsonl").read_bytes()).hexdigest()
    )

    labels = root / "labels/golden.jsonl"
    labels.write_bytes(labels.read_bytes() + b"\n")
    assert module.bundle_descriptor(root)["sha256"] != first["sha256"]


def test_open_rejects_hash_mismatch_before_parsing_labels(tmp_path):
    module = _module()
    root = _pack(tmp_path)
    (root / "labels/golden.jsonl").write_bytes(b"not-json\n")
    with pytest.raises(module.LocalBenchmarkPackError, match="bundle hash mismatch"):
        module.LocalBenchmarkPack.open(root, expected_bundle_sha256="0" * 64)


def test_bundle_admission_and_execution_do_not_parse_golden_labels(tmp_path):
    module = _module()
    root = _pack(tmp_path)
    (root / "labels/golden.jsonl").write_bytes(b"not-json\n")
    bundle = module.bundle_descriptor(root)
    pack = module.LocalBenchmarkPack.open(root, expected_bundle_sha256=bundle["sha256"])
    assert pack.execution_projection()[0]["id"] == "case-001"
    pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(module.LocalBenchmarkPackError, match="invalid JSON"):
        pack.load_labels()


def test_pack_root_must_be_outside_repository(tmp_path):
    module = _module()
    root = REPO / ".local-pack-must-not-exist"
    with pytest.raises(module.LocalBenchmarkPackError, match="outside repository"):
        module.bundle_descriptor(root)


def test_exact_layout_rejects_extra_file_and_missing_file(tmp_path):
    module = _module()
    root = _pack(tmp_path)
    (root / "extra.txt").write_text("extra")
    with pytest.raises(module.LocalBenchmarkPackError, match="exact two-file layout"):
        module.bundle_descriptor(root)
    (root / "extra.txt").unlink()
    (root / "labels/golden.jsonl").unlink()
    with pytest.raises(module.LocalBenchmarkPackError, match="exact two-file layout"):
        module.bundle_descriptor(root)


def test_layout_rejects_any_symlink(tmp_path):
    module = _module()
    root = _pack(tmp_path)
    target = root / "labels/real.jsonl"
    target.write_bytes((root / "labels/golden.jsonl").read_bytes())
    (root / "labels/golden.jsonl").unlink()
    (root / "labels/golden.jsonl").symlink_to(target)
    with pytest.raises(module.LocalBenchmarkPackError, match="symlink"):
        module.bundle_descriptor(root)


@pytest.mark.parametrize("side", ["execution", "labels"])
def test_jsonl_shapes_reject_missing_and_extra_fields(tmp_path, side):
    module = _module()
    root = _pack(tmp_path)
    record = _execution() if side == "execution" else _label()
    record.pop(next(iter(record)))
    record["unexpected"] = "not admitted"
    path = (
        root / "execution/cases.jsonl"
        if side == "execution"
        else root / "labels/golden.jsonl"
    )
    _write_jsonl(path, [record])
    pack = _open(module, root)
    action = pack.execution_projection if side == "execution" else pack.load_labels
    if side == "labels":
        pack.execution_projection()
        pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(module.LocalBenchmarkPackError, match="fields"):
        action()


def test_execution_shape_rejects_golden_fields(tmp_path):
    module = _module()
    execution = _execution()
    execution["answerable"] = True
    pack = _open(module, _pack(tmp_path, executions=[execution]))
    with pytest.raises(module.LocalBenchmarkPackError, match="fields"):
        pack.execution_projection()


def test_duplicate_and_mismatched_ids_are_rejected(tmp_path):
    module = _module()
    duplicate = _pack(
        tmp_path / "duplicate",
        executions=[_execution(), _execution()],
        labels=[_label(), _label()],
    )
    with pytest.raises(module.LocalBenchmarkPackError, match="duplicate id"):
        _open(module, duplicate).execution_projection()

    mismatch = _pack(tmp_path / "mismatch", labels=[_label("case-999")])
    pack = _open(module, mismatch)
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(module.LocalBenchmarkPackError, match="record IDs"):
        pack.load_labels()

    duplicate_labels = _pack(
        tmp_path / "duplicate-labels",
        executions=[_execution("one"), _execution("two")],
        labels=[_label("one"), _label("one")],
    )
    pack = _open(module, duplicate_labels)
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(module.LocalBenchmarkPackError, match="duplicate id"):
        pack.load_labels()


def test_labels_cannot_be_loaded_before_request_ledger_freeze(tmp_path):
    module = _module()
    pack = _open(module, _pack(tmp_path))
    with pytest.raises(module.LocalBenchmarkPackError, match="ledger.*frozen"):
        pack.load_labels()


def test_request_ledger_rejects_raw_prompt_and_requires_exact_ids(tmp_path):
    module = _module()
    pack = _open(module, _pack(tmp_path))
    pack.execution_projection()
    ledger = _ledger(module, pack)
    ledger[0]["raw_prompt"] = "PRIVATE QUERY AND CONTEXT"
    with pytest.raises(module.LocalBenchmarkPackError, match="fields"):
        pack.freeze_request_ledger(ledger)

    with pytest.raises(module.LocalBenchmarkPackError, match="record IDs"):
        pack.freeze_request_ledger([{"id": "case-999", "request_sha256": "a" * 64}])


def test_request_ledger_is_one_way_and_labels_follow_it(tmp_path):
    module = _module()
    pack = _open(module, _pack(tmp_path))
    pack.execution_projection()
    digest = pack.freeze_request_ledger(_ledger(module, pack))
    assert len(digest) == 64
    assert pack.load_labels() == [_label()]
    with pytest.raises(module.LocalBenchmarkPackError, match="already frozen"):
        pack.freeze_request_ledger(_ledger(module, pack))


def test_multiple_observed_request_events_have_ordered_bounded_aggregate():
    module = _module()
    first = "a" * 64
    second = "b" * 64
    aggregate = module.aggregate_request_hashes([first, second])
    assert len(aggregate) == 64
    assert aggregate != module.aggregate_request_hashes([second, first])
    assert aggregate not in {first, second}
    with pytest.raises(module.LocalBenchmarkPackError, match="nonempty"):
        module.aggregate_request_hashes([])


def test_label_only_change_does_not_change_execution_projection_or_request_hashes(
    tmp_path,
):
    module = _module()
    first_root = _pack(tmp_path / "first")
    changed = copy.deepcopy(_label())
    changed["expected_facts"] = ["A newly reviewed expected fact."]
    second_root = _pack(tmp_path / "second", labels=[changed])

    first = _open(module, first_root)
    second = _open(module, second_root)
    assert first.bundle_sha256 != second.bundle_sha256
    assert first.execution_projection() == second.execution_projection()
    assert first.execution_sha256 == second.execution_sha256
    assert first.request_hashes() == second.request_hashes()


def test_documents_reject_external_refs_and_bind_inline_content_bytes(tmp_path):
    module = _module()
    external = _execution()
    external["documents"][0]["path"] = "/private/source.txt"
    pack = _open(module, _pack(tmp_path / "external", executions=[external]))
    with pytest.raises(module.LocalBenchmarkPackError, match="document fields"):
        pack.execution_projection()

    changed = _execution()
    changed["documents"][0]["content_base64"] = base64.b64encode(
        b"The approved retention period is 31 days."
    ).decode("ascii")
    first = _open(module, _pack(tmp_path / "first"))
    second = _open(module, _pack(tmp_path / "second", executions=[changed]))
    first.execution_projection()
    second.execution_projection()
    assert first.bundle_sha256 != second.bundle_sha256
    assert first.execution_sha256 != second.execution_sha256
    assert first.request_hashes() != second.request_hashes()

    ledger = _ledger(module, first)
    serialized = json.dumps(ledger)
    assert _execution()["documents"][0]["content_base64"] not in serialized
    assert "query" not in serialized and "content" not in serialized


def test_documents_enforce_canonical_base64_uniqueness_and_decoded_limits(
    tmp_path, monkeypatch
):
    module = _module()
    malformed = _execution()
    malformed["documents"][0]["content_base64"] = "YQ"
    pack = _open(module, _pack(tmp_path / "base64", executions=[malformed]))
    with pytest.raises(module.LocalBenchmarkPackError, match="canonical base64"):
        pack.execution_projection()

    duplicated = _execution()
    duplicated["documents"].append(copy.deepcopy(duplicated["documents"][0]))
    pack = _open(module, _pack(tmp_path / "duplicate", executions=[duplicated]))
    with pytest.raises(module.LocalBenchmarkPackError, match="duplicate document_id"):
        pack.execution_projection()

    oversized = _execution()
    oversized["documents"][0]["content_base64"] = base64.b64encode(b"ab").decode()
    monkeypatch.setattr(module, "MAX_DOCUMENT_BYTES", 1)
    pack = _open(module, _pack(tmp_path / "per-document", executions=[oversized]))
    with pytest.raises(module.LocalBenchmarkPackError, match="document.*byte-size"):
        pack.execution_projection()

    monkeypatch.setattr(module, "MAX_DOCUMENT_BYTES", 8_000_000)
    monkeypatch.setattr(module, "MAX_TOTAL_DOCUMENT_BYTES", 1)
    pack = _open(module, _pack(tmp_path / "total-documents"))
    with pytest.raises(module.LocalBenchmarkPackError, match="total decoded document"):
        pack.execution_projection()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("scope", "outside", "scope"),
        ("permission_persona", "reader with spaces", "permission_persona"),
    ],
)
def test_execution_scope_and_permission_persona_are_bounded_identifiers(
    tmp_path, field, value, message
):
    module = _module()
    execution = _execution()
    execution[field] = value
    pack = _open(module, _pack(tmp_path, executions=[execution]))
    with pytest.raises(module.LocalBenchmarkPackError, match=message):
        pack.execution_projection()


def test_classification_enum_and_expected_source_join_are_exact(tmp_path):
    module = _module()
    execution = _execution()
    execution["documents"][0]["classification"] = "private"
    pack = _open(module, _pack(tmp_path / "classification", executions=[execution]))
    with pytest.raises(module.LocalBenchmarkPackError, match="classification"):
        pack.execution_projection()

    label = _label()
    label["expected_source_constraints"] = [{"document_id": "unknown-document"}]
    pack = _open(module, _pack(tmp_path / "source-join", labels=[label]))
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(
        module.LocalBenchmarkPackError, match="expected_source_constraints"
    ):
        pack.load_labels()


def test_forbidden_sources_join_and_remain_disjoint_from_expected(tmp_path):
    module = _module()
    label = _label()
    label["forbidden_sources"] = ["not-in-the-case"]
    pack = _open(module, _pack(tmp_path / "unknown", labels=[label]))
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(module.LocalBenchmarkPackError, match="forbidden_sources"):
        pack.load_labels()

    label = _label()
    label["forbidden_sources"] = ["policy-current"]
    pack = _open(module, _pack(tmp_path / "overlap", labels=[label]))
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(module.LocalBenchmarkPackError, match="disjoint"):
        pack.load_labels()


@pytest.mark.parametrize("source_type", ["file", "", None])
def test_document_source_type_is_required_and_enumerated(tmp_path, source_type):
    module = _module()
    execution = _execution()
    execution["documents"][0]["source_type"] = source_type
    pack = _open(module, _pack(tmp_path, executions=[execution]))
    with pytest.raises(module.LocalBenchmarkPackError, match="source_type"):
        pack.execution_projection()


def test_source_type_is_bound_into_execution_and_request_hashes(tmp_path):
    module = _module()
    changed = _execution()
    changed["documents"][0]["source_type"] = "archive"
    first = _open(module, _pack(tmp_path / "first"))
    second = _open(module, _pack(tmp_path / "second", executions=[changed]))
    first.execution_projection()
    second.execution_projection()
    assert first.bundle_sha256 != second.bundle_sha256
    assert first.execution_sha256 != second.execution_sha256
    assert first.request_hashes() != second.request_hashes()


def test_file_byte_record_query_type_and_id_limits(tmp_path, monkeypatch):
    module = _module()
    root = _pack(tmp_path / "bytes")
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 1)
    with pytest.raises(module.LocalBenchmarkPackError, match="byte-size limit"):
        module.bundle_descriptor(root)

    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 16_000_000)
    monkeypatch.setattr(module, "MAX_RECORDS", 1)
    root = _pack(
        tmp_path / "records",
        executions=[_execution("one"), _execution("two")],
        labels=[_label("one"), _label("two")],
    )
    with pytest.raises(module.LocalBenchmarkPackError, match="record-count limit"):
        _open(module, root).execution_projection()

    monkeypatch.setattr(module, "MAX_RECORDS", 10_000)
    monkeypatch.setattr(module, "MAX_QUERY_TYPES", 1)
    second = _execution("two")
    second["query_type"] = "another-type"
    root = _pack(
        tmp_path / "types",
        executions=[_execution("one"), second],
        labels=[_label("one"), _label("two")],
    )
    with pytest.raises(module.LocalBenchmarkPackError, match="query-type limit"):
        _open(module, root).execution_projection()

    monkeypatch.setattr(module, "MAX_QUERY_TYPES", 64)
    monkeypatch.setattr(module, "MAX_RECORD_ID_BYTES", 4)
    root = _pack(
        tmp_path / "case-id",
        executions=[_execution("too-long")],
        labels=[_label("too-long")],
    )
    with pytest.raises(module.LocalBenchmarkPackError, match="id"):
        _open(module, root).execution_projection()


def test_source_constraints_are_strict_bounded_and_join_inline_documents(tmp_path):
    module = _module()
    label = _label()
    label["expected_source_constraints"][0]["path"] = "/external/source"
    pack = _open(module, _pack(tmp_path / "extra", labels=[label]))
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(
        module.LocalBenchmarkPackError, match="source constraint fields"
    ):
        pack.load_labels()

    label = _label()
    label["expected_source_constraints"] = [
        {
            "document_id": "policy-current",
            "symbol": "RETENTION_DAYS",
            "must_contain": ["30", "days"],
        }
    ]
    pack = _open(module, _pack(tmp_path / "valid", labels=[label]))
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    assert pack.load_labels() == [label]

    label = _label()
    label["expected_source_constraints"][0]["active_version"] = "true"
    pack = _open(module, _pack(tmp_path / "active-version", labels=[label]))
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(module.LocalBenchmarkPackError, match="active_version"):
        pack.load_labels()


def test_answerability_and_split_semantics_are_enforced(tmp_path):
    module = _module()
    for field in ("expected_facts", "expected_source_constraints"):
        label = _label()
        label[field] = []
        pack = _open(module, _pack(tmp_path / field, labels=[label]))
        pack.execution_projection()
        pack.freeze_request_ledger(_ledger(module, pack))
        with pytest.raises(module.LocalBenchmarkPackError, match="answerable"):
            pack.load_labels()

    no_answer = _label()
    no_answer.update(
        answerable=False,
        expected_facts=[],
        expected_source_constraints=[],
        split="train",
    )
    pack = _open(module, _pack(tmp_path / "no-answer", labels=[no_answer]))
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    assert pack.load_labels() == [no_answer]

    mislabeled_no_answer = _label()
    mislabeled_no_answer["answerable"] = False
    pack = _open(
        module, _pack(tmp_path / "no-answer-with-gold", labels=[mislabeled_no_answer])
    )
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(module.LocalBenchmarkPackError, match="must not declare"):
        pack.load_labels()

    invalid_split = _label()
    invalid_split["split"] = "validation"
    pack = _open(module, _pack(tmp_path / "split", labels=[invalid_split]))
    pack.execution_projection()
    pack.freeze_request_ledger(_ledger(module, pack))
    with pytest.raises(module.LocalBenchmarkPackError, match="split"):
        pack.load_labels()


def test_execution_projection_must_match_manifest_record_and_query_type_claims(
    tmp_path,
):
    module = _module()
    root = _pack(tmp_path)
    bundle = module.bundle_descriptor(root)
    pack = module.LocalBenchmarkPack.open(
        root,
        expected_bundle_sha256=bundle["sha256"],
        expected_records=1,
        expected_query_types=["document-fact"],
    )
    assert pack.validate_execution_manifest(
        records=1, query_types=["document-fact"]
    ) == {
        "records": 1,
        "query_types": ["document-fact"],
        "execution_sha256": pack.execution_sha256,
    }

    mismatch = module.LocalBenchmarkPack.open(
        root,
        expected_bundle_sha256=bundle["sha256"],
        expected_records=2,
        expected_query_types=["document-fact"],
    )
    with pytest.raises(module.LocalBenchmarkPackError, match="record count"):
        mismatch.execution_projection()

    mismatch = _open(module, root)
    with pytest.raises(module.LocalBenchmarkPackError, match="query types"):
        mismatch.validate_execution_manifest(records=1, query_types=["identifier"])
