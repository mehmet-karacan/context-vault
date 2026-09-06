from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.context_vault.handoff import (
    HandoffError,
    HandoffRecord,
    HandoffValidationContext,
    HandoffVerifier,
    MutationEvidence,
    TestEvidence as HandoffTestEvidence,
)


NOW = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
REVISION = "a" * 40
MANIFEST_HASH = "b" * 64


def handoff() -> HandoffRecord:
    return HandoffRecord(
        work_item_id="work-12",
        attempt_id="attempt-3",
        claim_id="claim-9",
        fencing_token=7,
        objective="Implement the provider-neutral adapter boundary",
        exact_scope=("src/context_vault/adapters.py",),
        baseline_revision="c" * 40,
        current_revision=REVISION,
        mutations=(
            MutationEvidence(
                "Added adapter projections", "receipt://effects/mutation-1"
            ),
        ),
        tests=(
            HandoffTestEvidence(
                "adapter-conformance",
                "passed",
                "receipt://tests/adapter-conformance-1",
            ),
        ),
        decision_refs=("decision://adr/provider-neutral-core",),
        open_risks=("CLI binary not installed on this host",),
        blockers=(),
        next_allowed_step="Integrate the adapter projection with the CLI entrypoint",
        context_manifest_hash=MANIFEST_HASH,
        private_artifact_refs=("private-artifact://debug/attempt-3",),
        created_at=NOW,
        claim_expires_at=NOW + timedelta(minutes=10),
    )


def context(**overrides) -> HandoffValidationContext:
    values = {
        "work_item_id": "work-12",
        "attempt_id": "attempt-3",
        "claim_id": "claim-9",
        "fencing_token": 7,
        "current_revision": REVISION,
        "context_manifest_hash": MANIFEST_HASH,
        "now": NOW + timedelta(minutes=1),
    }
    values.update(overrides)
    return HandoffValidationContext(**values)


def test_handoff_is_structured_hashed_and_current_claim_bound() -> None:
    record = handoff()
    verified = HandoffVerifier.verify(record, context())
    assert verified.is_verified is True
    assert verified.handoff_hash == record.handoff_hash
    assert verified.fencing_token == 7
    assert record.canonical_payload()["mutations"][0]["receipt_ref"].startswith(
        "receipt://"
    )
    assert "conversation" not in record.canonical_payload()


@pytest.mark.parametrize(
    ("field", "value", "stale_name"),
    [
        ("claim_id", "claim-10", "claim_id"),
        ("fencing_token", 8, "fencing_token"),
        ("current_revision", "d" * 40, "current_revision"),
        ("context_manifest_hash", "e" * 64, "context_manifest_hash"),
        ("attempt_id", "attempt-4", "attempt_id"),
    ],
)
def test_stale_handoff_requires_new_prepare(
    field: str,
    value: str | int,
    stale_name: str,
) -> None:
    with pytest.raises(HandoffError) as error:
        HandoffVerifier.verify(handoff(), context(**{field: value}))
    assert error.value.code == "HANDOFF_STALE_PREPARE_REQUIRED"
    assert stale_name in error.value.detail


def test_expired_claim_makes_handoff_stale() -> None:
    with pytest.raises(HandoffError) as error:
        HandoffVerifier.verify(
            handoff(),
            context(now=NOW + timedelta(minutes=10)),
        )
    assert error.value.code == "HANDOFF_STALE_PREPARE_REQUIRED"
    assert error.value.detail == "claim_expires_at"


def test_future_created_handoff_fails_closed() -> None:
    with pytest.raises(HandoffError) as error:
        HandoffVerifier.verify(
            handoff(),
            context(now=NOW - timedelta(seconds=1)),
        )
    assert error.value.code == "HANDOFF_STALE_PREPARE_REQUIRED"
    assert error.value.detail == "created_at"


def test_handoff_requires_receipt_and_decision_references() -> None:
    with pytest.raises(HandoffError) as mutation_error:
        MutationEvidence("changed state", "chat://last-conversation")
    assert mutation_error.value.code == "INVALID_HANDOFF_REF"

    with pytest.raises(HandoffError) as decision_error:
        replace(handoff(), decision_refs=("last conversation said yes",))
    assert decision_error.value.code == "INVALID_HANDOFF_REF"


def test_private_artifacts_must_be_explicit_noncanonical_refs() -> None:
    with pytest.raises(HandoffError) as error:
        replace(handoff(), private_artifact_refs=("/tmp/raw-transcript.log",))
    assert error.value.code == "INVALID_HANDOFF_REF"


def test_handoff_hash_changes_with_receipt_bound_evidence() -> None:
    original = handoff()
    changed = replace(
        original,
        mutations=(
            MutationEvidence(
                "Added adapter projections", "receipt://effects/mutation-2"
            ),
        ),
    )
    assert original.handoff_hash != changed.handoff_hash


@pytest.mark.parametrize("length", (41, 42, 63))
def test_handoff_rejects_pseudo_git_revisions(length: int) -> None:
    with pytest.raises(HandoffError) as error:
        replace(handoff(), current_revision="a" * length)
    assert error.value.code == "INVALID_REVISION"
