"""Fail-closed Work Graph state, lease, fencing and drift invariants."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from .clock import ensure_utc


class WorkGraphError(RuntimeError):
    """Base class for rejected Work Graph operations."""


class InvalidWorkTransition(WorkGraphError):
    pass


class LeaseExpired(WorkGraphError):
    pass


class DriftDetected(WorkGraphError):
    pass


class ApprovalRequired(WorkGraphError):
    pass


class MissingTerminalEvidence(WorkGraphError):
    pass


class WorkStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


TERMINAL_STATUSES = frozenset(
    {
        WorkStatus.COMPLETED,
        WorkStatus.FAILED,
        WorkStatus.CANCELLED,
        WorkStatus.RECOVERY_REQUIRED,
    }
)

_ALLOWED_TRANSITIONS = {
    WorkStatus.DRAFT: {WorkStatus.READY, WorkStatus.CANCELLED},
    WorkStatus.READY: {WorkStatus.CLAIMED, WorkStatus.CANCELLED},
    WorkStatus.CLAIMED: {
        WorkStatus.RUNNING,
        WorkStatus.CANCELLED,
        WorkStatus.RECOVERY_REQUIRED,
    },
    WorkStatus.RUNNING: {
        WorkStatus.BLOCKED,
        WorkStatus.VERIFYING,
        WorkStatus.FAILED,
        WorkStatus.CANCELLED,
        WorkStatus.RECOVERY_REQUIRED,
    },
    WorkStatus.BLOCKED: {
        WorkStatus.READY,
        WorkStatus.FAILED,
        WorkStatus.CANCELLED,
        WorkStatus.RECOVERY_REQUIRED,
    },
    WorkStatus.VERIFYING: {
        WorkStatus.COMPLETED,
        WorkStatus.FAILED,
        WorkStatus.RECOVERY_REQUIRED,
    },
}


def validate_transition(
    previous: WorkStatus | str,
    new: WorkStatus | str,
    *,
    has_terminal_receipt: bool = False,
    has_acceptance_evidence: bool = False,
) -> None:
    """Validate a monotonic transition before any state mutation."""

    previous_status = WorkStatus(previous)
    new_status = WorkStatus(new)
    if new_status not in _ALLOWED_TRANSITIONS.get(previous_status, set()):
        raise InvalidWorkTransition(
            f"invalid work transition: {previous_status} -> {new_status}"
        )
    if new_status is WorkStatus.COMPLETED and not (
        has_terminal_receipt and has_acceptance_evidence
    ):
        raise MissingTerminalEvidence(
            "COMPLETED requires a terminal receipt and acceptance evidence"
        )


def assert_apply_admitted(
    *,
    status: WorkStatus | str,
    claim_status: str,
    claim_expires_at: datetime,
    expected_fencing_token: int,
    presented_fencing_token: int,
    expected_revision: str,
    observed_revision: str,
    now: datetime,
    approval_required: bool = False,
    approval_actor_type: str | None = None,
) -> None:
    """Admit an effect only for the exact live claim and prepared revision."""

    current_status = WorkStatus(status)
    current_time = ensure_utc(now)
    expires_at = ensure_utc(claim_expires_at)
    if current_status is not WorkStatus.CLAIMED:
        raise InvalidWorkTransition("apply requires a CLAIMED work item")
    if claim_status != "active" or expires_at <= current_time:
        raise LeaseExpired("apply requires an active, unexpired lease")
    if presented_fencing_token != expected_fencing_token:
        raise LeaseExpired("stale fencing token cannot create an effect")
    if not expected_revision or observed_revision != expected_revision:
        raise DriftDetected("observed revision differs from the prepared revision")
    if approval_required and approval_actor_type not in {"human", "policy"}:
        raise ApprovalRequired("a model or CLI cannot grant its own approval")
