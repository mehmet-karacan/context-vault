import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.application.work_graph import EffectCapability, EffectRequest, WorkGraphService
from src.domain.work_graph import (
    ApprovalRequired,
    DriftDetected,
    InvalidWorkTransition,
    LeaseExpired,
    MissingTerminalEvidence,
    WorkStatus,
    assert_apply_admitted,
    validate_transition,
)


NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def test_running_requires_current_fencing_token_and_live_lease() -> None:
    assert_apply_admitted(
        status=WorkStatus.CLAIMED,
        claim_status="active",
        claim_expires_at=NOW + timedelta(seconds=30),
        expected_fencing_token=8,
        presented_fencing_token=8,
        expected_revision="abc",
        observed_revision="abc",
        now=NOW,
    )
    with pytest.raises(LeaseExpired):
        assert_apply_admitted(
            status=WorkStatus.CLAIMED,
            claim_status="active",
            claim_expires_at=NOW,
            expected_fencing_token=8,
            presented_fencing_token=8,
            expected_revision="abc",
            observed_revision="abc",
            now=NOW,
        )
    with pytest.raises(LeaseExpired, match="fencing"):
        assert_apply_admitted(
            status=WorkStatus.CLAIMED,
            claim_status="active",
            claim_expires_at=NOW + timedelta(seconds=30),
            expected_fencing_token=8,
            presented_fencing_token=7,
            expected_revision="abc",
            observed_revision="abc",
            now=NOW,
        )


def test_revision_drift_is_fail_closed() -> None:
    with pytest.raises(DriftDetected):
        assert_apply_admitted(
            status=WorkStatus.CLAIMED,
            claim_status="active",
            claim_expires_at=NOW + timedelta(seconds=30),
            expected_fencing_token=1,
            presented_fencing_token=1,
            expected_revision="before",
            observed_revision="after",
            now=NOW,
        )


def test_completion_requires_terminal_receipt_and_acceptance_evidence() -> None:
    with pytest.raises(MissingTerminalEvidence):
        validate_transition(
            WorkStatus.VERIFYING,
            WorkStatus.COMPLETED,
            has_terminal_receipt=False,
            has_acceptance_evidence=True,
        )
    with pytest.raises(MissingTerminalEvidence):
        validate_transition(
            WorkStatus.VERIFYING,
            WorkStatus.COMPLETED,
            has_terminal_receipt=True,
            has_acceptance_evidence=False,
        )
    validate_transition(
        WorkStatus.VERIFYING,
        WorkStatus.COMPLETED,
        has_terminal_receipt=True,
        has_acceptance_evidence=True,
    )


def test_state_machine_is_monotonic_and_approval_cannot_be_self_granted() -> None:
    with pytest.raises(InvalidWorkTransition):
        validate_transition(WorkStatus.COMPLETED, WorkStatus.RUNNING)
    with pytest.raises(ApprovalRequired):
        assert_apply_admitted(
            status=WorkStatus.CLAIMED,
            claim_status="active",
            claim_expires_at=NOW + timedelta(seconds=30),
            expected_fencing_token=1,
            presented_fencing_token=1,
            expected_revision="same",
            observed_revision="same",
            now=NOW,
            approval_required=True,
            approval_actor_type="model",
        )


def test_invalid_receipt_signer_is_rejected_before_database_or_effect() -> None:
    service = WorkGraphService(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="signer"):
        service.apply(
            uuid.uuid4(),
            claim_id=uuid.uuid4(),
            fencing_token=1,
            observed_revision="rev",
            idempotency_key="apply",
            command="mutation",
            tool="fixture",
            action="write",
            signer_type="model",
            signer_id="self-authorizing-model",
            effect_request=EffectRequest(
                capability=EffectCapability.WRITE_RECEIPT_MARKER,
                relative_path="evidence/marker.json",
                content=b"must-not-run",
            ),
        )
