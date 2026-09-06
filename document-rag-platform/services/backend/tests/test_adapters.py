from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.context_vault.adapters import (
    AdapterError,
    AdapterHub,
    CodexCliAdapter,
    CoreContextEnvelope,
    WorkAuthority,
    assert_projection_conformance,
)
from src.context_vault.handoff import (
    HandoffRecord,
    HandoffValidationContext,
    HandoffVerifier,
    VerifiedHandoff,
)
from src.context_vault.registry import canonical_hash
from src.context_vault.context_compiler import (
    ContextCompiler,
    ContextItem,
    ContextRole,
    DataClassification,
    LoadTier,
    ProviderContextPolicy,
)


REVISION = "a" * 40
NOW = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)


def core() -> CoreContextEnvelope:
    return CoreContextEnvelope.from_payload(
        {
            "schema_version": "1",
            "work_item": {"id": "work-12", "acceptance": ["three adapters"]},
            "items": [
                {
                    "source_id": "security-policy",
                    "hash": "b" * 64,
                    "reason": "must_load",
                    "token_cost": 9,
                }
            ],
        }
    )


def authority(token: int = 7) -> WorkAuthority:
    return WorkAuthority("work-12", "attempt-3", "claim-9", token, REVISION)


def verified_handoff(envelope: CoreContextEnvelope | None = None):
    envelope = envelope or core()
    record = HandoffRecord(
        work_item_id="work-12",
        attempt_id="attempt-3",
        claim_id="claim-9",
        fencing_token=7,
        objective="Run the adapter",
        exact_scope=("adapter-projection",),
        baseline_revision="c" * 40,
        current_revision=REVISION,
        mutations=(),
        tests=(),
        decision_refs=("decision://protocol/provider-neutral",),
        open_risks=(),
        blockers=(),
        next_allowed_step="Prepare an effect intent",
        context_manifest_hash=envelope.manifest_hash,
        private_artifact_refs=(),
        created_at=NOW,
        claim_expires_at=NOW + timedelta(minutes=10),
    )
    return HandoffVerifier.verify(
        record,
        HandoffValidationContext(
            work_item_id="work-12",
            attempt_id="attempt-3",
            claim_id="claim-9",
            fencing_token=7,
            current_revision=REVISION,
            context_manifest_hash=envelope.manifest_hash,
            now=NOW + timedelta(minutes=1),
        ),
    )


def test_three_cli_adapters_preserve_same_core_manifest() -> None:
    envelope = core()
    hub = AdapterHub.default()
    projections = tuple(
        hub.project(
            adapter_id,
            envelope,
            authority(),
            model_id="qwen2.5-1.5b@local",
            tool_mapping={"read": "read_file"},
        )
        for adapter_id in ("opencode", "claude", "codex")
    )
    assert_projection_conformance(envelope, projections)
    assert {item.core_manifest_hash for item in projections} == {envelope.manifest_hash}
    assert {item.adapter_id for item in projections} == {"opencode", "claude", "codex"}
    assert {item.canonical_authority for item in projections} == {
        "postgresql_work_graph"
    }
    assert not any(item.direct_state_write for item in projections)


def test_missing_opencode_adapter_does_not_break_core_or_other_adapters() -> None:
    envelope = core()
    hub = AdapterHub.default({"opencode": False})
    assert hub.is_available("opencode") is False
    assert hub.is_available("codex") is True
    with pytest.raises(AdapterError) as error:
        hub.project(
            "opencode",
            envelope,
            authority(),
            model_id="qwen@local",
            tool_mapping={},
        )
    assert error.value.code == "ADAPTER_UNAVAILABLE"
    codex = hub.project(
        "codex",
        envelope,
        authority(),
        model_id="qwen@local",
        tool_mapping={},
    )
    assert codex.core_manifest_hash == envelope.manifest_hash


def test_adapter_format_changes_but_semantic_core_does_not() -> None:
    envelope = core()
    hub = AdapterHub.default()
    opencode = hub.project(
        "opencode", envelope, authority(), model_id="model@v1", tool_mapping={}
    )
    claude = hub.project(
        "claude", envelope, authority(), model_id="model@v1", tool_mapping={}
    )
    codex = hub.project(
        "codex", envelope, authority(), model_id="model@v1", tool_mapping={}
    )
    assert "context" in opencode.formatted_payload
    assert "system_context" in claude.formatted_payload
    assert "input_context" in codex.formatted_payload
    assert_projection_conformance(envelope, (opencode, claude, codex))


def test_claim_heartbeat_receipt_protocol_is_projected_without_write_authority() -> (
    None
):
    projection = AdapterHub.default().project(
        "codex",
        core(),
        authority(),
        model_id="model@v1",
        tool_mapping={"heartbeat": "work_heartbeat", "receipt": "submit_receipt"},
    )
    protocol = projection.formatted_payload["protocol"]
    assert protocol == {
        "claim_id": "claim-9",
        "fencing_token": 7,
        "attempt_id": "attempt-3",
        "heartbeat_required": True,
        "receipt_required": True,
        "canonical_state_write": False,
        "effect_mode": "intent_only",
    }
    assert projection.transcript_policy == "optional_private_debug_noncanonical"
    assert len(projection.projection_receipt_hash) == 64


def test_effect_intent_requires_fresh_claim_and_verified_handoff() -> None:
    envelope = core()
    adapter = CodexCliAdapter()
    projection = adapter.project(
        envelope,
        authority(),
        model_id="model@v1",
        tool_mapping={},
    )
    forged = VerifiedHandoff(
        handoff_hash="c" * 64,
        work_item_id="work-12",
        attempt_id="attempt-3",
        claim_id="claim-9",
        fencing_token=7,
        current_revision=REVISION,
        context_manifest_hash=envelope.manifest_hash,
        _attestation=object(),
    )
    with pytest.raises(AdapterError) as unverified:
        adapter.prepare_effect(
            projection,
            current_authority=authority(),
            verified_handoff=forged,
            idempotency_key="apply-1",
        )
    assert unverified.value.code == "UNVERIFIED_HANDOFF"

    with pytest.raises(AdapterError) as stale:
        adapter.prepare_effect(
            projection,
            current_authority=authority(token=8),
            verified_handoff=verified_handoff(envelope),
            idempotency_key="apply-1",
        )
    assert stale.value.code == "STALE_CLAIM_OR_REVISION"


def test_effect_intent_is_non_applying_and_receipt_handoff_bound() -> None:
    envelope = core()
    adapter = CodexCliAdapter()
    projection = adapter.project(
        envelope,
        authority(),
        model_id="model@v1",
        tool_mapping={},
    )
    verified = verified_handoff(envelope)
    hub = AdapterHub.default()
    intent = hub.prepare_effect(
        "codex",
        projection,
        current_authority=authority(),
        verified_handoff=verified,
        idempotency_key="apply-1",
    )
    assert intent.verified_handoff_hash == verified.handoff_hash
    assert intent.core_manifest_hash == envelope.manifest_hash
    assert intent.fencing_token == 7
    assert intent.canonical_effect_applied is False
    assert len(intent.intent_hash) == 64


def test_real_compiler_manifest_flows_unchanged_through_all_adapters() -> None:
    item = ContextItem.from_content(
        source_id="security-policy",
        logical_id="security-policy",
        version=1,
        content="Never let an adapter write canonical state.",
        reason="global authority policy",
        token_cost=43,
        load_tier=LoadTier.MUST_LOAD,
        classification=DataClassification.INTERNAL,
        role=ContextRole.SECURITY_POLICY,
    )
    compiled = ContextCompiler().compile(
        attempt_id="attempt-3",
        items=(item,),
        provider_policy=ProviderContextPolicy(
            provider_id="local-bge",
            is_remote=False,
            allowed_classifications=frozenset({DataClassification.INTERNAL}),
        ),
        context_window=100,
        reserved_output=20,
        safety_margin=10,
    )
    envelope = CoreContextEnvelope.from_compiled_context(compiled)
    hub = AdapterHub.default()
    projections = tuple(
        hub.project(
            adapter_id,
            envelope,
            authority(),
            model_id="qwen2.5-1.5b@local",
            tool_mapping={},
        )
        for adapter_id in ("opencode", "claude", "codex")
    )
    assert envelope.manifest_hash == compiled.manifest_hash
    assert_projection_conformance(envelope, projections)


def test_mutated_or_falsely_claimed_core_manifest_fails_closed() -> None:
    with pytest.raises(AdapterError) as claimed:
        CoreContextEnvelope.from_payload({"safe": True}, claimed_hash="d" * 64)
    assert claimed.value.code == "CORE_MANIFEST_HASH_MISMATCH"

    envelope = core()
    attempted_mutation = envelope.payload
    attempted_mutation["work_item"] = {"id": "changed"}
    projection = AdapterHub.default().project(
        "codex",
        envelope,
        authority(),
        model_id="model@v1",
        tool_mapping={},
    )
    assert projection.formatted_payload["input_context"]["work_item"]["id"] == "work-12"


def test_conformance_rejects_projection_that_claims_direct_write() -> None:
    envelope = core()
    projection = AdapterHub.default().project(
        "codex",
        envelope,
        authority(),
        model_id="model@v1",
        tool_mapping={},
    )
    with pytest.raises(AdapterError) as error:
        assert_projection_conformance(
            envelope,
            (replace(projection, direct_state_write=True),),
        )
    assert error.value.code == "DIRECT_CANONICAL_WRITE"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("canonical_authority", "markdown", "AUTHORITY_DIVERGENCE"),
        ("direct_state_write", True, "DIRECT_CANONICAL_WRITE"),
        ("transcript_policy", "canonical_memory", "TRANSCRIPT_POLICY_DIVERGENCE"),
    ),
)
def test_prepare_effect_revalidates_projection_security_invariants(
    field: str,
    value: str | bool,
    code: str,
) -> None:
    envelope = core()
    adapter = CodexCliAdapter()
    projection = adapter.project(
        envelope,
        authority(),
        model_id="model@v1",
        tool_mapping={},
    )
    with pytest.raises(AdapterError) as error:
        adapter.prepare_effect(
            replace(projection, **{field: value}),
            current_authority=authority(),
            verified_handoff=verified_handoff(envelope),
            idempotency_key="apply-1",
        )
    assert error.value.code == code


def test_projection_receipt_and_admission_bind_security_invariants() -> None:
    envelope = core()
    projection = AdapterHub.default().project(
        "codex",
        envelope,
        authority(),
        model_id="model@v1",
        tool_mapping={},
    )
    mutated = replace(projection, transcript_policy="private_but_authoritative")
    with pytest.raises(AdapterError) as error:
        assert_projection_conformance(envelope, (mutated,))
    assert error.value.code == "TRANSCRIPT_POLICY_DIVERGENCE"

    with pytest.raises(AdapterError) as receipt_error:
        assert_projection_conformance(
            envelope,
            (replace(projection, projection_receipt_hash="0" * 64),),
        )
    assert receipt_error.value.code == "PROJECTION_RECEIPT_MISMATCH"


def test_core_context_rejects_nonfinite_json() -> None:
    with pytest.raises(AdapterError) as error:
        CoreContextEnvelope.from_payload({"score": float("nan")})
    assert error.value.code == "NON_CANONICAL_JSON"


@pytest.mark.parametrize("length", (41, 42, 63))
def test_work_authority_rejects_pseudo_git_revisions(length: int) -> None:
    with pytest.raises(AdapterError) as error:
        WorkAuthority("work-12", "attempt-3", "claim-9", 7, "a" * length)
    assert error.value.code == "INVALID_CURRENT_REVISION"


def _rehash_projection(projection):
    receipt_payload = {
        "adapter_id": projection.adapter_id,
        "adapter_version": projection.adapter_version,
        "canonical_authority": projection.canonical_authority,
        "core_manifest_hash": projection.core_manifest_hash,
        "direct_state_write": projection.direct_state_write,
        "formatted_payload_hash": canonical_hash(projection.formatted_payload),
        "model_id": projection.model_id,
        "tool_mapping": dict(sorted(projection.tool_mapping.items())),
        "transcript_policy": projection.transcript_policy,
        "work_authority": {
            "attempt_id": projection.work_authority.attempt_id,
            "claim_id": projection.work_authority.claim_id,
            "current_revision": projection.work_authority.current_revision,
            "fencing_token": projection.work_authority.fencing_token,
            "work_item_id": projection.work_authority.work_item_id,
        },
    }
    return replace(projection, projection_receipt_hash=canonical_hash(receipt_payload))


def test_prepare_effect_rejects_rehashed_core_context_tamper() -> None:
    envelope = core()
    adapter = CodexCliAdapter()
    projection = adapter.project(
        envelope,
        authority(),
        model_id="model@v1",
        tool_mapping={},
    )
    formatted = dict(projection.formatted_payload)
    formatted["input_context"] = {"attacker": "replacement context"}
    tampered = _rehash_projection(replace(projection, formatted_payload=formatted))
    with pytest.raises(AdapterError) as error:
        adapter.prepare_effect(
            tampered,
            current_authority=authority(),
            verified_handoff=verified_handoff(envelope),
            idempotency_key="apply-1",
        )
    assert error.value.code == "CORE_PAYLOAD_DIVERGENCE"


@pytest.mark.parametrize("alternative_field", ("context", "system_context"))
def test_prepare_effect_rejects_alternative_context_fields_even_when_rehashed(
    alternative_field: str,
) -> None:
    envelope = core()
    adapter = CodexCliAdapter()
    projection = adapter.project(
        envelope,
        authority(),
        model_id="model@v1",
        tool_mapping={},
    )
    formatted = dict(projection.formatted_payload)
    formatted[alternative_field] = envelope.payload
    tampered = _rehash_projection(replace(projection, formatted_payload=formatted))
    with pytest.raises(AdapterError) as error:
        adapter.prepare_effect(
            tampered,
            current_authority=authority(),
            verified_handoff=verified_handoff(envelope),
            idempotency_key="apply-1",
        )
    assert error.value.code == "AMBIGUOUS_CONTEXT_PAYLOAD"
