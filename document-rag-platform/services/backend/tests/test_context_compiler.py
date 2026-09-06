from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from src.context_vault.context_compiler import (
    BudgetExceeded,
    ContextCompiler,
    ContextItem,
    ContextRole,
    DataClassification,
    ItemState,
    LoadTier,
    PolicyViolation,
    ProviderContextPolicy,
)


REMOTE = ProviderContextPolicy(
    provider_id="remote/chat-v1",
    is_remote=True,
    allowed_classifications=frozenset(
        {DataClassification.PUBLIC, DataClassification.INTERNAL}
    ),
)


def _item(
    source_id: str,
    *,
    logical_id: str | None = None,
    version: int = 1,
    tier: LoadTier = LoadTier.RETRIEVE_ON_DEMAND,
    classification: DataClassification = DataClassification.PUBLIC,
    role: ContextRole = ContextRole.KNOWLEDGE,
    state: ItemState = ItemState.ACTIVE,
    tokens: int = 10,
    relevance: float = 0,
    supersedes: tuple[str, ...] = (),
    local_only: bool = False,
) -> ContextItem:
    return ContextItem.from_content(
        source_id=source_id,
        logical_id=logical_id or source_id,
        version=version,
        content="x" * tokens,
        reason=f"test:{source_id}",
        token_cost=tokens,
        load_tier=tier,
        classification=classification,
        role=role,
        state=state,
        relevance_score=relevance,
        supersedes=supersedes,
        local_only=local_only,
    )


def _compile(items, **overrides):
    arguments = {
        "attempt_id": "attempt-001",
        "items": items,
        "provider_policy": REMOTE,
        "context_window": 100,
        "reserved_output": 20,
        "safety_margin": 10,
    }
    arguments.update(overrides)
    return ContextCompiler().compile(**arguments)


def test_tiers_provider_policy_and_budget_produce_bounded_context() -> None:
    items = [
        _item("security", tier=LoadTier.MUST_LOAD, role=ContextRole.SECURITY_POLICY),
        _item("work", tier=LoadTier.MUST_LOAD, role=ContextRole.ACTIVE_WORK),
        _item("relevant", tier=LoadTier.SHOULD_LOAD_IF_RELEVANT, relevance=0.9),
        _item("irrelevant", tier=LoadTier.SHOULD_LOAD_IF_RELEVANT),
        _item("retrieved"),
        _item("not-retrieved"),
        _item("secret", tier=LoadTier.NEVER_AUTO_LOAD),
        _item(
            "local",
            tier=LoadTier.SHOULD_LOAD_IF_RELEVANT,
            relevance=1,
            local_only=True,
        ),
        _item(
            "confidential",
            tier=LoadTier.SHOULD_LOAD_IF_RELEVANT,
            classification=DataClassification.CONFIDENTIAL,
            relevance=1,
        ),
    ]
    compiled = _compile(items, retrieved_source_ids=frozenset({"retrieved"}))

    assert [item.source_id for item in compiled.items] == [
        "security",
        "work",
        "relevant",
        "retrieved",
    ]
    assert compiled.token_cost == 40
    assert compiled.token_cost <= compiled.available_input_tokens == 70
    reasons = {item.source_id: item.reason for item in compiled.excluded}
    assert reasons == {
        "confidential": "provider_classification_policy",
        "irrelevant": "not_relevant",
        "local": "local_only",
        "not-retrieved": "not_retrieved",
        "secret": "never_auto_load",
    }
    assert "content:secret" not in compiled.core_text


def test_full_vault_preload_attempt_is_rejected() -> None:
    with pytest.raises(PolicyViolation, match="full-vault"):
        _compile([_item("anything")], preload_all=True)


def test_never_auto_load_cannot_enter_remote_manifest() -> None:
    secret = _item("raw-transcript", tier=LoadTier.NEVER_AUTO_LOAD)
    compiled = _compile(
        [secret],
        relevant_source_ids=frozenset({secret.source_id}),
        retrieved_source_ids=frozenset({secret.source_id}),
    )

    assert compiled.items == ()
    provider_payload = json.dumps(compiled.canonical_payload())
    assert secret.content_hash not in provider_payload
    assert secret.source_id not in provider_payload
    assert compiled.excluded[0].reason == "never_auto_load"


def test_superseded_stale_and_older_duplicate_are_deterministically_eliminated() -> (
    None
):
    old = _item("decision-v1", logical_id="decision", version=1)
    new = _item(
        "decision-v2",
        logical_id="decision",
        version=2,
        supersedes=(old.source_id,),
    )
    stale = _item("stale", state=ItemState.STALE)
    explicit = _item("superseded", state=ItemState.SUPERSEDED)
    retrieved = frozenset({item.source_id for item in (old, new, stale, explicit)})

    first = _compile([old, new, stale, explicit], retrieved_source_ids=retrieved)
    second = _compile([explicit, stale, new, old], retrieved_source_ids=retrieved)

    assert [item.source_id for item in first.items] == ["decision-v2"]
    assert first.manifest_hash == second.manifest_hash
    assert {item.reason for item in first.excluded} == {"stale", "superseded"}


def test_stale_superseder_cannot_suppress_an_active_revision() -> None:
    active = _item("active", logical_id="decision", version=1)
    stale = _item(
        "stale-new",
        logical_id="decision",
        version=2,
        state=ItemState.STALE,
        supersedes=(active.source_id,),
    )
    compiled = _compile(
        [stale, active],
        retrieved_source_ids=frozenset({active.source_id, stale.source_id}),
    )

    assert [item.source_id for item in compiled.items] == ["active"]


def test_mandatory_security_and_active_work_cannot_be_dropped_under_pressure() -> None:
    mandatory = [
        _item(
            "security",
            tier=LoadTier.MUST_LOAD,
            role=ContextRole.SECURITY_POLICY,
            tokens=40,
        ),
        _item(
            "work",
            tier=LoadTier.MUST_LOAD,
            role=ContextRole.ACTIVE_WORK,
            tokens=40,
        ),
    ]
    with pytest.raises(BudgetExceeded, match="MUST_LOAD"):
        _compile(mandatory)


def test_mandatory_item_blocked_by_remote_policy_fails_closed() -> None:
    active_work = _item(
        "restricted-work",
        tier=LoadTier.MUST_LOAD,
        role=ContextRole.ACTIVE_WORK,
        classification=DataClassification.RESTRICTED,
    )

    with pytest.raises(PolicyViolation, match="MUST_LOAD.*provider"):
        _compile([active_work])


def test_output_is_immutable_and_same_inputs_have_same_core_and_hash() -> None:
    items = [
        _item("security", tier=LoadTier.MUST_LOAD, role=ContextRole.SECURITY_POLICY),
        _item("retrieved"),
    ]
    args = {"retrieved_source_ids": frozenset({"retrieved"})}
    first = _compile(items, **args)
    second = _compile(reversed(items), **args)

    assert first.core_text == second.core_text
    assert first.manifest_hash == second.manifest_hash
    assert len(first.manifest_hash) == 64
    payload = first.canonical_payload()
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == first.manifest_hash
    payload["attempt_id"] = "tampered"
    assert first.canonical_payload()["attempt_id"] == "attempt-001"
    assert payload["items"][0]["content"] == items[0].content
    with pytest.raises(FrozenInstanceError):
        first.token_cost = 999  # type: ignore[misc]


def test_optional_budget_skips_large_item_but_can_include_smaller_item() -> None:
    large = _item(
        "large", tier=LoadTier.SHOULD_LOAD_IF_RELEVANT, tokens=80, relevance=1
    )
    small = _item(
        "small", tier=LoadTier.SHOULD_LOAD_IF_RELEVANT, tokens=10, relevance=0.5
    )
    compiled = _compile([large, small])

    assert [item.source_id for item in compiled.items] == ["small"]
    assert {item.source_id: item.reason for item in compiled.excluded} == {
        "large": "token_budget"
    }


def test_context_item_rejects_tampered_hash_and_unpinned_security() -> None:
    valid = _item("valid")
    with pytest.raises(PolicyViolation, match="content_hash"):
        ContextItem(**{**valid.__dict__, "content_hash": "0" * 64})
    with pytest.raises(PolicyViolation, match="MUST_LOAD"):
        _item("security", role=ContextRole.SECURITY_POLICY)


def test_context_item_rejects_token_cost_underclaim_for_large_utf8_content() -> None:
    content = "é" * 1_200_000
    with pytest.raises(PolicyViolation, match="token_cost"):
        ContextItem.from_content(
            source_id="huge",
            logical_id="huge",
            version=1,
            content=content,
            reason="adversarial underclaim",
            token_cost=1,
            load_tier=LoadTier.MUST_LOAD,
            classification=DataClassification.PUBLIC,
        )


@pytest.mark.parametrize(
    ("role", "state"),
    [
        (ContextRole.SECURITY_POLICY, ItemState.STALE),
        (ContextRole.ACTIVE_WORK, ItemState.SUPERSEDED),
    ],
)
def test_critical_must_load_state_cannot_be_silently_removed(role, state) -> None:
    critical = _item("critical", tier=LoadTier.MUST_LOAD, role=role, state=state)

    with pytest.raises(PolicyViolation, match="critical MUST_LOAD"):
        _compile([critical])


def test_ambiguous_critical_must_load_revisions_fail_closed() -> None:
    revisions = [
        _item(
            "security-v1",
            logical_id="security",
            version=1,
            tier=LoadTier.MUST_LOAD,
            role=ContextRole.SECURITY_POLICY,
        ),
        _item(
            "security-v2",
            logical_id="security",
            version=2,
            tier=LoadTier.MUST_LOAD,
            role=ContextRole.SECURITY_POLICY,
        ),
    ]

    with pytest.raises(PolicyViolation, match="ambiguous critical MUST_LOAD"):
        _compile(revisions)


def test_provider_policy_copies_classifications_and_rejects_type_confusion() -> None:
    allowed = {DataClassification.PUBLIC}
    policy = ProviderContextPolicy(
        provider_id="remote/strict",
        is_remote=True,
        allowed_classifications=allowed,  # type: ignore[arg-type]
    )
    allowed.add(DataClassification.RESTRICTED)

    assert policy.allowed_classifications == frozenset({DataClassification.PUBLIC})
    with pytest.raises(PolicyViolation, match="classification"):
        ProviderContextPolicy(
            provider_id="remote/invalid",
            is_remote=True,
            allowed_classifications={"PUBLIC"},  # type: ignore[arg-type]
        )
    with pytest.raises(PolicyViolation, match="is_remote"):
        ProviderContextPolicy(
            provider_id="remote/invalid",
            is_remote=1,  # type: ignore[arg-type]
            allowed_classifications=frozenset({DataClassification.PUBLIC}),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("context_window", True),
        ("context_window", 100.0),
        ("context_window", float("nan")),
        ("reserved_output", False),
        ("reserved_output", 1.5),
        ("reserved_output", float("nan")),
        ("safety_margin", True),
        ("safety_margin", 1.5),
        ("safety_margin", float("nan")),
    ],
)
def test_compiler_budget_requires_exact_finite_integers(field: str, value) -> None:
    with pytest.raises(PolicyViolation, match="token budget"):
        _compile([], **{field: value})
