"""Fail-closed contract tests for the local BGE runtime verifier."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[5]
SCRIPT = REPO / "scripts/verify_local_bge.py"


def _module():
    spec = importlib.util.spec_from_file_location("cv_verify_local_bge", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(tmp_path: Path) -> Path:
    snapshot = tmp_path / "models--BAAI--bge-m3" / "snapshots" / "revision-a"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text('{"model_type":"xlm-roberta"}\n')
    (snapshot / "modules.json").write_text("[]\n")
    (snapshot / "pytorch_model.bin").write_bytes(b"model-weights")
    (snapshot / "tokenizer.json").write_text("{}\n")
    return snapshot


def test_snapshot_bundle_is_deterministic_and_byte_sensitive(tmp_path):
    module = _module()
    snapshot = _snapshot(tmp_path)

    first = module.snapshot_bundle(snapshot)
    second = module.snapshot_bundle(snapshot)

    assert first == second
    assert first["files"] == 4
    assert first["bytes"] > 0
    (snapshot / "tokenizer.json").write_text('{"changed":true}\n')
    assert module.snapshot_bundle(snapshot)["sha256"] != first["sha256"]


@pytest.mark.parametrize(
    ("deleted", "message"),
    [
        ("config.json", "config.json"),
        ("modules.json", "modules.json"),
        ("pytorch_model.bin", "model weights"),
        ("tokenizer.json", "tokenizer"),
    ],
)
def test_snapshot_validation_rejects_incomplete_model(tmp_path, deleted, message):
    module = _module()
    snapshot = _snapshot(tmp_path)
    (snapshot / deleted).unlink()

    with pytest.raises(module.LocalBgeError, match=message):
        module.validate_snapshot(
            snapshot,
            expected_model="BAAI/bge-m3",
            expected_revision="revision-a",
        )


def test_smoke_rejects_bundle_drift_before_model_load(tmp_path, monkeypatch):
    module = _module()
    snapshot = _snapshot(tmp_path)
    loaded = False

    def forbidden_loader(*_args, **_kwargs):
        nonlocal loaded
        loaded = True
        raise AssertionError("model loader must not run after hash drift")

    monkeypatch.setattr(module, "_load_and_encode", forbidden_loader)

    with pytest.raises(module.LocalBgeError, match="bundle SHA-256 mismatch"):
        module.verify_local_bge(
            snapshot=snapshot,
            expected_model="BAAI/bge-m3",
            expected_revision="revision-a",
            expected_bundle_sha256="0" * 64,
            texts=["query", "passage"],
            device="cpu",
        )
    assert loaded is False


def test_smoke_rejects_snapshot_change_during_inference(tmp_path, monkeypatch):
    module = _module()
    snapshot = _snapshot(tmp_path)
    bundle = module.snapshot_bundle(snapshot)

    def mutating_loader(*_args, **_kwargs):
        (snapshot / "tokenizer.json").write_text('{"changed":"during-load"}\n')
        return {
            "shape": [2, 1024],
            "finite": True,
            "norms": [1.0, 1.0],
            "cosine": 0.5,
            "load_seconds": 1.0,
            "encode_seconds": 2.0,
            "runtime_versions": {"torch": "test", "sentence_transformers": "test"},
        }

    monkeypatch.setattr(module, "_load_and_encode", mutating_loader)

    with pytest.raises(module.LocalBgeError, match="changed during inference"):
        module.verify_local_bge(
            snapshot=snapshot,
            expected_model="BAAI/bge-m3",
            expected_revision="revision-a",
            expected_bundle_sha256=bundle["sha256"],
            texts=["query", "passage"],
            device="cpu",
        )


def test_smoke_rejects_same_byte_symlink_retarget_during_inference(
    tmp_path, monkeypatch
):
    module = _module()
    snapshot = _snapshot(tmp_path)
    bundle = module.snapshot_bundle(snapshot)
    original = (snapshot / "tokenizer.json").read_bytes()
    outside = tmp_path / "same-bytes.json"
    outside.write_bytes(original)

    def retargeting_loader(*_args, **_kwargs):
        tokenizer = snapshot / "tokenizer.json"
        tokenizer.unlink()
        tokenizer.symlink_to(outside)
        return {
            "shape": [2, 1024],
            "finite": True,
            "norms": [1.0, 1.0],
            "cosine": 0.5,
            "load_seconds": 1.0,
            "encode_seconds": 2.0,
            "runtime_versions": {"torch": "test", "sentence_transformers": "test"},
        }

    monkeypatch.setattr(module, "_load_and_encode", retargeting_loader)

    with pytest.raises(module.LocalBgeError, match="escapes model cache root"):
        module.verify_local_bge(
            snapshot=snapshot,
            expected_model="BAAI/bge-m3",
            expected_revision="revision-a",
            expected_bundle_sha256=bundle["sha256"],
            texts=["query", "passage"],
            device="cpu",
        )


def test_snapshot_rejects_symlink_escape_from_model_cache_root(tmp_path):
    module = _module()
    snapshot = _snapshot(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"not-part-of-the-admitted-model")
    (snapshot / "pytorch_model.bin").unlink()
    (snapshot / "pytorch_model.bin").symlink_to(outside)

    with pytest.raises(module.LocalBgeError, match="escapes model cache root"):
        module.validate_snapshot(
            snapshot,
            expected_model="BAAI/bge-m3",
            expected_revision="revision-a",
        )


def test_smoke_report_distinguishes_local_inference_from_remote_provider(
    tmp_path, monkeypatch
):
    module = _module()
    snapshot = _snapshot(tmp_path)
    bundle = module.snapshot_bundle(snapshot)
    monkeypatch.setattr(
        module,
        "_load_and_encode",
        lambda *_args, **_kwargs: {
            "shape": [2, 1024],
            "finite": True,
            "norms": [1.0, 1.0],
            "cosine": 0.5,
            "load_seconds": 1.0,
            "encode_seconds": 2.0,
            "runtime_versions": {"torch": "test", "sentence_transformers": "test"},
        },
    )
    monkeypatch.setattr(
        module,
        "_provenance",
        lambda: {
            "dependency_lock_sha256": "f" * 64,
            "verifier_sha256": "e" * 64,
            "repository_revision": "a" * 40,
        },
    )

    report = module.verify_local_bge(
        snapshot=snapshot,
        expected_model="BAAI/bge-m3",
        expected_revision="revision-a",
        expected_bundle_sha256=bundle["sha256"],
        texts=["query", "passage"],
        device="cpu",
    )

    assert report["result"] == "PASS"
    assert report["local_model_invoked"] is True
    assert report["remote_provider_invoked"] is False
    assert report["library_offline_mode"] is True
    assert report["network_isolation_verified"] is False
    assert report["network_policy"] == "huggingface-local-files-only"
    assert "network_allowed" not in report
    assert report["model_bundle_sha256"] == bundle["sha256"]
    assert report["embedding_dimension"] == 1024
    assert report["dependency_lock_sha256"] == "f" * 64
    assert report["verifier_sha256"] == "e" * 64
    assert report["repository_revision"] == "a" * 40
    assert "snapshot" not in json.dumps(report).lower()


def test_json_output_refuses_existing_file_and_symlink(tmp_path):
    module = _module()
    existing = tmp_path / "existing.json"
    existing.write_text("do-not-overwrite")
    target = tmp_path / "target.json"
    target.write_text("also-do-not-overwrite")
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(module.LocalBgeError, match="new regular file"):
        module.write_json_output(existing, {"result": "PASS"})
    with pytest.raises(module.LocalBgeError, match="new regular file"):
        module.write_json_output(link, {"result": "PASS"})
    assert existing.read_text() == "do-not-overwrite"
    assert target.read_text() == "also-do-not-overwrite"


def test_local_eval_dependencies_are_an_opt_in_extra_not_an_all_groups_group():
    pyproject = (
        REPO / "document-rag-platform/services/backend/pyproject.toml"
    ).read_text()

    assert "[project.optional-dependencies]" in pyproject
    optional_section = pyproject.split("[project.optional-dependencies]", 1)[1].split(
        "[dependency-groups]", 1
    )[0]
    groups_section = pyproject.split("[dependency-groups]", 1)[1].split("[tool.uv]", 1)[
        0
    ]
    assert "local-eval" in optional_section
    assert "local-eval" not in groups_section


def test_local_eval_extra_is_supply_chain_scanned_but_not_in_normal_ci():
    security = (REPO / ".github/workflows/ci-security.yml").read_text()
    backend = (REPO / ".github/workflows/ci-backend.yml").read_text()
    rag = (REPO / ".github/workflows/ci-rag-eval.yml").read_text()

    assert "uv sync --frozen --all-groups --extra local-eval" in security
    assert "--extra local-eval" not in backend
    assert "--extra local-eval" not in rag
