"""Structured, revision-bound handoff verification.

A handoff is evidence for continuity, not an authority source.  Verification binds
it to the current PostgreSQL work claim, source revision, and compiled context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .registry import canonical_hash


_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}")
_VERIFICATION_ATTESTATION = object()


class HandoffError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _require_ref(value: str, scheme: str, field_name: str) -> None:
    prefix = f"{scheme}://"
    if not value.startswith(prefix) or not _REF.fullmatch(value[len(prefix) :]):
        raise HandoffError("INVALID_HANDOFF_REF", f"{field_name} must use {prefix}")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise HandoffError("INVALID_HANDOFF_TIME", f"{field_name} must be UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MutationEvidence:
    summary: str
    receipt_ref: str

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise HandoffError("INVALID_MUTATION_EVIDENCE", "summary is required")
        _require_ref(self.receipt_ref, "receipt", "receipt_ref")


@dataclass(frozen=True)
class TestEvidence:
    test_id: str
    status: str
    receipt_ref: str

    def __post_init__(self) -> None:
        if not self.test_id.strip() or self.status not in {"passed", "failed"}:
            raise HandoffError("INVALID_TEST_EVIDENCE", self.test_id)
        _require_ref(self.receipt_ref, "receipt", "receipt_ref")


@dataclass(frozen=True)
class HandoffRecord:
    work_item_id: str
    attempt_id: str
    claim_id: str
    fencing_token: int
    objective: str
    exact_scope: tuple[str, ...]
    baseline_revision: str
    current_revision: str
    mutations: tuple[MutationEvidence, ...]
    tests: tuple[TestEvidence, ...]
    decision_refs: tuple[str, ...]
    open_risks: tuple[str, ...]
    blockers: tuple[str, ...]
    next_allowed_step: str
    context_manifest_hash: str
    private_artifact_refs: tuple[str, ...]
    created_at: datetime
    claim_expires_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "exact_scope",
            "mutations",
            "tests",
            "decision_refs",
            "open_risks",
            "blockers",
            "private_artifact_refs",
        ):
            value = getattr(self, field_name)
            if isinstance(value, (str, bytes)):
                raise HandoffError(
                    "INVALID_HANDOFF_COLLECTION",
                    f"{field_name} must be a collection",
                )
            try:
                object.__setattr__(self, field_name, tuple(value))
            except TypeError as exc:
                raise HandoffError(
                    "INVALID_HANDOFF_COLLECTION",
                    f"{field_name} must be iterable",
                ) from exc
        for value, name in (
            (self.work_item_id, "work_item_id"),
            (self.attempt_id, "attempt_id"),
            (self.claim_id, "claim_id"),
        ):
            if not _REF.fullmatch(value):
                raise HandoffError("INVALID_HANDOFF_ID", name)
        if (
            not isinstance(self.fencing_token, int)
            or isinstance(self.fencing_token, bool)
            or self.fencing_token <= 0
        ):
            raise HandoffError("INVALID_FENCING_TOKEN", str(self.fencing_token))
        if not self.objective.strip() or not self.exact_scope:
            raise HandoffError("INCOMPLETE_HANDOFF_SCOPE", self.work_item_id)
        if not _REVISION.fullmatch(self.baseline_revision):
            raise HandoffError("INVALID_REVISION", "baseline_revision")
        if not _REVISION.fullmatch(self.current_revision):
            raise HandoffError("INVALID_REVISION", "current_revision")
        if not _SHA256.fullmatch(self.context_manifest_hash):
            raise HandoffError("INVALID_MANIFEST_HASH", self.context_manifest_hash)
        if not self.next_allowed_step.strip():
            raise HandoffError("MISSING_NEXT_STEP", self.work_item_id)
        for ref in self.decision_refs:
            _require_ref(ref, "decision", "decision_ref")
        for ref in self.private_artifact_refs:
            _require_ref(ref, "private-artifact", "private_artifact_ref")
        created_at = _utc(self.created_at, "created_at")
        expires_at = _utc(self.claim_expires_at, "claim_expires_at")
        if expires_at <= created_at:
            raise HandoffError("INVALID_HANDOFF_TIME", "claim expires before creation")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "baseline_revision": self.baseline_revision,
            "blockers": list(self.blockers),
            "claim_expires_at": self.claim_expires_at.astimezone(
                timezone.utc
            ).isoformat(),
            "claim_id": self.claim_id,
            "context_manifest_hash": self.context_manifest_hash,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "current_revision": self.current_revision,
            "decision_refs": list(self.decision_refs),
            "exact_scope": list(self.exact_scope),
            "fencing_token": self.fencing_token,
            "mutations": [
                {"receipt_ref": item.receipt_ref, "summary": item.summary}
                for item in self.mutations
            ],
            "next_allowed_step": self.next_allowed_step,
            "objective": self.objective,
            "open_risks": list(self.open_risks),
            "private_artifact_refs": list(self.private_artifact_refs),
            "tests": [
                {
                    "receipt_ref": item.receipt_ref,
                    "status": item.status,
                    "test_id": item.test_id,
                }
                for item in self.tests
            ],
            "work_item_id": self.work_item_id,
        }

    @property
    def handoff_hash(self) -> str:
        return canonical_hash(self.canonical_payload())


@dataclass(frozen=True)
class HandoffValidationContext:
    work_item_id: str
    attempt_id: str
    claim_id: str
    fencing_token: int
    current_revision: str
    context_manifest_hash: str
    now: datetime


@dataclass(frozen=True)
class VerifiedHandoff:
    handoff_hash: str
    work_item_id: str
    attempt_id: str
    claim_id: str
    fencing_token: int
    current_revision: str
    context_manifest_hash: str
    _attestation: object

    @property
    def is_verified(self) -> bool:
        return self._attestation is _VERIFICATION_ATTESTATION


class HandoffVerifier:
    """Returns an attested value only when every mutable binding is current."""

    @staticmethod
    def verify(
        record: HandoffRecord,
        context: HandoffValidationContext,
    ) -> VerifiedHandoff:
        now = _utc(context.now, "now")
        bindings = {
            "work_item_id": (record.work_item_id, context.work_item_id),
            "attempt_id": (record.attempt_id, context.attempt_id),
            "claim_id": (record.claim_id, context.claim_id),
            "fencing_token": (record.fencing_token, context.fencing_token),
            "current_revision": (record.current_revision, context.current_revision),
            "context_manifest_hash": (
                record.context_manifest_hash,
                context.context_manifest_hash,
            ),
        }
        stale = sorted(
            name for name, values in bindings.items() if values[0] != values[1]
        )
        if now < record.created_at:
            stale.append("created_at")
        if now >= record.claim_expires_at:
            stale.append("claim_expires_at")
        if stale:
            raise HandoffError(
                "HANDOFF_STALE_PREPARE_REQUIRED",
                ",".join(stale),
            )
        return VerifiedHandoff(
            handoff_hash=record.handoff_hash,
            work_item_id=record.work_item_id,
            attempt_id=record.attempt_id,
            claim_id=record.claim_id,
            fencing_token=record.fencing_token,
            current_revision=record.current_revision,
            context_manifest_hash=record.context_manifest_hash,
            _attestation=_VERIFICATION_ATTESTATION,
        )
