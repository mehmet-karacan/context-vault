"""Fail-closed contract tests for the local generation runtime verifier."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[5]
SCRIPT = REPO / "scripts/verify_local_generation.py"


def _module():
    spec = importlib.util.spec_from_file_location("cv_verify_local_generation", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(tmp_path: Path) -> Path:
    snapshot = (
        tmp_path / "models--Qwen--Qwen2.5-0.5B-Instruct" / "snapshots" / "revision-a"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text(
        '{"model_type":"qwen2","architectures":["Qwen2ForCausalLM"]}\n'
    )
    (snapshot / "generation_config.json").write_text("{}\n")
    (snapshot / "model.safetensors").write_bytes(b"model-weights")
    (snapshot / "tokenizer.json").write_text("{}\n")
    (snapshot / "tokenizer_config.json").write_text("{}\n")
    return snapshot


def _inference(*, grounded=True, no_answer=True):
    return {
        "load_seconds": 1.0,
        "generation_seconds": 2.0,
        "grounded_guard_pass": grounded,
        "no_answer_guard_pass": no_answer,
        "deterministic_repeat_match": True,
        "prompt_tokens": 42,
        "generated_tokens": 8,
        "grounded_output_sha256": (
            "b850c26c1da5cac3bd8f2011776506fd3193ade0cd4e917fa14a135465689411"
        ),
        "no_answer_output_sha256": (
            "4e1d96a3d622df6b6aae19c1018f4984a1bc5208ae869595dc1a6ce6f01542f1"
        ),
        "runtime_versions": {
            "python": "3.12.14",
            "torch": "2.14.0",
            "transformers": "5.15.1",
        },
    }


def test_snapshot_bundle_is_deterministic_and_byte_sensitive(tmp_path):
    module = _module()
    snapshot = _snapshot(tmp_path)
    first = module.snapshot_bundle(snapshot)
    assert first == module.snapshot_bundle(snapshot)
    assert first["files"] == 5
    assert first["bytes"] > 0
    (snapshot / "tokenizer.json").write_text('{"changed":true}\n')
    assert module.snapshot_bundle(snapshot)["sha256"] != first["sha256"]


def test_snapshot_bundle_enforces_file_and_byte_limits(tmp_path, monkeypatch):
    module = _module()
    snapshot = _snapshot(tmp_path)
    monkeypatch.setattr(module, "MAX_SNAPSHOT_FILES", 4)
    with pytest.raises(module.LocalGenerationError, match="file-count limit"):
        module.snapshot_bundle(snapshot)
    monkeypatch.setattr(module, "MAX_SNAPSHOT_FILES", 64)
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", 1)
    with pytest.raises(module.LocalGenerationError, match="byte-size limit"):
        module.snapshot_bundle(snapshot)


@pytest.mark.parametrize(
    ("deleted", "message"),
    [
        ("config.json", "config.json"),
        ("generation_config.json", "generation_config.json"),
        ("model.safetensors", "model weights"),
        ("tokenizer.json", "tokenizer"),
        ("tokenizer_config.json", "tokenizer_config.json"),
    ],
)
def test_snapshot_validation_rejects_incomplete_model(tmp_path, deleted, message):
    module = _module()
    snapshot = _snapshot(tmp_path)
    (snapshot / deleted).unlink()
    with pytest.raises(module.LocalGenerationError, match=message):
        module.validate_snapshot(
            snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
        )


def test_snapshot_validation_rejects_wrong_architecture(tmp_path):
    module = _module()
    snapshot = _snapshot(tmp_path)
    (snapshot / "config.json").write_text(
        '{"model_type":"bert","architectures":["BertModel"]}\n'
    )
    with pytest.raises(module.LocalGenerationError, match="architecture"):
        module.validate_snapshot(
            snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
        )


def test_snapshot_validation_rejects_string_architectures(tmp_path):
    module = _module()
    snapshot = _snapshot(tmp_path)
    (snapshot / "config.json").write_text(
        '{"model_type":"qwen2","architectures":"Qwen2ForCausalLM"}\n'
    )
    with pytest.raises(module.LocalGenerationError, match="architecture"):
        module.validate_snapshot(
            snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
        )


@pytest.mark.parametrize("malformed", ["[]\n", '"not-an-object"\n'])
def test_snapshot_validation_wraps_malformed_config_shape(tmp_path, malformed):
    module = _module()
    snapshot = _snapshot(tmp_path)
    (snapshot / "config.json").write_text(malformed)
    with pytest.raises(module.LocalGenerationError, match="model config"):
        module.validate_snapshot(
            snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
        )


def test_smoke_rejects_bundle_drift_before_model_load(tmp_path, monkeypatch):
    module = _module()
    snapshot = _snapshot(tmp_path)
    loaded = False

    def forbidden_loader(*_args, **_kwargs):
        nonlocal loaded
        loaded = True
        raise AssertionError("loader must not run after hash drift")

    monkeypatch.setattr(module, "_load_and_generate", forbidden_loader)
    with pytest.raises(module.LocalGenerationError, match="bundle SHA-256 mismatch"):
        module.verify_local_generation(
            snapshot=snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
            expected_bundle_sha256="0" * 64,
            device="cpu",
        )
    assert loaded is False


def test_smoke_rejects_snapshot_change_during_inference(tmp_path, monkeypatch):
    module = _module()
    snapshot = _snapshot(tmp_path)
    bundle = module.snapshot_bundle(snapshot)

    def mutating_loader(*_args, **_kwargs):
        (snapshot / "tokenizer.json").write_text('{"changed":"during-load"}\n')
        return _inference()

    monkeypatch.setattr(module, "_load_and_generate", mutating_loader)
    with pytest.raises(module.LocalGenerationError, match="changed during inference"):
        module.verify_local_generation(
            snapshot=snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
            expected_bundle_sha256=bundle["sha256"],
            device="cpu",
        )


def test_snapshot_rejects_symlink_escape_from_model_cache_root(tmp_path):
    module = _module()
    snapshot = _snapshot(tmp_path)
    outside = tmp_path / "outside.safetensors"
    outside.write_bytes(b"not-admitted")
    (snapshot / "model.safetensors").unlink()
    (snapshot / "model.safetensors").symlink_to(outside)
    with pytest.raises(module.LocalGenerationError, match="escapes model cache root"):
        module.validate_snapshot(
            snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
        )


def test_same_byte_symlink_retarget_during_inference_is_rejected(tmp_path, monkeypatch):
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
        return _inference()

    monkeypatch.setattr(module, "_load_and_generate", retargeting_loader)
    with pytest.raises(module.LocalGenerationError, match="escapes model cache root"):
        module.verify_local_generation(
            snapshot=snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
            expected_bundle_sha256=bundle["sha256"],
            device="cpu",
        )


def test_same_byte_in_cache_symlink_retarget_during_inference_is_rejected(
    tmp_path, monkeypatch
):
    module = _module()
    snapshot = _snapshot(tmp_path)
    cache_root = snapshot.parent.parent
    blobs = cache_root / "blobs"
    blobs.mkdir()
    first = blobs / "first"
    second = blobs / "second"
    first.write_bytes(b'{"same":true}\n')
    second.write_bytes(first.read_bytes())
    tokenizer = snapshot / "tokenizer.json"
    tokenizer.unlink()
    tokenizer.symlink_to(first)
    bundle = module.snapshot_bundle(snapshot)

    def retargeting_loader(*_args, **_kwargs):
        tokenizer.unlink()
        tokenizer.symlink_to(second)
        return _inference()

    monkeypatch.setattr(module, "_load_and_generate", retargeting_loader)
    with pytest.raises(module.LocalGenerationError, match="changed during inference"):
        module.verify_local_generation(
            snapshot=snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
            expected_bundle_sha256=bundle["sha256"],
            device="cpu",
        )


@pytest.mark.parametrize("failed_guard", ["grounded", "no_answer", "repeat"])
def test_generation_guards_fail_closed(tmp_path, monkeypatch, failed_guard):
    module = _module()
    snapshot = _snapshot(tmp_path)
    bundle = module.snapshot_bundle(snapshot)
    inference = _inference(
        grounded=failed_guard != "grounded", no_answer=failed_guard != "no_answer"
    )
    if failed_guard == "repeat":
        inference["deterministic_repeat_match"] = False
    monkeypatch.setattr(
        module, "_load_and_generate", lambda *_args, **_kwargs: inference
    )
    with pytest.raises(module.LocalGenerationError, match="generation guard"):
        module.verify_local_generation(
            snapshot=snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
            expected_bundle_sha256=bundle["sha256"],
            device="cpu",
        )


@pytest.mark.parametrize(
    "grounded",
    [
        "KEEP_EXISTING_USER_DATA plus unsupported claim",
        "DELETE everything then KEEP_EXISTING_USER_DATA",
    ],
)
def test_grounded_guard_rejects_overclaim_and_unsafe_text(
    grounded,
):
    module = _module()
    assert module._grounded_guard_pass(grounded) is False


def test_grounded_guard_accepts_only_exact_token():
    module = _module()
    assert module._grounded_guard_pass("KEEP_EXISTING_USER_DATA") is True


def test_inference_cannot_add_or_override_report_envelope(tmp_path, monkeypatch):
    module = _module()
    snapshot = _snapshot(tmp_path)
    bundle = module.snapshot_bundle(snapshot)
    poisoned = {
        **_inference(),
        "raw_output": "TOP_SECRET",
        "model": "attacker/model",
        "result": "FORGED",
        "generation_text_retained": True,
        "repository_revision": "0" * 40,
    }
    monkeypatch.setattr(
        module, "_load_and_generate", lambda *_args, **_kwargs: poisoned
    )
    with pytest.raises(module.LocalGenerationError, match="inference report"):
        module.verify_local_generation(
            snapshot=snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
            expected_bundle_sha256=bundle["sha256"],
            device="cpu",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("grounded_guard_pass", 1),
        ("load_seconds", float("inf")),
        ("generation_seconds", -1),
        ("prompt_tokens", True),
        ("generated_tokens", -1),
        ("grounded_output_sha256", "not-a-hash"),
        ("runtime_versions", {"python": "test"}),
        ("load_seconds", 1e300),
        ("prompt_tokens", 10**100),
        ("generated_tokens", 73),
        ("grounded_output_sha256", "0" * 64),
        (
            "runtime_versions",
            {"python": "3.12.14", "torch": "evil", "transformers": "5.15.1"},
        ),
    ],
)
def test_inference_report_rejects_invalid_types_and_ranges(
    tmp_path, monkeypatch, field, value
):
    module = _module()
    snapshot = _snapshot(tmp_path)
    bundle = module.snapshot_bundle(snapshot)
    malformed = {**_inference(), field: value}
    monkeypatch.setattr(
        module, "_load_and_generate", lambda *_args, **_kwargs: malformed
    )
    with pytest.raises(module.LocalGenerationError, match="inference report"):
        module.verify_local_generation(
            snapshot=snapshot,
            expected_model="Qwen/Qwen2.5-0.5B-Instruct",
            expected_revision="revision-a",
            expected_bundle_sha256=bundle["sha256"],
            device="cpu",
        )


def test_bounded_worker_fails_closed_on_timeout(monkeypatch, capsys):
    module = _module()

    def timeout(*_args, **_kwargs):
        raise module.subprocess.TimeoutExpired(cmd="worker", timeout=1)

    monkeypatch.setattr(module.subprocess, "run", timeout)
    assert module._run_bounded_worker(1) == 2
    assert "timed out" in capsys.readouterr().out


@pytest.mark.parametrize("timeout_seconds", [0, 601, 3600])
def test_bounded_worker_rejects_unbounded_timeout(capsys, timeout_seconds):
    module = _module()
    assert module._run_bounded_worker(timeout_seconds) == 2
    assert "out of range" in capsys.readouterr().out


def test_direct_worker_mode_without_parent_capability_fails_closed(
    tmp_path, monkeypatch, capsys
):
    module = _module()
    snapshot = _snapshot(tmp_path)
    invoked = False

    def forbidden_verify(**_kwargs):
        nonlocal invoked
        invoked = True
        return {"result": "PASS"}

    monkeypatch.setattr(module, "verify_local_generation", forbidden_verify)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            str(module.__file__),
            "--snapshot",
            str(snapshot),
            "--expected-model",
            "Qwen/Qwen2.5-0.5B-Instruct",
            "--expected-revision",
            "revision-a",
            "--expected-bundle-sha256",
            "0" * 64,
            "--worker-fd",
            "99",
        ],
    )
    assert module.main() == 2
    assert invoked is False
    assert "worker capability is invalid" in capsys.readouterr().out


def test_report_is_local_bounded_and_provenance_bound(tmp_path, monkeypatch):
    module = _module()
    snapshot = _snapshot(tmp_path)
    bundle = module.snapshot_bundle(snapshot)
    monkeypatch.setattr(
        module, "_load_and_generate", lambda *_args, **_kwargs: _inference()
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
    report = module.verify_local_generation(
        snapshot=snapshot,
        expected_model="Qwen/Qwen2.5-0.5B-Instruct",
        expected_revision="revision-a",
        expected_bundle_sha256=bundle["sha256"],
        device="cpu",
    )
    assert report["result"] == "PASS"
    assert report["local_model_invoked"] is True
    assert report["remote_provider_invoked"] is False
    assert report["library_offline_mode"] is True
    assert report["network_isolation_verified"] is False
    assert report["grounded_guard_pass"] is True
    assert report["no_answer_guard_pass"] is True
    assert report["deterministic_repeat_match"] is True
    assert report["model_resource_limits"]["max_files"] == 64
    assert report["model_resource_limits"]["max_bytes"] == 4_000_000_000
    assert report["execution_wall_clock_limit_seconds"] == 600
    assert len(report["prompt_case_bundle_sha256"]) == 64
    assert report["dependency_lock_sha256"] == "f" * 64
    assert report["verifier_sha256"] == "e" * 64
    assert "snapshot" not in json.dumps(report).lower()
    assert "raw_output" not in json.dumps(report).lower()


def test_json_output_refuses_existing_file_and_symlink(tmp_path):
    module = _module()
    existing = tmp_path / "existing.json"
    existing.write_text("do-not-overwrite")
    target = tmp_path / "target.json"
    target.write_text("also-do-not-overwrite")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(module.LocalGenerationError, match="new regular file"):
        module.write_json_output(existing, {"result": "PASS"})
    with pytest.raises(module.LocalGenerationError, match="new regular file"):
        module.write_json_output(link, {"result": "PASS"})
    assert existing.read_text() == "do-not-overwrite"
    assert target.read_text() == "also-do-not-overwrite"
