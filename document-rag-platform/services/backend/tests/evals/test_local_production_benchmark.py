from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[5]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load(
    "local_production_benchmark", REPO / "scripts/run_local_production_benchmark.py"
)
run_eval = _load("run_eval_for_local_benchmark", REPO / "scripts/run_eval.py")
pack_module = _load(
    "local_pack_for_production_benchmark", REPO / "scripts/local_benchmark_pack.py"
)


class FakeEmbedding:
    def __init__(self):
        self.drift = False

    def report(self):
        return {
            "is_remote": False,
            "model": runner.BGE_IDENTITY,
            "model_revision": runner.BGE_REVISION,
            "model_bundle_sha256": "0" * 64 if self.drift else runner.BGE_BUNDLE_SHA256,
            "embedding_dimension": 1024,
            "counters": {
                "batch_calls": 1,
                "batch_tokens": 20,
                "query_calls": 0,
                "query_tokens": 10,
            },
        }


class FakeGeneration:
    def __init__(self):
        self.drift = False

    def report(self):
        return {
            "is_remote": False,
            "model": runner.QWEN_IDENTITY,
            "model_revision": runner.QWEN_REVISION,
            "model_bundle_sha256": "0" * 64
            if self.drift
            else runner.QWEN_BUNDLE_SHA256,
            "deterministic_generation": True,
            "counters": {
                "generation_calls": 1,
                "repair_calls": 0,
                "prompt_tokens": 30,
                "generated_tokens": 8,
            },
        }


class FakeExecutor:
    def __init__(self):
        self.calls = []
        self.attestation_drift = False

    def provenance(self):
        return {
            "pipeline": "context-vault-production-core",
            "ingestion": "IngestionOrchestrator.accept_source/process_job",
            "retrieval": "RetrievalService.retrieve",
            "answer": "application.answer_service.generate_answer",
            "celery_delivery_exercised": False,
            "redis_role": "health-only",
        }

    def attestation(self):
        return {
            "embedding_profile_hash": "a" * 64,
            "prompt_hash": "b" * 64,
            "pipeline_config_hash": ("0" * 64 if self.attestation_drift else "c" * 64),
        }

    def __call__(self, case, *, embedding_provider, generation_client, observe_request):
        case_id = case["id"]
        self.calls.append(case_id)
        for ordinal, kind in enumerate(("embedding", "generation"), start=1):
            if kind == "embedding":
                event = {
                    "ordinal": ordinal,
                    "kind": kind,
                    "model": runner.BGE_IDENTITY,
                    "item_count": 1,
                    "byte_count": 8,
                    "token_count": 2,
                    "payload_sha256": str(ordinal) * 64,
                }
            else:
                event = {
                    "ordinal": ordinal,
                    "kind": kind,
                    "model": runner.QWEN_IDENTITY,
                    "field_names": ["input"],
                    "byte_counts": {"input": 8},
                    "token_counts": {"prompt": 2, "maximum_output": 4},
                    "payload_sha256": str(ordinal) * 64,
                }
            observe_request(event)
        return {
            "id": case_id,
            "predicted_answerable": True,
            "retrieved_source_ids": ["doc-1"],
            "cited_source_ids": ["doc-1"],
            "answer": "The approved fact is 42.",
            "claim_count": 1,
            "supported_claim_count": 1,
            "permission_version_leakage": 0,
            "active_version_leakage": 0,
            "profile_leakage": 0,
            "cross_project_leakage": 0,
            "cross_workspace_leakage": 0,
            "critical_high_security_findings": 0,
            "retry_count": 0,
            "duplicate_count": 0,
            "orphan_count": 0,
            "ingestion_ms": 2.0,
            "retrieval_ms": 3.0,
            "end_to_end_ms": 7.0,
            "queue_wait_ms": 0.0,
            "stage_duration_ms": {"parse": 1.0, "retrieval": 3.0},
            "error_code": None,
        }


def _pack(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "private-pack"
    (root / "execution").mkdir(parents=True)
    (root / "labels").mkdir()
    document = {
        "document_id": "doc-1",
        "filename": "fact.txt",
        "mime_type": "text/plain",
        "source_type": "document",
        "content_base64": base64.b64encode(b"The approved fact is 42.").decode(),
        "classification": "internal",
        "revises_document_id": None,
    }
    case = {
        "id": "case-1",
        "intent": "fact_lookup",
        "workspace_fixture": "workspace_one",
        "project_fixture": "project_one",
        "query_type": "prose",
        "query": "What is the approved fact?",
        "scope": "documents",
        "document_scope": "case",
        "permission_persona": "member",
        "language": "en",
        "documents": [document],
    }
    label = {
        "id": "case-1",
        "answerable": True,
        "expected_facts": ["42"],
        "expected_source_constraints": [{"document_id": "doc-1"}],
        "forbidden_sources": [],
        "adversarial_tags": [],
        "notes": "private notes",
        "reviewer": "private-reviewer",
        "dataset_version": "1.0.0",
        "split": "holdout",
    }
    (root / "execution/cases.jsonl").write_text(json.dumps(case) + "\n")
    (root / "labels/golden.jsonl").write_text(json.dumps(label) + "\n")
    return root, pack_module.bundle_descriptor(root)["sha256"]


def _pack_two(tmp_path: Path) -> tuple[Path, str]:
    root, _sha = _pack(tmp_path)
    execution_path = root / "execution/cases.jsonl"
    label_path = root / "labels/golden.jsonl"
    first_case = json.loads(execution_path.read_text())
    second_case = json.loads(execution_path.read_text())
    second_case["id"] = "case-2"
    second_case["documents"][0]["document_id"] = "doc-2"
    second_case["documents"][0]["filename"] = "fact-two.txt"
    first_label = json.loads(label_path.read_text())
    second_label = json.loads(label_path.read_text())
    second_label["id"] = "case-2"
    second_label["expected_source_constraints"][0]["document_id"] = "doc-2"
    execution_path.write_text(
        json.dumps(first_case) + "\n" + json.dumps(second_case) + "\n"
    )
    label_path.write_text(
        json.dumps(first_label) + "\n" + json.dumps(second_label) + "\n"
    )
    return root, pack_module.bundle_descriptor(root)["sha256"]


def _env(tmp_path: Path, pack_root: Path, pack_sha: str) -> dict[str, str]:
    output = tmp_path / "report.json"
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
        "CV_EVAL_OUTPUT": str(output),
        "CV_EVAL_APPROVAL_ID": "approved-local-run",
        "CV_EVAL_MAX_PROVIDER_CALLS": "10",
        "CV_EVAL_MAX_INPUT_TOKENS": "1000",
        "CV_EVAL_MAX_OUTPUT_TOKENS": "1000",
        "CV_EVAL_MAX_COST_USD": "0",
        "CV_EVAL_MAX_DURATION_SECONDS": "60",
        "CV_EVAL_EMBEDDING_PROVIDER": runner.EMBEDDING_PROVIDER,
        "CV_EVAL_EMBEDDING_MODEL": runner.BGE_IDENTITY,
        "CV_EVAL_GENERATION_PROVIDER": runner.GENERATION_PROVIDER,
        "CV_EVAL_GENERATION_MODEL": runner.QWEN_IDENTITY,
        "CV_EVAL_ENVIRONMENT_HASH": "e" * 64,
        "CV_EVAL_CURRENCY": "USD",
        "CV_EVAL_DATASET_SHA256": pack_sha,
        "CV_EVAL_PRIVATE_PACK_MANIFEST_SHA256": "f" * 64,
        "CV_EVAL_PRIVATE_PACK_CLASSIFICATION": "internal",
        "CV_EVAL_PRIVATE_PACK_RECORDS": "1",
        "CV_EVAL_PRIVATE_PACK_QUERY_TYPES": '["prose"]',
        "CV_EVAL_RUNNER_BUNDLE_SHA256": "a" * 64,
        "CV_LOCAL_EVAL_PACK_ROOT": str(pack_root),
        "DATABASE_URL": "postgresql://tester:secret@127.0.0.1:5432/cv3_eval_unit",
        "MINIO_ENDPOINT": "http://127.0.0.1:9000",
        "MINIO_ACCESS_KEY": "test-access",
        "MINIO_SECRET_KEY": "test-secret",
        "MINIO_BUCKET": "cv3-eval-unit",
        "OBJECT_STORAGE_ENCRYPTION_KEY": "test-only-encryption-key",
        "CV_LOCAL_BGE_SNAPSHOT": str(tmp_path / "bge"),
        "CV_LOCAL_GENERATION_SNAPSHOT": str(tmp_path / "qwen"),
        "REDIS_URL": "redis://:test-only@127.0.0.1:6379/0",
    }


def _providers(_config, _observer):
    return FakeEmbedding(), FakeGeneration()


def test_default_provider_factory_observes_both_exact_local_adapters(
    monkeypatch, tmp_path
):
    captured = {}

    def embedding(**kwargs):
        captured["embedding"] = kwargs
        return object()

    def generation(**kwargs):
        captured["generation"] = kwargs
        return object()

    def observer(_event):
        return None

    monkeypatch.setattr(runner, "LocalBgeProvider", embedding)
    monkeypatch.setattr(runner, "DeferredLocalQwenClient", generation)
    runner._provider_factory(
        {
            "env": {
                "CV_LOCAL_BGE_SNAPSHOT": str(tmp_path / "bge"),
                "CV_LOCAL_GENERATION_SNAPSHOT": str(tmp_path / "qwen"),
            }
        },
        observer,
    )
    assert captured["embedding"]["expected_model"] == runner.BGE_MODEL
    assert captured["embedding"]["expected_revision"] == runner.BGE_REVISION
    assert captured["embedding"]["device"] == "mps"
    assert captured["embedding"]["request_observer"] is observer
    assert captured["generation"]["expected_model"] == runner.QWEN_MODEL
    assert captured["generation"]["expected_revision"] == runner.QWEN_REVISION
    assert captured["generation"]["device"] == "mps"
    assert (
        captured["generation"]["max_output_tokens"]
        == runner.LOCAL_GENERATION_MAX_OUTPUT_TOKENS
        == 256
    )
    assert captured["generation"]["request_observer"] is observer


def test_happy_path_writes_only_bounded_v2_report(tmp_path):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    executor = FakeExecutor()

    report = runner.execute(
        env,
        case_executor=executor,
        provider_factory=_providers,
    )

    assert executor.calls == ["case-1"]
    assert report == json.loads(Path(env["CV_EVAL_OUTPUT"]).read_text())
    assert os.stat(env["CV_EVAL_OUTPUT"]).st_mode & 0o777 == 0o600
    assert run_eval._benchmark_report(report) == report
    assert report["golden_results_sent_to_provider"] is False
    assert report["embedding_model"] == runner.BGE_IDENTITY
    assert report["generation_model"] == runner.QWEN_IDENTITY
    assert report["embedding_profile_hash"] == "a" * 64
    assert report["prompt_hash"] == "b" * 64
    serialized = json.dumps(report)
    for secret in ("approved fact", "private-reviewer", "private notes", "test-secret"):
        assert secret not in serialized


def test_observation_accepts_only_bounded_answer_validation_codes():
    value = FakeExecutor()(
        {"id": "case-1"},
        embedding_provider=None,
        generation_client=None,
        observe_request=lambda _event: None,
    )
    code = "answer_validation.used_source_labels_mismatch"
    value["error_code"] = code

    assert runner._observation(value, "case-1")["error_code"] == code

    value["error_code"] = "secret_dynamic_code"
    with pytest.raises(runner.LocalProductionBenchmarkError, match="error code"):
        runner._observation(value, "case-1")


def test_config_hash_binds_phased_generation_token_limit(tmp_path, monkeypatch):
    pack_root, pack_sha = _pack(tmp_path)
    first_env = _env(tmp_path, pack_root, pack_sha)
    first = runner.execute(
        first_env, case_executor=FakeExecutor(), provider_factory=_providers
    )

    second_env = dict(first_env)
    second_env["CV_EVAL_OUTPUT"] = str(tmp_path / "second-report.json")
    monkeypatch.setattr(runner, "LOCAL_GENERATION_MAX_OUTPUT_TOKENS", 127)
    second = runner.execute(
        second_env, case_executor=FakeExecutor(), provider_factory=_providers
    )

    assert runner.TOOL_VERSION == "1.1.0"
    assert first["config_hash"] != second["config_hash"]


def test_phased_runner_completes_all_retrieval_before_release_and_answers(
    tmp_path, monkeypatch
):
    pack_root, pack_sha = _pack_two(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    env["CV_EVAL_PRIVATE_PACK_RECORDS"] = "2"
    env["CV_EVAL_MAX_PROVIDER_CALLS"] = "8"
    events = []
    original_labels = runner.LocalBenchmarkPack.load_labels

    def labels(self):
        events.append("labels")
        return original_labels(self)

    monkeypatch.setattr(runner.LocalBenchmarkPack, "load_labels", labels)

    class Embedding(FakeEmbedding):
        def __init__(self, observer):
            super().__init__()
            self.observer = observer
            self.calls = 0

        def request(self, case_id):
            self.calls += 1
            events.append(("retrieve", case_id))
            self.observer(
                {
                    "ordinal": self.calls,
                    "kind": "query",
                    "model": runner.BGE_IDENTITY,
                    "item_count": 1,
                    "byte_count": 8,
                    "token_count": 2,
                    "payload_sha256": str(self.calls) * 64,
                }
            )

        def close(self):
            events.append("embedding_close")

        def report(self):
            value = super().report()
            value["counters"].update(
                batch_calls=0,
                batch_tokens=0,
                query_calls=self.calls,
                query_tokens=self.calls * 2,
            )
            return value

    class Generation(FakeGeneration):
        def __init__(self, observer):
            super().__init__()
            self.observer = observer
            self.calls = 0
            self.loaded = False

        def request(self, case_id):
            if not self.loaded:
                self.loaded = True
                events.append("generation_load")
            self.calls += 1
            events.append(("answer", case_id))
            self.observer(
                {
                    "ordinal": self.calls,
                    "kind": "generation",
                    "model": runner.QWEN_IDENTITY,
                    "field_names": ["input"],
                    "byte_counts": {"input": 8},
                    "token_counts": {"prompt": 2, "maximum_output": 256},
                    "payload_sha256": str(self.calls + 2) * 64,
                }
            )

        def report(self):
            value = super().report()
            value["counters"].update(
                generation_calls=self.calls,
                prompt_tokens=self.calls * 2,
                generated_tokens=self.calls,
            )
            return value

        def close(self):
            events.append("generation_close")

    class Executor(FakeExecutor):
        def provenance(self):
            return {
                **super().provenance(),
                "model_residency_strategy": "phased",
            }

        def phase_one(self, case, *, embedding_provider, observe_request):
            del observe_request
            embedding_provider.request(case["id"])
            return {"id": case["id"]}

        def phase_two(self, case, *, handle, generation_client, observe_request):
            del observe_request
            assert handle["id"] == case["id"]
            generation_client.request(case["id"])
            value = super().__call__(
                case,
                embedding_provider=None,
                generation_client=None,
                observe_request=lambda _event: None,
            )
            value["retrieved_source_ids"] = [case["documents"][0]["document_id"]]
            value["cited_source_ids"] = list(value["retrieved_source_ids"])
            return value

        def finalize(self):
            events.append("finalize")

    def providers(_config, observer):
        return Embedding(observer), Generation(observer)

    monkeypatch.setattr(
        runner, "_cleanup_local_model_memory", lambda: events.append("memory_cleanup")
    )
    runner.execute(env, case_executor=Executor(), provider_factory=providers)
    assert events == [
        ("retrieve", "case-1"),
        ("retrieve", "case-2"),
        "embedding_close",
        "memory_cleanup",
        "generation_load",
        ("answer", "case-1"),
        ("answer", "case-2"),
        "finalize",
        "generation_close",
        "labels",
    ]


def test_repeated_ledger_selection_preserves_both_phase_hashes():
    ledger = runner._ObservedLedger({"max_provider_calls": 4})
    ledger.select("case-1")
    ledger.observe(
        {
            "ordinal": 1,
            "kind": "query",
            "model": runner.BGE_IDENTITY,
            "item_count": 1,
            "byte_count": 8,
            "token_count": 2,
            "payload_sha256": "1" * 64,
        }
    )
    first = list(ledger.events["case-1"])
    ledger.select("case-1")
    ledger.observe(
        {
            "ordinal": 1,
            "kind": "generation",
            "model": runner.QWEN_IDENTITY,
            "field_names": ["input"],
            "byte_counts": {"input": 8},
            "token_counts": {"prompt": 2, "maximum_output": 256},
            "payload_sha256": "2" * 64,
        }
    )
    assert ledger.events["case-1"][:1] == first
    assert len(ledger.events["case-1"]) == 2
    assert len(ledger.records(["case-1"])[0]["request_sha256"]) == 64


def test_duration_is_checked_after_each_case(tmp_path):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    env["CV_EVAL_MAX_DURATION_SECONDS"] = "60"
    ticks = iter((0.0, 1.0, 61.0))
    with pytest.raises(runner.LocalProductionBenchmarkError, match="duration"):
        runner.execute(
            env,
            case_executor=FakeExecutor(),
            provider_factory=_providers,
            clock=lambda: next(ticks),
        )
    assert not Path(env["CV_EVAL_OUTPUT"]).exists()


@pytest.mark.parametrize(
    ("failure", "message", "expected_events"),
    [
        (
            "phase_one",
            "retrieval phase",
            ["phase_one", "finalize", "generation_close", "embedding_close"],
        ),
        (
            "phase_two",
            "answer phase",
            [
                "phase_one",
                "embedding_close",
                "memory_cleanup",
                "phase_two",
                "finalize",
                "generation_close",
            ],
        ),
        (
            "cleanup",
            "cleanup failure",
            [
                "phase_one",
                "embedding_close",
                "memory_cleanup",
                "finalize",
                "generation_close",
            ],
        ),
    ],
)
def test_phased_failures_finalize_close_and_never_open_labels(
    tmp_path, monkeypatch, failure, message, expected_events
):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    events = []
    labels_called = False

    def labels(_self):
        nonlocal labels_called
        labels_called = True
        raise AssertionError("golden labels must remain closed")

    monkeypatch.setattr(runner.LocalBenchmarkPack, "load_labels", labels)

    class Embedding(FakeEmbedding):
        def __init__(self, observer):
            super().__init__()
            self.observer = observer

        def request(self):
            self.observer(
                {
                    "ordinal": 1,
                    "kind": "query",
                    "model": runner.BGE_IDENTITY,
                    "item_count": 1,
                    "byte_count": 8,
                    "token_count": 2,
                    "payload_sha256": "1" * 64,
                }
            )

        def close(self):
            events.append("embedding_close")

    class Generation(FakeGeneration):
        def __init__(self, observer):
            super().__init__()
            self.observer = observer

        def request(self):
            self.observer(
                {
                    "ordinal": 1,
                    "kind": "generation",
                    "model": runner.QWEN_IDENTITY,
                    "field_names": ["input"],
                    "byte_counts": {"input": 8},
                    "token_counts": {"prompt": 2, "maximum_output": 256},
                    "payload_sha256": "2" * 64,
                }
            )

        def close(self):
            events.append("generation_close")

    class Executor(FakeExecutor):
        def provenance(self):
            return {
                **super().provenance(),
                "model_residency_strategy": "phased",
            }

        def phase_one(self, case, *, embedding_provider, observe_request):
            del observe_request
            events.append("phase_one")
            embedding_provider.request()
            if failure == "phase_one":
                raise RuntimeError("injected phase-one failure")
            return {"id": case["id"]}

        def phase_two(self, case, *, handle, generation_client, observe_request):
            del observe_request
            assert handle["id"] == case["id"]
            events.append("phase_two")
            generation_client.request()
            raise RuntimeError("injected phase-two failure")

        def finalize(self):
            events.append("finalize")

    def providers(_config, observer):
        return Embedding(observer), Generation(observer)

    def cleanup():
        events.append("memory_cleanup")
        if failure == "cleanup":
            raise runner.LocalProductionBenchmarkError("injected cleanup failure")

    monkeypatch.setattr(runner, "_cleanup_local_model_memory", cleanup)
    with pytest.raises(runner.LocalProductionBenchmarkError, match=message):
        runner.execute(
            env,
            case_executor=Executor(),
            provider_factory=providers,
        )

    assert events == expected_events
    assert labels_called is False
    assert not Path(env["CV_EVAL_OUTPUT"]).exists()


def test_phased_duration_is_checked_after_answer_phase(tmp_path, monkeypatch):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    labels_called = False

    def labels(_self):
        nonlocal labels_called
        labels_called = True
        raise AssertionError("golden labels must remain closed")

    monkeypatch.setattr(runner.LocalBenchmarkPack, "load_labels", labels)
    monkeypatch.setattr(runner, "_cleanup_local_model_memory", lambda: None)

    class Embedding(FakeEmbedding):
        def close(self):
            return None

    class Executor(FakeExecutor):
        def provenance(self):
            return {
                **super().provenance(),
                "model_residency_strategy": "phased",
            }

        def phase_one(self, case, *, embedding_provider, observe_request):
            observe_request(
                {
                    "ordinal": 1,
                    "kind": "query",
                    "model": runner.BGE_IDENTITY,
                    "item_count": 1,
                    "byte_count": 8,
                    "token_count": 2,
                    "payload_sha256": "1" * 64,
                }
            )
            return {"id": case["id"]}

        def phase_two(self, case, *, handle, generation_client, observe_request):
            assert handle["id"] == case["id"]
            observe_request(
                {
                    "ordinal": 1,
                    "kind": "generation",
                    "model": runner.QWEN_IDENTITY,
                    "field_names": ["input"],
                    "byte_counts": {"input": 8},
                    "token_counts": {"prompt": 2, "maximum_output": 256},
                    "payload_sha256": "2" * 64,
                }
            )
            return super().__call__(
                case,
                embedding_provider=None,
                generation_client=generation_client,
                observe_request=lambda _event: None,
            )

    def providers(_config, _observer):
        return Embedding(), FakeGeneration()

    ticks = iter((0.0, 1.0, 2.0, 3.0, 61.0))
    with pytest.raises(runner.LocalProductionBenchmarkError, match="duration"):
        runner.execute(
            env,
            case_executor=Executor(),
            provider_factory=providers,
            clock=lambda: next(ticks),
        )

    assert labels_called is False
    assert not Path(env["CV_EVAL_OUTPUT"]).exists()


def test_label_only_change_cannot_change_execution_identity(tmp_path):
    captured = []
    bundle_hashes = []

    class CapturingExecutor(FakeExecutor):
        def prepare(self, config, _embedding, _generation):
            captured.append(config["execution_sha256"])

    for name, notes in (("one", "first private note"), ("two", "second private note")):
        pack_root, _ = _pack(tmp_path / name)
        label_path = pack_root / "labels/golden.jsonl"
        label = json.loads(label_path.read_text())
        label["notes"] = notes
        label_path.write_text(json.dumps(label) + "\n")
        pack_sha = pack_module.bundle_descriptor(pack_root)["sha256"]
        bundle_hashes.append(pack_sha)
        runner.execute(
            _env(tmp_path / name, pack_root, pack_sha),
            case_executor=CapturingExecutor(),
            provider_factory=_providers,
        )

    assert bundle_hashes[0] != bundle_hashes[1]
    assert len(set(captured)) == 1


def test_adversarial_case_without_forbidden_sources_fails_closed(tmp_path):
    pack_root, _ = _pack(tmp_path)
    label_path = pack_root / "labels/golden.jsonl"
    label = json.loads(label_path.read_text())
    label["adversarial_tags"] = ["prompt-injection"]
    label_path.write_text(json.dumps(label) + "\n")
    pack_sha = pack_module.bundle_descriptor(pack_root)["sha256"]

    report = runner.execute(
        _env(tmp_path, pack_root, pack_sha),
        case_executor=FakeExecutor(),
        provider_factory=_providers,
    )

    assert report["metrics"]["prompt_injection_success_rate"] == 1.0


def test_database_url_query_cannot_override_validated_loopback_host():
    value = (
        "postgresql+psycopg://tester:secret@localhost:5432/cv3_eval_unit"
        "?host=remote.example"
    )

    with pytest.raises(
        runner.LocalProductionBenchmarkError,
        match="database URL",
    ):
        runner._isolated_database(value)


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://tester:secret@localhost:5432/cv3_eval_unit?sslmode=require",
        "postgresql://tester:secret@localhost:5432/cv3_eval_unit?host=%2Ftmp",
        "postgresql://tester:secret@localhost:5432/cv3_eval_unit?",
        "postgresql://tester:secret@localhost:5432/cv3_eval_unit#ignored",
        "postgresql://tester:secret@localhost:5432/cv3_eval_unit#",
        "postgresql://tester:secret@localhost:5432/cv3_eval_unit;mode=unsafe",
        "postgresql://tester:se@cret@localhost:5432/cv3_eval_unit",
        "postgresql://tester:secret@localhost:not-a-port/cv3_eval_unit",
        "postgresql://tester:secret@localhost:5432/cv3_eval_unit\n",
    ],
)
def test_database_url_rejects_ambiguous_or_unbound_components(value):
    with pytest.raises(runner.LocalProductionBenchmarkError, match="database"):
        runner._isolated_database(value)


def test_database_url_allows_percent_encoded_userinfo_without_target_drift():
    value = "postgresql+psycopg://test%40user:se%40cret@localhost:5432/" "cv3_eval_unit"

    assert runner._isolated_database(value) == "cv3_eval_unit"


def test_database_url_rejects_sqlalchemy_connect_target_drift(monkeypatch):
    class DriftingDialect:
        def create_connect_args(self, _url):
            return [], {
                "host": "remote.example",
                "dbname": "cv3_eval_unit",
                "user": "tester",
                "password": "secret",
                "port": 5432,
            }

    class DriftingUrl:
        def get_dialect(self):
            return DriftingDialect

    monkeypatch.setattr(runner, "make_url", lambda _value: DriftingUrl())

    with pytest.raises(
        runner.LocalProductionBenchmarkError,
        match="connection target",
    ):
        runner._isolated_database(
            "postgresql://tester:secret@localhost:5432/cv3_eval_unit"
        )


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("UNAPPROVED", "1", "allowlist"),
        ("CV_EVAL_DATASET_SHA256", "sha256:" + "a" * 64, "dataset"),
        ("CV_EVAL_EMBEDDING_MODEL", "BAAI/bge-m3@wrong", "embedding"),
        ("DATABASE_URL", "postgresql://x:y@localhost/context_vault", "isolated"),
        ("MINIO_BUCKET", "context-vault", "isolated"),
        ("REDIS_URL", "http://127.0.0.1:6379", "Redis"),
        ("REDIS_URL", "redis://remote.example:6379/0", "Redis"),
        (
            "DATABASE_URL",
            "postgresql://x:y@db.example/cv3_eval_unit",
            "local PostgreSQL",
        ),
        ("MINIO_ENDPOINT", "https://objects.example", "local MinIO"),
    ],
)
def test_admission_rejects_unbound_or_unsafe_environment(tmp_path, key, value, match):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    env[key] = value
    with pytest.raises(runner.LocalProductionBenchmarkError, match=match):
        runner.execute(env, case_executor=FakeExecutor(), provider_factory=_providers)
    assert not Path(env["CV_EVAL_OUTPUT"]).exists()


def test_macos_injected_environment_name_is_optional(tmp_path):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    env["__CF_USER_TEXT_ENCODING"] = "0x1F5:0x0:0x0"
    report = runner.execute(
        env,
        case_executor=FakeExecutor(),
        provider_factory=_providers,
    )
    assert report["schema_version"] == "2.0"


def test_unmeasurable_golden_source_constraint_fails_closed(tmp_path):
    pack_root, _pack_sha = _pack(tmp_path)
    label_path = pack_root / "labels/golden.jsonl"
    label = json.loads(label_path.read_text())
    label["expected_source_constraints"][0]["locator"] = "section-2"
    label_path.write_text(json.dumps(label) + "\n")
    pack_sha = pack_module.bundle_descriptor(pack_root)["sha256"]
    env = _env(tmp_path, pack_root, pack_sha)
    with pytest.raises(
        runner.LocalProductionBenchmarkError,
        match="source constraint",
    ):
        runner.execute(
            env,
            case_executor=FakeExecutor(),
            provider_factory=_providers,
        )


def test_existing_or_symlink_output_is_never_overwritten(tmp_path):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    output = Path(env["CV_EVAL_OUTPUT"])
    victim = tmp_path / "victim"
    victim.write_text("keep")
    output.symlink_to(victim)

    with pytest.raises(runner.LocalProductionBenchmarkError, match="output"):
        runner.execute(env, case_executor=FakeExecutor(), provider_factory=_providers)
    assert victim.read_text() == "keep"


def test_default_cli_binds_concrete_production_executor(monkeypatch):
    captured = {}

    def execute(env, *, case_executor):
        captured["env"] = env
        captured["case_executor"] = case_executor

    monkeypatch.setattr(os, "environ", {"BOUND": "1"})
    monkeypatch.setattr(runner, "execute", execute)
    assert runner.main() == 0
    assert captured == {
        "env": {"BOUND": "1"},
        "case_executor": runner.production_case_executor,
    }


def test_lifecycle_finishes_and_closes_before_freeze_and_labels(tmp_path, monkeypatch):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    events = []
    labels_loaded = False
    original_freeze = runner.LocalBenchmarkPack.freeze_request_ledger
    original_labels = runner.LocalBenchmarkPack.load_labels

    def freeze(self, records):
        events.append("freeze")
        assert not labels_loaded
        return original_freeze(self, records)

    def labels(self):
        nonlocal labels_loaded
        events.append("labels")
        labels_loaded = True
        return original_labels(self)

    monkeypatch.setattr(runner.LocalBenchmarkPack, "freeze_request_ledger", freeze)
    monkeypatch.setattr(runner.LocalBenchmarkPack, "load_labels", labels)

    class LifecycleExecutor(FakeExecutor):
        def prepare(self, _config, _embedding, _generation):
            assert not labels_loaded
            events.append("prepare")

        def __call__(self, case, **kwargs):
            assert not labels_loaded
            events.append("case")
            return super().__call__(case, **kwargs)

        def finalize(self):
            assert not labels_loaded
            events.append("finalize")

    class ClosingEmbedding(FakeEmbedding):
        def close(self):
            assert not labels_loaded
            events.append("embedding_close")

    class ClosingGeneration(FakeGeneration):
        def close(self):
            assert not labels_loaded
            events.append("generation_close")

    def providers(_config, _observer):
        return ClosingEmbedding(), ClosingGeneration()

    runner.execute(env, case_executor=LifecycleExecutor(), provider_factory=providers)
    assert events == [
        "prepare",
        "case",
        "finalize",
        "generation_close",
        "embedding_close",
        "freeze",
        "labels",
    ]


@pytest.mark.parametrize("failure", ["finalize", "close"])
def test_lifecycle_cleanup_failure_is_fail_closed(tmp_path, monkeypatch, failure):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    labels_called = False
    original_labels = runner.LocalBenchmarkPack.load_labels

    def labels(self):
        nonlocal labels_called
        labels_called = True
        return original_labels(self)

    monkeypatch.setattr(runner.LocalBenchmarkPack, "load_labels", labels)

    class Executor(FakeExecutor):
        def finalize(self):
            if failure == "finalize":
                raise RuntimeError("private cleanup failure")

    class Generation(FakeGeneration):
        def close(self):
            if failure == "close":
                raise RuntimeError("private close failure")

    def providers(_config, _observer):
        return FakeEmbedding(), Generation()

    with pytest.raises(runner.LocalProductionBenchmarkError, match=failure):
        runner.execute(env, case_executor=Executor(), provider_factory=providers)
    assert labels_called is False
    assert not Path(env["CV_EVAL_OUTPUT"]).exists()


def test_request_ledger_is_frozen_before_labels_are_loaded(tmp_path, monkeypatch):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    original = pack_module.LocalBenchmarkPack.load_labels
    observed = []

    def checked(self):
        observed.append(self.request_ledger_sha256)
        assert self.request_ledger_sha256 is not None
        return original(self)

    monkeypatch.setattr(pack_module.LocalBenchmarkPack, "load_labels", checked)
    monkeypatch.setattr(runner, "LocalBenchmarkPack", pack_module.LocalBenchmarkPack)
    runner.execute(env, case_executor=FakeExecutor(), provider_factory=_providers)
    assert observed and len(observed[0]) == 64


def test_case_without_observed_provider_request_fails_before_labels(tmp_path):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    executor = FakeExecutor()

    def no_request(case, **_kwargs):
        return executor(
            case,
            observe_request=lambda _event: None,
            **{
                key: value for key, value in _kwargs.items() if key != "observe_request"
            },
        )

    no_request.provenance = executor.provenance
    no_request.attestation = executor.attestation
    with pytest.raises(runner.LocalProductionBenchmarkError, match="request"):
        runner.execute(env, case_executor=no_request, provider_factory=_providers)


def test_malformed_observation_fails_closed(tmp_path):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    executor = FakeExecutor()

    def malformed(case, **kwargs):
        value = executor(case, **kwargs)
        value["raw_debug"] = case["query"]
        return value

    malformed.provenance = executor.provenance
    malformed.attestation = executor.attestation
    with pytest.raises(runner.LocalProductionBenchmarkError, match="observation"):
        runner.execute(env, case_executor=malformed, provider_factory=_providers)


def test_usage_budget_and_model_drift_fail_closed(tmp_path):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)
    env["CV_EVAL_MAX_PROVIDER_CALLS"] = "1"
    with pytest.raises(runner.LocalProductionBenchmarkError, match="budget"):
        runner.execute(env, case_executor=FakeExecutor(), provider_factory=_providers)

    env = _env(tmp_path, pack_root, pack_sha)
    output = Path(env["CV_EVAL_OUTPUT"])
    if output.exists():
        output.unlink()
    embedding = FakeEmbedding()
    original_executor = FakeExecutor()

    class DriftingExecutor:
        provenance = original_executor.provenance
        attestation = original_executor.attestation

        def __call__(self, case, **kwargs):
            result = original_executor(case, **kwargs)
            embedding.drift = True
            return result

    def drift_providers(_config, _observer):
        return embedding, FakeGeneration()

    with pytest.raises(runner.LocalProductionBenchmarkError, match="changed"):
        runner.execute(
            env,
            case_executor=DriftingExecutor(),
            provider_factory=drift_providers,
        )


def test_executor_attestation_drift_fails_closed(tmp_path):
    pack_root, pack_sha = _pack(tmp_path)
    env = _env(tmp_path, pack_root, pack_sha)

    class DriftingExecutor(FakeExecutor):
        def __call__(self, case, **kwargs):
            result = super().__call__(case, **kwargs)
            self.attestation_drift = True
            return result

    with pytest.raises(
        runner.LocalProductionBenchmarkError, match="attestation changed"
    ):
        runner.execute(
            env,
            case_executor=DriftingExecutor(),
            provider_factory=_providers,
        )
    assert not Path(env["CV_EVAL_OUTPUT"]).exists()
