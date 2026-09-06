from __future__ import annotations

import io
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest

from src.context_vault.cli import build_parser, main


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        self.closed = False

    def invoke(self, command: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((command, payload))
        return {
            "z": 2,
            "a": 1,
            "content": "must-not-leak",
            "password": "also-must-not-leak",
        }

    def close(self) -> None:
        self.closed = True


def _run(argv: list[str], runtime: FakeRuntime) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(argv, runtime=runtime, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _json_file(tmp_path: Path, name: str, value: object) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def test_parser_exposes_every_minimum_command() -> None:
    help_text = build_parser().format_help()
    for group in (
        "doctor",
        "project",
        "work",
        "context",
        "knowledge",
        "provider",
        "skill",
    ):
        assert group in help_text


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["doctor"], "doctor"),
        (["project", "register"], "project.register"),
        (["work", "reconcile"], "work.reconcile"),
    ],
)
def test_simple_commands_have_json_and_human_modes(
    argv: list[str], command: str
) -> None:
    runtime = FakeRuntime()
    code, human, error = _run(argv, runtime)
    assert (code, error) == (0, "")
    assert human.startswith(f"OK {command}")
    assert "must-not-leak" not in human
    assert "also-must-not-leak" not in human

    runtime = FakeRuntime()
    code, raw_json, error = _run([*argv, "--json"], runtime)
    assert (code, error) == (0, "")
    assert raw_json == ('{"command":"%s","ok":true,"result":{"a":1,"z":2}}\n' % command)
    assert runtime.closed


def test_work_flow_arguments_are_typed_and_forwarded(tmp_path: Path) -> None:
    prepared = _json_file(
        tmp_path,
        "prepared.json",
        {
            "work_item_id": "11111111-1111-4111-8111-111111111111",
            "work_item_revision": 1,
            "expected_revision": "abc",
            "drift_token": "d" * 64,
            "scope_hash": "e" * 64,
        },
    )
    scope = _json_file(
        tmp_path,
        "scope.json",
        {
            "paths": ["evidence/marker.json"],
            "capabilities": ["write_receipt_marker"],
        },
    )
    content = tmp_path / "marker.json"
    content.write_text('{"status":"verified"}\n')
    cases = [
        (["work", "create", "--request", scope], "work.create"),
        (
            [
                "work",
                "prepare",
                "11111111-1111-4111-8111-111111111111",
                "--observed-revision",
                "abc",
            ],
            "work.prepare",
        ),
        (
            [
                "work",
                "claim",
                "11111111-1111-4111-8111-111111111111",
                "--prepared",
                prepared,
                "--scope",
                scope,
                "--claimant-id",
                "cli",
                "--executor-id",
                "worker",
                "--idempotency-key",
                "claim-1",
            ],
            "work.claim",
        ),
        (
            [
                "work",
                "heartbeat",
                "22222222-2222-4222-8222-222222222222",
                "--fencing-token",
                "3",
            ],
            "work.heartbeat",
        ),
        (
            [
                "work",
                "apply",
                "11111111-1111-4111-8111-111111111111",
                "--claim",
                "22222222-2222-4222-8222-222222222222",
                "--fencing-token",
                "3",
                "--expected-revision",
                "abc",
                "--idempotency-key",
                "apply-1",
                "--scope",
                scope,
                "--action",
                "write_receipt_marker",
                "--relative-path",
                "evidence/marker.json",
                "--content-file",
                str(content),
                "--signer-id",
                "cli-1",
                "--dry-run",
            ],
            "work.apply",
        ),
        (
            [
                "work",
                "verify",
                "11111111-1111-4111-8111-111111111111",
                "--attempt",
                "33333333-3333-4333-8333-333333333333",
                "--idempotency-key",
                "verify-1",
                "--acceptance-evidence",
                "receipt://a",
                "--test-evidence",
                "receipt://t",
                "--observed-revision",
                "abc",
                "--signer-id",
                "cli-1",
            ],
            "work.verify",
        ),
        (
            [
                "work",
                "close",
                "11111111-1111-4111-8111-111111111111",
                "--attempt",
                "33333333-3333-4333-8333-333333333333",
                "--verify-receipt",
                "44444444-4444-4444-8444-444444444444",
                "--idempotency-key",
                "close-1",
                "--after-revision",
                "abc",
                "--signer-id",
                "cli-1",
            ],
            "work.close",
        ),
    ]
    runtime = FakeRuntime()
    for argv, command in cases:
        assert _run([*argv, "--json"], runtime)[0] == 0
        assert runtime.calls[-1][0] == command
    apply_payload = next(
        payload for command, payload in runtime.calls if command == "work.apply"
    )
    assert apply_payload["fencing_token"] == 3
    assert apply_payload["scope"]["capabilities"] == ["write_receipt_marker"]
    assert apply_payload["dry_run"] is True


@pytest.mark.parametrize(
    ("field", "value", "detail"),
    (
        ("priority", True, "priority must be an integer"),
        (
            "workspace_id",
            12345678123456781234567812345678,
            "workspace_id must be a UUID string",
        ),
        ("risk_class", 123, "risk_class is invalid"),
        ("evidence_requirements", [1], "must contain non-empty strings"),
        ("acceptance_criteria", [True], "must contain non-empty JSON objects"),
    ),
)
def test_work_create_rejects_inexact_json_types(
    tmp_path: Path, field: str, value: object, detail: str
) -> None:
    from src.context_vault.cli import DefaultRuntime

    request_payload: dict[str, Any] = {
        "workspace_id": "11111111-1111-4111-8111-111111111111",
        "title": "Verify",
        "objective": "Prove exact request typing",
        "scope": {
            "paths": ["evidence/marker.json"],
            "capabilities": ["write_receipt_marker"],
        },
        "expected_revision": "a" * 40,
        "created_by_principal_id": "22222222-2222-4222-8222-222222222222",
        "acceptance_criteria": [{"id": "tests", "required": True}],
        "evidence_requirements": ["receipt://tests"],
        "priority": 1,
        "risk_class": "low",
    }
    request_payload[field] = value
    request = _json_file(tmp_path, f"work-{field}.json", request_payload)
    runtime = DefaultRuntime()
    code, output, _ = _run(["work", "create", "--request", request, "--json"], runtime)

    assert code == 2
    assert detail in json.loads(output)["error"]["detail"]


@pytest.mark.parametrize(
    ("group", "action"),
    [
        ("context", "compile"),
        ("knowledge", "propose"),
        ("knowledge", "review"),
        ("knowledge", "approve"),
        ("provider", "test"),
        ("skill", "verify"),
    ],
)
def test_domain_commands_require_bounded_json_request(
    tmp_path: Path, group: str, action: str
) -> None:
    request = _json_file(tmp_path, f"{group}-{action}.json", {"opaque": True})
    runtime = FakeRuntime()
    assert _run([group, action, "--request", request, "--json"], runtime)[0] == 0
    assert runtime.calls == [(f"{group}.{action}", {"request": {"opaque": True}})]


def test_request_loader_rejects_symlink_and_does_not_echo_raw_content(
    tmp_path: Path,
) -> None:
    target = tmp_path / "secret.json"
    target.write_text('{"content":"raw-secret"}', encoding="utf-8")
    link = tmp_path / "request.json"
    link.symlink_to(target)
    runtime = FakeRuntime()
    code, output, error = _run(
        ["context", "compile", "--request", str(link), "--json"], runtime
    )
    assert (code, error) == (2, "")
    assert "raw-secret" not in output
    assert json.loads(output)["error"]["code"] == "CliError"
    assert runtime.calls == []


def test_apply_rejects_non_allowlisted_effect_at_parse_time(tmp_path: Path) -> None:
    scope = _json_file(
        tmp_path,
        "scope.json",
        {
            "paths": ["evidence/marker.json"],
            "capabilities": ["write_receipt_marker"],
        },
    )
    with pytest.raises(SystemExit):
        main(
            [
                "work",
                "apply",
                "11111111-1111-4111-8111-111111111111",
                "--claim",
                "22222222-2222-4222-8222-222222222222",
                "--fencing-token",
                "1",
                "--expected-revision",
                "abc",
                "--idempotency-key",
                "x",
                "--scope",
                scope,
                "--action",
                "shell",
                "--relative-path",
                "evidence/marker.json",
                "--content-file",
                scope,
                "--signer-id",
                "cli",
            ],
            runtime=FakeRuntime(),
        )


def test_scope_rejects_parent_escape_before_runtime(tmp_path: Path) -> None:
    scope = _json_file(
        tmp_path,
        "scope.json",
        {
            "paths": ["../outside"],
            "capabilities": ["write_receipt_marker"],
        },
    )
    runtime = FakeRuntime()
    code, output, _ = _run(
        [
            "work",
            "apply",
            "11111111-1111-4111-8111-111111111111",
            "--claim",
            "22222222-2222-4222-8222-222222222222",
            "--fencing-token",
            "1",
            "--expected-revision",
            "abc",
            "--idempotency-key",
            "x",
            "--scope",
            scope,
            "--action",
            "write_receipt_marker",
            "--relative-path",
            "../outside",
            "--content-file",
            scope,
            "--signer-id",
            "cli",
            "--dry-run",
            "--json",
        ],
        runtime,
    )
    assert code == 2
    assert json.loads(output)["ok"] is False
    assert runtime.calls == []


def test_real_dry_run_is_side_effect_free_without_database(tmp_path: Path) -> None:
    from src.context_vault.cli import DefaultRuntime

    scope = _json_file(
        tmp_path,
        "scope.json",
        {
            "paths": ["evidence/marker.json"],
            "capabilities": ["write_receipt_marker"],
        },
    )
    content = tmp_path / "marker.json"
    content.write_text('{"status":"verified"}\n')
    code, output, error = _run(
        [
            "work",
            "apply",
            "11111111-1111-4111-8111-111111111111",
            "--claim",
            "22222222-2222-4222-8222-222222222222",
            "--fencing-token",
            "1",
            "--expected-revision",
            "abc",
            "--idempotency-key",
            "x",
            "--scope",
            scope,
            "--action",
            "write_receipt_marker",
            "--relative-path",
            "evidence/marker.json",
            "--content-file",
            str(content),
            "--signer-id",
            "cli",
            "--dry-run",
            "--json",
        ],
        DefaultRuntime(),
    )
    assert (code, error) == (0, "")
    result = json.loads(output)["result"]
    assert result["dry_run"] is True
    assert result["effect_applied"] is False


def test_default_runtime_binds_typed_apply_to_work_graph_service(
    tmp_path: Path,
) -> None:
    from src.application.work_graph import ApplyResult, EffectRequest
    from src.context_vault.cli import DefaultRuntime

    @dataclass
    class ServiceSpy:
        call: Mapping[str, Any] | None = None

        def apply(self, work_item_id: object, **kwargs: Any) -> ApplyResult:
            self.call = {"work_item_id": work_item_id, **kwargs}
            request = kwargs["effect_request"]
            assert isinstance(request, EffectRequest)
            assert request.relative_path == "evidence/marker.json"
            return ApplyResult(
                receipt_id=uuid.UUID("55555555-5555-4555-8555-555555555555"),
                attempt_id=uuid.UUID("33333333-3333-4333-8333-333333333333"),
                replayed=False,
                exit_status=0,
                output_artifact_hash="b" * 64,
                after_revision="b" * 64,
            )

    runtime = DefaultRuntime()
    spy = ServiceSpy()
    runtime._effect_service = lambda effect_root: spy  # type: ignore[method-assign]
    content = tmp_path / "marker.json"
    content.write_text('{"status":"verified"}\n')
    result = runtime.invoke(
        "work.apply",
        {
            "work_item_id": "11111111-1111-4111-8111-111111111111",
            "claim_id": "22222222-2222-4222-8222-222222222222",
            "fencing_token": 9,
            "expected_revision": "a" * 40,
            "idempotency_key": "apply-1",
            "scope": {
                "paths": ["evidence/marker.json"],
                "capabilities": ["write_receipt_marker"],
            },
            "action": "write_receipt_marker",
            "relative_path": "evidence/marker.json",
            "content_file": str(content),
            "effect_root": str(tmp_path),
            "signer_id": "cli-1",
            "dry_run": False,
        },
    )
    assert result["after_revision"] == "b" * 64
    assert spy.call is not None
    assert spy.call["fencing_token"] == 9
    assert spy.call["observed_revision"] == "a" * 40
    assert spy.call["action"] == "write_receipt_marker"


def test_default_context_compile_uses_domain_api_without_echoing_content(
    tmp_path: Path,
) -> None:
    request = _json_file(
        tmp_path,
        "context.json",
        {
            "attempt_id": "attempt-1",
            "context_window": 100,
            "reserved_output": 10,
            "safety_margin": 10,
            "provider_policy": {
                "provider_id": "local-bge",
                "is_remote": False,
                "allowed_classifications": ["INTERNAL"],
            },
            "items": [
                {
                    "source_id": "policy-1",
                    "logical_id": "policy",
                    "version": 1,
                    "content": "private raw policy text",
                    "reason": "mandatory security policy",
                    "token_cost": 23,
                    "load_tier": "MUST_LOAD",
                    "classification": "INTERNAL",
                    "role": "SECURITY_POLICY",
                }
            ],
        },
    )
    from src.context_vault.cli import DefaultRuntime

    code, output, error = _run(
        ["context", "compile", "--request", request, "--json"], DefaultRuntime()
    )
    assert (code, error) == (0, "")
    decoded = json.loads(output)
    assert decoded["result"]["included"][0]["source_id"] == "policy-1"
    assert len(decoded["result"]["manifest_hash"]) == 64
    assert "private raw policy text" not in output


@pytest.mark.parametrize(
    ("field", "value"),
    [("preload_all", "false"), ("provider_policy.is_remote", "false")],
)
def test_context_json_boolean_fields_reject_string_coercion(
    tmp_path: Path, field: str, value: object
) -> None:
    from src.context_vault.cli import DefaultRuntime

    payload: dict[str, Any] = {
        "attempt_id": "attempt-1",
        "context_window": 100,
        "reserved_output": 10,
        "safety_margin": 10,
        "preload_all": False,
        "provider_policy": {
            "provider_id": "local-bge",
            "is_remote": False,
            "allowed_classifications": ["INTERNAL"],
        },
        "items": [],
    }
    if field == "provider_policy.is_remote":
        payload["provider_policy"]["is_remote"] = value
    else:
        payload[field] = value
    request = _json_file(tmp_path, "context-bool.json", payload)

    code, output, _ = _run(
        ["context", "compile", "--request", request, "--json"], DefaultRuntime()
    )

    assert code == 2
    assert "must be a boolean" in json.loads(output)["error"]["detail"]


def test_request_json_rejects_non_finite_numbers(tmp_path: Path) -> None:
    request = tmp_path / "nonfinite.json"
    request.write_text('{"confidence":NaN}')
    runtime = FakeRuntime()

    code, output, _ = _run(
        ["knowledge", "propose", "--request", str(request), "--json"], runtime
    )

    assert code == 2
    assert json.loads(output)["error"]["detail"] == ("request is not valid UTF-8 JSON")
    assert runtime.calls == []


def test_context_cli_binds_same_core_to_adapter_hashes_without_raw_output(
    tmp_path: Path,
) -> None:
    from src.context_vault.cli import DefaultRuntime

    attempt_id = "33333333-3333-4333-8333-333333333333"

    @dataclass
    class RegistrySpy:
        manifest_hash: str | None = None

        def bind_compiled_context(self, **kwargs: Any) -> Mapping[str, Any]:
            self.manifest_hash = kwargs["compiled"].manifest_hash
            return {
                "adapter_hashes": {
                    item.adapter_id: item.projection_receipt_hash
                    for item in kwargs["projections"]
                }
            }

    request = _json_file(
        tmp_path,
        "bound-context.json",
        {
            "attempt_id": attempt_id,
            "context_window": 100,
            "reserved_output": 10,
            "safety_margin": 10,
            "provider_policy": {
                "provider_id": "local-bge",
                "is_remote": False,
                "allowed_classifications": ["INTERNAL"],
            },
            "items": [
                {
                    "source_id": "policy-1",
                    "logical_id": "policy",
                    "version": 1,
                    "content": "private raw policy text",
                    "reason": "mandatory security policy",
                    "token_cost": 23,
                    "load_tier": "MUST_LOAD",
                    "classification": "INTERNAL",
                    "role": "SECURITY_POLICY",
                }
            ],
            "binding": {
                "model_id": "bge-m3@exact",
                "adapter_ids": ["codex", "claude"],
                "tool_mapping": {"read": "read_file"},
                "work_authority": {
                    "work_item_id": "11111111-1111-4111-8111-111111111111",
                    "attempt_id": attempt_id,
                    "claim_id": "22222222-2222-4222-8222-222222222222",
                    "fencing_token": 1,
                    "current_revision": "a" * 40,
                },
            },
        },
    )
    spy = RegistrySpy()
    runtime = DefaultRuntime()
    runtime._registry_service = lambda: spy  # type: ignore[method-assign]

    code, output, error = _run(
        ["context", "compile", "--request", request, "--json"], runtime
    )

    assert (code, error) == (0, "")
    decoded = json.loads(output)["result"]
    assert decoded["persisted"] is True
    assert set(decoded["adapter_hashes"]) == {"claude", "codex"}
    assert decoded["manifest_hash"] == spy.manifest_hash
    assert "private raw policy text" not in output


def test_default_knowledge_cli_uses_persistence_and_never_echoes_content(
    tmp_path: Path,
) -> None:
    from src.context_vault.cli import DefaultRuntime

    @dataclass
    class RegistrySpy:
        call: Mapping[str, Any] | None = None

        def propose_knowledge(self, **kwargs: Any) -> Mapping[str, Any]:
            self.call = kwargs
            return {
                "revision_id": "33333333-3333-4333-8333-333333333333",
                "content_hash": "a" * 64,
                "status": "PROPOSED",
                "content": kwargs["content"],
            }

    request = _json_file(
        tmp_path,
        "knowledge.json",
        {
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "project_id": "22222222-2222-4222-8222-222222222222",
            "stable_key": "project.authority",
            "content": "raw private knowledge",
            "source_ids": ["source:adr"],
            "evidence_ids": ["receipt:test"],
            "owner_id": "44444444-4444-4444-8444-444444444444",
            "scope": "project",
            "confidence": 0.9,
            "classification": "INTERNAL",
            "proposed_by": {"kind": "MODEL", "actor_id": "local-qwen"},
            "valid_from": "2026-09-06T12:00:00Z",
        },
    )
    spy = RegistrySpy()
    runtime = DefaultRuntime()
    runtime._registry_service = lambda: spy  # type: ignore[method-assign]
    code, output, error = _run(
        ["knowledge", "propose", "--request", request, "--json"], runtime
    )

    assert (code, error) == (0, "")
    assert spy.call is not None
    assert spy.call["content"] == "raw private knowledge"
    assert "raw private knowledge" not in output
    assert json.loads(output)["result"]["status"] == "PROPOSED"


def test_default_knowledge_cli_rejects_unbounded_shape_before_persistence(
    tmp_path: Path,
) -> None:
    from src.context_vault.cli import DefaultRuntime

    request = _json_file(
        tmp_path,
        "knowledge.json",
        {"revision_id": str(uuid.uuid4()), "actor": {}, "unexpected": True},
    )
    runtime = DefaultRuntime()
    code, output, _ = _run(
        ["knowledge", "review", "--request", request, "--json"], runtime
    )

    assert code == 2
    assert json.loads(output)["error"]["detail"] == (
        "knowledge request contains unknown fields"
    )


def test_provider_cli_persists_local_bge_metadata_without_model_call(
    tmp_path: Path,
) -> None:
    from src.context_vault.cli import DefaultRuntime

    @dataclass
    class RegistrySpy:
        provider_id: str | None = None

        def register_provider(self, record: Any) -> Mapping[str, Any]:
            self.provider_id = record.provider_id
            return {"provider_id": record.provider_id, "metadata_only": True}

    request = _json_file(
        tmp_path,
        "provider.json",
        {
            "provider": {
                "provider_id": "local-bge-m3",
                "adapter_id": "openai-compatible",
                "locality": "local",
                "network_required": False,
                "health": "healthy",
                "circuit_open": False,
                "version": "BAAI/bge-m3@6c6f",
                "config_hash": "a" * 64,
                "metadata": {"endpoint_ref": "config://providers/local-bge"},
            }
        },
    )
    spy = RegistrySpy()
    runtime = DefaultRuntime()
    runtime._registry_service = lambda: spy  # type: ignore[method-assign]

    code, output, error = _run(
        ["provider", "test", "--request", request, "--json"], runtime
    )

    assert (code, error) == (0, "")
    assert spy.provider_id == "local-bge-m3"
    result = json.loads(output)["result"]
    assert result["provider_call_performed"] is False
    assert result["metadata_only"] is True


def test_skill_cli_verifies_persisted_registry_and_local_package_without_secret_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.context_vault.cli import DefaultRuntime

    @dataclass
    class RegistrySpy:
        skill_id: str | None = None

        def verify_registered_skill(
            self, skill_id: str, **kwargs: Any
        ) -> Mapping[str, Any]:
            self.skill_id = skill_id
            return {
                "skill_id": skill_id,
                "package_hash": kwargs["observed_package_hash"],
                "raw_content": "must-not-leak",
            }

    package = tmp_path / "skill.pkg"
    package.write_bytes(b"trusted package bytes")
    monkeypatch.setenv("CV_SKILL_ALLOWED_TOOLS", "read_file")
    monkeypatch.setenv("CV_SKILL_ALLOWED_PERMISSIONS", "read_project")
    monkeypatch.setenv("CV_SKILL_FILESYSTEM_ROOTS", "/workspace/project")
    request = _json_file(
        tmp_path,
        "skill.json",
        {
            "skill_id": "context-reader",
            "package_path": str(package),
            "adapter_id": "codex",
        },
    )
    spy = RegistrySpy()
    runtime = DefaultRuntime()
    runtime._registry_service = lambda: spy  # type: ignore[method-assign]

    code, output, error = _run(
        ["skill", "verify", "--request", request, "--json"], runtime
    )

    assert (code, error) == (0, "")
    assert spy.skill_id == "context-reader"
    assert (
        json.loads(output)["result"]["package_hash"]
        == hashlib.sha256(package.read_bytes()).hexdigest()
    )
    assert "must-not-leak" not in output


def test_skill_cli_rejects_self_asserted_registry_and_policy(
    tmp_path: Path,
) -> None:
    from src.context_vault.cli import DefaultRuntime

    request = _json_file(
        tmp_path,
        "skill-self-asserted.json",
        {
            "skill_id": "context-reader",
            "package_path": str(tmp_path / "package"),
            "adapter_id": "codex",
            "policy": {"allowed_trust_levels": ["verified"]},
        },
    )
    code, output, _ = _run(
        ["skill", "verify", "--request", request, "--json"], DefaultRuntime()
    )

    assert code == 2
    assert "unknown fields" in json.loads(output)["error"]["detail"]
