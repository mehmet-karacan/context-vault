"""Transactional PostgreSQL Work Graph orchestration.

Claims are committed before the typed, scope-bound dispatcher can create an effect.
Every outcome is bound to one canonical, request-digested receipt; a crash leaves a
stale lease for explicit reconciliation and never marks work complete.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..domain.clock import Clock, SYSTEM_CLOCK, ensure_utc
from ..domain.work_graph import (
    ApprovalRequired,
    DriftDetected,
    InvalidWorkTransition,
    LeaseExpired,
    WorkGraphError,
    WorkStatus,
    assert_apply_admitted,
    validate_transition,
)
from ..models import (
    ContextSource,
    Principal,
    Project,
    WorkApproval,
    WorkAttempt,
    WorkClaim,
    WorkEvent,
    WorkItem,
    WorkReceipt,
    WorkspaceMembership,
)


class WorkNotFound(WorkGraphError):
    pass


class ScopeViolation(WorkGraphError):
    pass


class WorkEffectFailed(WorkGraphError):
    pass


class EffectAlreadyStarted(WorkGraphError):
    pass


class IdempotencyConflict(WorkGraphError):
    pass


class EffectCapability(StrEnum):
    WRITE_RECEIPT_MARKER = "write_receipt_marker"


@dataclass(frozen=True)
class EffectRequest:
    capability: EffectCapability
    relative_path: str
    content: bytes

    def digest_payload(self) -> dict[str, str]:
        return {
            "capability": self.capability.value,
            "relative_path": self.relative_path,
            "content_hash": hashlib.sha256(self.content).hexdigest(),
        }


@dataclass(frozen=True)
class ScopedEffectContext:
    root: Path
    allowed_paths: tuple[str, ...]
    capabilities: tuple[EffectCapability, ...]


@dataclass(frozen=True)
class PreparedWork:
    work_item_id: uuid.UUID
    work_item_revision: int
    expected_revision: str
    drift_token: str
    scope_hash: str


@dataclass(frozen=True)
class ClaimResult:
    claim_id: uuid.UUID
    attempt_id: uuid.UUID
    fencing_token: int
    expires_at: datetime


@dataclass(frozen=True)
class EffectResult:
    before_artifact_hash: str
    output_artifact_hash: str
    after_revision: str
    test_evidence_refs: tuple[str, ...] = ()
    rollback_result: dict[str, Any] | None = None
    attestation: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApplyResult:
    receipt_id: uuid.UUID
    attempt_id: uuid.UUID
    replayed: bool
    exit_status: int
    output_artifact_hash: str | None
    after_revision: str


@dataclass(frozen=True)
class VerifiedWork:
    receipt_id: uuid.UUID
    attempt_id: uuid.UUID
    evidence_hash: str
    observed_revision: str
    replayed: bool


class ScopedMutationDispatcher:
    """Create-only, path-scoped marker mutation with exact-hash rollback."""

    ABSENT_HASH = hashlib.sha256(b"context-vault:absent-v1").hexdigest()

    def __init__(self, root: Path) -> None:
        raw = root.absolute()
        if raw.is_symlink() or not raw.is_dir():
            raise ScopeViolation(
                "effect root must be an existing non-symlink directory"
            )
        metadata = raw.stat()
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
            raise ScopeViolation("effect root must be owner-only writable")
        self.root = raw.resolve(strict=True)

    def dispatch(
        self, context: ScopedEffectContext, request: EffectRequest
    ) -> EffectResult:
        target = self._admit(context, request)
        if target.exists() or target.is_symlink():
            raise ScopeViolation("create-only mutation target already exists")
        parent_fd = self._open_parent(target)
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(target.name, flags, 0o600, dir_fd=parent_fd)
            try:
                view = memoryview(request.content)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(parent_fd)
        except FileExistsError as exc:
            raise ScopeViolation("create-only mutation target already exists") from exc
        finally:
            os.close(parent_fd)
        output_hash = hashlib.sha256(request.content).hexdigest()
        return EffectResult(
            before_artifact_hash=self.ABSENT_HASH,
            output_artifact_hash=output_hash,
            after_revision=output_hash,
            attestation={
                "capability": request.capability.value,
                "relative_path": request.relative_path,
            },
        )

    def validate(self, context: ScopedEffectContext, request: EffectRequest) -> None:
        target = self._admit(context, request)
        if target.exists() or target.is_symlink():
            raise ScopeViolation("create-only mutation target already exists")

    def rollback(
        self,
        context: ScopedEffectContext,
        request: EffectRequest,
        *,
        expected_after_hash: str,
    ) -> EffectResult:
        target = self._admit(context, request)
        parent_fd = self._open_parent(target)
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(target.name, flags, dir_fd=parent_fd)
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ScopeViolation("rollback target must be a regular file")
                digest = hashlib.sha256()
                while chunk := os.read(fd, 64 * 1024):
                    digest.update(chunk)
            finally:
                os.close(fd)
            if digest.hexdigest() != expected_after_hash:
                raise ScopeViolation("rollback target hash differs from apply receipt")
            os.unlink(target.name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileNotFoundError as exc:
            raise ScopeViolation("rollback target is absent") from exc
        finally:
            os.close(parent_fd)
        return EffectResult(
            before_artifact_hash=expected_after_hash,
            output_artifact_hash=self.ABSENT_HASH,
            after_revision=self.ABSENT_HASH,
            rollback_result={"status": "completed", "action": "unlink"},
            attestation={
                "capability": request.capability.value,
                "relative_path": request.relative_path,
            },
        )

    def _admit(self, context: ScopedEffectContext, request: EffectRequest) -> Path:
        if context.root.resolve(strict=True) != self.root:
            raise ScopeViolation("effect context root differs from dispatcher root")
        if request.capability not in context.capabilities:
            raise ScopeViolation("effect capability is outside the claim scope")
        path = PurePosixPath(request.relative_path)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ScopeViolation("effect path escapes the scoped root")
        normalized = path.as_posix()
        if normalized not in context.allowed_paths:
            raise ScopeViolation("effect path is outside the claim allowlist")
        target = self.root.joinpath(*path.parts)
        current = self.root
        for part in path.parts[:-1]:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError as exc:
                raise ScopeViolation("effect parent directory is absent") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ScopeViolation("effect parent directory cannot be a symlink")
            if not stat.S_ISDIR(metadata.st_mode):
                raise ScopeViolation("effect parent must be a directory")
        return target

    def _open_parent(self, target: Path) -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        root_fd: int | None = None
        current_fd: int | None = None
        try:
            root_fd = os.open(self.root, flags)
            current_fd = root_fd
            relative_parent = target.parent.relative_to(self.root)
            for part in relative_parent.parts:
                next_fd = os.open(part, flags, dir_fd=current_fd)
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            if current_fd == root_fd:
                root_fd = None
            else:
                os.close(root_fd)
                root_fd = None
            return current_fd
        except OSError as exc:
            if current_fd is not None and current_fd != root_fd:
                os.close(current_fd)
            raise ScopeViolation("effect parent admission failed") from exc
        finally:
            if root_fd is not None:
                os.close(root_fd)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class WorkGraphService:
    def __init__(
        self,
        db: Session,
        clock: Clock = SYSTEM_CLOCK,
        dispatcher: ScopedMutationDispatcher | None = None,
        *,
        authenticated_principal_id: uuid.UUID | None = None,
        trusted_policy_ids: frozenset[str] | None = None,
    ) -> None:
        self.db = db
        self.clock = clock
        self.dispatcher = dispatcher
        self.authenticated_principal_id = authenticated_principal_id
        self.trusted_policy_ids = trusted_policy_ids or frozenset()

    def create_work_item(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None,
        title: str,
        objective: str,
        scope: dict[str, Any],
        expected_revision: str,
        created_by_principal_id: uuid.UUID,
        acceptance_criteria: list[dict[str, Any]],
        evidence_requirements: list[str],
        exclusions: list[str] | None = None,
        required_approvals: list[str] | None = None,
        risk_class: str = "low",
        priority: int = 0,
        owner_principal_id: uuid.UUID | None = None,
        parent_id: uuid.UUID | None = None,
    ) -> WorkItem:
        if not title.strip() or not objective.strip() or not expected_revision.strip():
            raise ValueError("title, objective and expected_revision are required")
        if not acceptance_criteria or not evidence_requirements:
            raise ValueError(
                "acceptance criteria and evidence requirements are required"
            )
        item = WorkItem(
            workspace_id=workspace_id,
            project_id=project_id,
            title=title.strip(),
            objective=objective.strip(),
            scope_json=scope,
            exclusions_json=exclusions or [],
            priority=priority,
            risk_class=risk_class,
            required_approvals=required_approvals or [],
            expected_revision=expected_revision,
            created_by_principal_id=created_by_principal_id,
            owner_principal_id=owner_principal_id,
            parent_id=parent_id,
            acceptance_criteria=acceptance_criteria,
            evidence_requirements=evidence_requirements,
        )
        self.db.add(item)
        self.db.flush()
        self._append_event(
            item,
            event_type="work.created",
            actor_type="human",
            actor_id=str(created_by_principal_id),
            reason="authoritative work item created",
            previous_state=None,
            new_state=WorkStatus.DRAFT.value,
        )
        self.db.commit()
        return item

    def mark_ready(self, work_item_id: uuid.UUID, *, actor_id: str) -> WorkItem:
        item = self._locked_item(work_item_id)
        self._transition(item, WorkStatus.READY, actor_id=actor_id, reason="prepared")
        self.db.commit()
        return item

    def prepare(
        self, work_item_id: uuid.UUID, *, observed_revision: str
    ) -> PreparedWork:
        """Perform a read-only prepare; no row or external resource is changed."""

        item = self.db.get(WorkItem, work_item_id)
        if item is None:
            raise WorkNotFound(str(work_item_id))
        if WorkStatus(item.status) not in {WorkStatus.READY, WorkStatus.CLAIMED}:
            raise InvalidWorkTransition("prepare requires READY or CLAIMED work")
        if observed_revision != item.expected_revision:
            raise DriftDetected("prepare observed a different baseline revision")
        scope_hash = canonical_hash(item.scope_json)
        drift_token = canonical_hash(
            {
                "work_item_id": str(item.id),
                "work_item_revision": item.revision,
                "expected_revision": item.expected_revision,
                "scope_hash": scope_hash,
            }
        )
        return PreparedWork(
            work_item_id=item.id,
            work_item_revision=item.revision,
            expected_revision=item.expected_revision,
            drift_token=drift_token,
            scope_hash=scope_hash,
        )

    def claim(
        self,
        work_item_id: uuid.UUID,
        *,
        prepared: PreparedWork,
        claimant_id: str,
        executor_id: str,
        resource_scope: dict[str, Any],
        lease_duration: timedelta,
        idempotency_key: str,
        adapter_id: str | None = None,
        model_id: str | None = None,
        provider_id: str | None = None,
        input_context_manifest_hash: str | None = None,
    ) -> ClaimResult:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        item = self._locked_item(work_item_id)
        now = ensure_utc(self.clock.now())
        claim_request_hash = canonical_hash(
            {
                "work_item_id": str(work_item_id),
                "prepared": {
                    "work_item_id": str(prepared.work_item_id),
                    "work_item_revision": prepared.work_item_revision,
                    "expected_revision": prepared.expected_revision,
                    "drift_token": prepared.drift_token,
                    "scope_hash": prepared.scope_hash,
                },
                "claimant_id": claimant_id,
                "executor_id": executor_id,
                "resource_scope": resource_scope,
                "lease_duration_microseconds": lease_duration
                // timedelta(microseconds=1),
                "idempotency_key": idempotency_key,
                "adapter_id": adapter_id,
                "model_id": model_id,
                "provider_id": provider_id,
                "input_context_manifest_hash": input_context_manifest_hash,
            }
        )

        existing_attempt = (
            self.db.query(WorkAttempt)
            .filter_by(work_item_id=item.id, idempotency_key=idempotency_key)
            .one_or_none()
        )
        if existing_attempt is not None:
            if existing_attempt.claim_request_hash != claim_request_hash:
                raise IdempotencyConflict(
                    "claim idempotency key was bound to a different exact request"
                )
            existing_claim = (
                self.db.query(WorkClaim)
                .filter_by(attempt_id=existing_attempt.id)
                .one_or_none()
            )
            if existing_claim is None:
                raise WorkGraphError("idempotent attempt exists without a claim")
            if (
                existing_claim.status != "active"
                or ensure_utc(existing_claim.expires_at) <= now
                or item.active_attempt_id != existing_attempt.id
                or item.active_fencing_token != existing_claim.fencing_token
            ):
                raise LeaseExpired(
                    "idempotent claim replay no longer has current lease authority"
                )
            self._append_observation(
                item,
                attempt_id=existing_attempt.id,
                event_type="claim.replayed",
                actor_id=claimant_id,
                reason="idempotency key resolved to exact active claim",
            )
            self.db.commit()
            return ClaimResult(
                claim_id=existing_claim.id,
                attempt_id=existing_attempt.id,
                fencing_token=existing_claim.fencing_token,
                expires_at=existing_claim.expires_at,
            )

        self._validate_prepared(item, prepared)
        if canonical_hash(resource_scope) != prepared.scope_hash:
            raise ScopeViolation("claim resource scope differs from prepared scope")

        active = (
            self.db.query(WorkClaim)
            .filter_by(work_item_id=item.id, status="active")
            .with_for_update()
            .one_or_none()
        )
        if active is not None and ensure_utc(active.expires_at) > now:
            raise LeaseExpired("work item already has an active lease")
        if active is not None:
            active.status = "reconciled"
            active.reconciled_reason = "expired_before_effect"
            active.released_at = now
            old_attempt = self.db.get(WorkAttempt, active.attempt_id)
            if old_attempt is not None:
                old_attempt.status = "RECOVERY_REQUIRED"
                old_attempt.finished_at = now
                old_attempt.terminal_reason = "lease_expired_before_effect"

        if WorkStatus(item.status) not in {WorkStatus.READY, WorkStatus.CLAIMED}:
            raise InvalidWorkTransition(
                "claim requires READY work or stale CLAIMED work"
            )
        attempt_no = (
            self.db.query(func.coalesce(func.max(WorkAttempt.attempt_no), 0))
            .filter(WorkAttempt.work_item_id == item.id)
            .scalar()
            + 1
        )
        attempt = WorkAttempt(
            work_item_id=item.id,
            attempt_no=attempt_no,
            executor_id=executor_id,
            adapter_id=adapter_id,
            model_id=model_id,
            provider_id=provider_id,
            status="CLAIMED",
            started_at=now,
            input_context_manifest_hash=input_context_manifest_hash,
            expected_revision=prepared.expected_revision,
            drift_token=prepared.drift_token,
            idempotency_key=idempotency_key,
            claim_request_hash=claim_request_hash,
        )
        self.db.add(attempt)
        self.db.flush()
        item.next_fencing_token += 1
        item.active_fencing_token = item.next_fencing_token
        item.active_attempt_id = attempt.id
        claim = WorkClaim(
            work_item_id=item.id,
            attempt_id=attempt.id,
            resource_scope=resource_scope,
            scope_hash=prepared.scope_hash,
            claimant_id=claimant_id,
            acquired_at=now,
            expires_at=now + lease_duration,
            heartbeat_at=now,
            fencing_token=item.active_fencing_token,
            status="active",
        )
        self.db.add(claim)
        self.db.flush()
        if WorkStatus(item.status) is WorkStatus.READY:
            self._transition(
                item,
                WorkStatus.CLAIMED,
                actor_id=claimant_id,
                reason="lease acquired",
                attempt_id=attempt.id,
            )
        else:
            self._append_observation(
                item,
                attempt_id=attempt.id,
                event_type="claim.reacquired",
                actor_id=claimant_id,
                reason="stale pre-effect claim replaced",
            )
        self.db.commit()
        return ClaimResult(
            claim_id=claim.id,
            attempt_id=attempt.id,
            fencing_token=claim.fencing_token,
            expires_at=claim.expires_at,
        )

    def heartbeat(
        self,
        claim_id: uuid.UUID,
        *,
        fencing_token: int,
        extend_by: timedelta,
    ) -> datetime:
        if extend_by <= timedelta(0):
            raise ValueError("extend_by must be positive")
        claim = (
            self.db.query(WorkClaim)
            .filter(WorkClaim.id == claim_id)
            .with_for_update()
            .one_or_none()
        )
        if claim is None:
            raise WorkNotFound(str(claim_id))
        now = ensure_utc(self.clock.now())
        if (
            claim.status != "active"
            or claim.fencing_token != fencing_token
            or ensure_utc(claim.expires_at) <= now
        ):
            raise LeaseExpired("heartbeat rejected for stale claim or fencing token")
        claim.heartbeat_at = now
        claim.expires_at = now + extend_by
        self.db.commit()
        return claim.expires_at

    def grant_approval(
        self,
        work_item_id: uuid.UUID,
        *,
        approval_type: str,
        scope_hash: str,
        actor_type: str,
        actor_id: str,
        reason: str,
        evidence_refs: list[str],
        expires_at: datetime | None = None,
    ) -> WorkApproval:
        if actor_type not in {"human", "policy"}:
            raise ApprovalRequired("a model or CLI cannot grant approval")
        item = self._locked_item(work_item_id)
        if approval_type not in set(item.required_approvals or []):
            raise ApprovalRequired("approval type is not required by this work item")
        if scope_hash != canonical_hash(item.scope_json):
            raise ApprovalRequired("approval scope does not match authoritative work")
        if (
            not reason.strip()
            or not evidence_refs
            or not all(isinstance(ref, str) and ref.strip() for ref in evidence_refs)
        ):
            raise ApprovalRequired("approval requires a reason and evidence references")
        now = ensure_utc(self.clock.now())
        if expires_at is not None and ensure_utc(expires_at) <= now:
            raise ApprovalRequired("approval expiry must be in the future")
        self._assert_trusted_approval_actor(
            item,
            approval_type=approval_type,
            actor_type=actor_type,
            actor_id=actor_id,
        )
        approval = WorkApproval(
            work_item_id=item.id,
            approval_type=approval_type,
            scope_hash=scope_hash,
            decision="approved",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            evidence_refs=evidence_refs,
            granted_at=now,
            expires_at=expires_at,
        )
        self.db.add(approval)
        self.db.commit()
        return approval

    def apply(
        self,
        work_item_id: uuid.UUID,
        *,
        claim_id: uuid.UUID,
        fencing_token: int,
        observed_revision: str,
        idempotency_key: str,
        command: str,
        tool: str,
        action: str,
        signer_type: str,
        signer_id: str,
        effect_request: EffectRequest,
    ) -> ApplyResult:
        self._validate_signer(signer_type, signer_id)
        item, claim, attempt = self._locked_apply_rows(work_item_id, claim_id)
        context = self._scoped_effect_context(item, claim)
        request_binding = {
            "work_item_id": str(item.id),
            "attempt_id": str(attempt.id),
            "claim_id": str(claim.id),
            "scope_hash": claim.scope_hash,
            "fencing_token": fencing_token,
            "before_revision": attempt.expected_revision,
            "observed_revision": observed_revision,
            "idempotency_key": idempotency_key,
            "command": command,
            "tool": tool,
            "action": action,
            "signer_type": signer_type,
            "signer_id": signer_id,
            "effect": effect_request.digest_payload(),
            "expected_after_revision": hashlib.sha256(
                effect_request.content
            ).hexdigest(),
        }
        request_hash = canonical_hash(request_binding)
        replay = self._find_apply_receipt(item.id, idempotency_key)
        if replay is not None:
            self._assert_exact_replay(replay, request_hash)
            self._append_observation(
                item,
                attempt_id=replay.attempt_id,
                event_type="apply.replayed",
                actor_id=signer_id,
                reason="idempotency key resolved to canonical receipt",
            )
            self.db.commit()
            return self._apply_result(replay, replayed=True)

        prior_intent = (
            self.db.query(WorkAttempt)
            .filter_by(
                work_item_id=item.id,
                effect_idempotency_key=idempotency_key,
            )
            .one_or_none()
        )
        if prior_intent is not None:
            self._append_observation(
                item,
                attempt_id=prior_intent.id,
                event_type="apply.duplicate_blocked",
                actor_id=signer_id,
                reason="effect intent exists without a canonical result receipt",
            )
            self.db.commit()
            raise EffectAlreadyStarted(
                "idempotent effect already started; reconcile before any retry"
            )

        now = ensure_utc(self.clock.now())
        try:
            assert_apply_admitted(
                status=item.status,
                claim_status=claim.status,
                claim_expires_at=claim.expires_at,
                expected_fencing_token=item.active_fencing_token,
                presented_fencing_token=fencing_token,
                expected_revision=attempt.expected_revision,
                observed_revision=observed_revision,
                now=now,
            )
            self._assert_approvals(item, claim, now)
        except DriftDetected:
            attempt.status = "RECOVERY_REQUIRED"
            attempt.finished_at = now
            attempt.terminal_reason = "revision_drift"
            claim.status = "reconciled"
            claim.reconciled_reason = "revision_drift"
            claim.released_at = now
            self._transition(
                item,
                WorkStatus.RECOVERY_REQUIRED,
                actor_id=signer_id,
                reason="revision drift blocked apply",
                attempt_id=attempt.id,
            )
            self.db.commit()
            raise

        if self.dispatcher is None:
            raise ScopeViolation("apply requires a configured typed effect dispatcher")
        self.dispatcher.validate(context, effect_request)

        attempt.status = "RUNNING"
        attempt.effect_idempotency_key = idempotency_key
        attempt.effect_started_at = now
        self._transition(
            item,
            WorkStatus.RUNNING,
            actor_id=signer_id,
            reason="claim admitted before effect",
            attempt_id=attempt.id,
        )
        self.db.commit()  # claim/RUNNING must be durable before effect starts
        started_at = now
        try:
            outcome = self.dispatcher.dispatch(context, effect_request)
            if not self._is_sha256(outcome.output_artifact_hash):
                raise ValueError("effect output_artifact_hash must be SHA-256")
            if (
                outcome.before_artifact_hash != ScopedMutationDispatcher.ABSENT_HASH
                or outcome.output_artifact_hash
                != request_binding["expected_after_revision"]
                or outcome.after_revision != request_binding["expected_after_revision"]
            ):
                raise ValueError("typed dispatcher returned an unbound effect result")
        except Exception as exc:
            self._record_uncertain_effect(
                work_item_id=item.id,
                claim_id=claim.id,
                attempt_id=attempt.id,
                idempotency_key=idempotency_key,
                command=command,
                tool=tool,
                action=action,
                signer_type=signer_type,
                signer_id=signer_id,
                started_at=started_at,
                request_hash=request_hash,
                request_binding=request_binding,
                reason=type(exc).__name__,
            )
            raise WorkEffectFailed("effect failed; reconciliation is required") from exc

        item, claim, attempt = self._locked_apply_rows(work_item_id, claim_id)
        ended_at = ensure_utc(self.clock.now())
        if (
            claim.status != "active"
            or item.active_fencing_token != fencing_token
            or ensure_utc(claim.expires_at) <= ended_at
        ):
            self._record_lost_fence(
                item=item,
                claim=claim,
                attempt=attempt,
                outcome=outcome,
                idempotency_key=idempotency_key,
                command=command,
                tool=tool,
                action=action,
                signer_type=signer_type,
                signer_id=signer_id,
                started_at=started_at,
                ended_at=ended_at,
                request_hash=request_hash,
                request_binding=request_binding,
            )
            raise LeaseExpired(
                "effect finished after lease or fencing authority was lost"
            )

        receipt = WorkReceipt(
            work_item_id=item.id,
            attempt_id=attempt.id,
            claim_id=claim.id,
            receipt_type="apply",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            command=command,
            tool=tool,
            action=action,
            started_at=started_at,
            ended_at=ended_at,
            exit_status=0,
            input_artifact_hash=outcome.before_artifact_hash,
            output_artifact_hash=outcome.output_artifact_hash,
            before_revision=attempt.expected_revision,
            after_revision=outcome.after_revision,
            test_evidence_refs=list(outcome.test_evidence_refs),
            acceptance_evidence=[],
            rollback_result=outcome.rollback_result,
            signer_type=signer_type,
            signer_id=signer_id,
            attestation={**outcome.attestation, "request_binding": request_binding},
            is_terminal=False,
        )
        self.db.add(receipt)
        self.db.flush()
        attempt.status = "VERIFYING"
        attempt.outcome_json = {
            "apply_receipt_id": str(receipt.id),
            "output_artifact_hash": outcome.output_artifact_hash,
        }
        self._transition(
            item,
            WorkStatus.VERIFYING,
            actor_id=signer_id,
            reason="effect bound to apply receipt",
            attempt_id=attempt.id,
        )
        self.db.commit()
        return self._apply_result(receipt, replayed=False)

    def verify(
        self,
        work_item_id: uuid.UUID,
        *,
        attempt_id: uuid.UUID,
        idempotency_key: str,
        acceptance_evidence: list[str],
        test_evidence_refs: list[str],
        signer_type: str,
        signer_id: str,
        observed_revision: str,
    ) -> VerifiedWork:
        self._validate_signer(signer_type, signer_id)
        if not acceptance_evidence:
            raise ValueError("acceptance evidence is required")
        item = self._locked_item(work_item_id)
        attempt = self.db.get(WorkAttempt, attempt_id)
        if attempt is None or attempt.work_item_id != item.id:
            raise WorkNotFound(str(attempt_id))
        applied = (
            self.db.query(WorkReceipt)
            .filter_by(
                work_item_id=item.id,
                attempt_id=attempt.id,
                receipt_type="apply",
                exit_status=0,
            )
            .one_or_none()
        )
        if applied is None or applied.after_revision != observed_revision:
            raise DriftDetected("verify revision differs from successful apply receipt")
        request_binding = {
            "work_item_id": str(item.id),
            "attempt_id": str(attempt.id),
            "apply_receipt_id": str(applied.id),
            "idempotency_key": idempotency_key,
            "acceptance_evidence": acceptance_evidence,
            "test_evidence_refs": test_evidence_refs,
            "signer_type": signer_type,
            "signer_id": signer_id,
            "observed_revision": observed_revision,
        }
        request_hash = canonical_hash(request_binding)
        existing = (
            self.db.query(WorkReceipt)
            .filter_by(
                work_item_id=item.id,
                receipt_type="verify",
                idempotency_key=idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            self._assert_exact_replay(existing, request_hash)
            self._append_observation(
                item,
                attempt_id=existing.attempt_id,
                event_type="verify.replayed",
                actor_id=signer_id,
                reason="idempotency key resolved to exact verify receipt",
            )
            self.db.commit()
            return VerifiedWork(
                receipt_id=existing.id,
                attempt_id=existing.attempt_id,
                evidence_hash=existing.output_artifact_hash,
                observed_revision=existing.after_revision,
                replayed=True,
            )
        if WorkStatus(item.status) is not WorkStatus.VERIFYING:
            raise InvalidWorkTransition("verify requires VERIFYING")
        now = ensure_utc(self.clock.now())
        claim = self.db.get(WorkClaim, applied.claim_id)
        self._assert_current_claim(item, attempt, claim, now=now)
        assert claim is not None
        self._assert_approvals(item, claim, now)
        evidence_hash = canonical_hash(
            {
                "acceptance_evidence": acceptance_evidence,
                "test_evidence_refs": test_evidence_refs,
                "observed_revision": observed_revision,
            }
        )
        receipt = WorkReceipt(
            work_item_id=item.id,
            attempt_id=attempt.id,
            parent_receipt_id=applied.id,
            receipt_type="verify",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            command="cv work verify",
            tool="context-vault",
            action="verify",
            started_at=now,
            ended_at=now,
            exit_status=0,
            before_revision=attempt.expected_revision,
            after_revision=observed_revision,
            output_artifact_hash=evidence_hash,
            test_evidence_refs=test_evidence_refs,
            acceptance_evidence=acceptance_evidence,
            signer_type=signer_type,
            signer_id=signer_id,
            attestation={
                "work_item_id": str(item.id),
                "evidence_hash": evidence_hash,
                "request_binding": request_binding,
            },
            is_terminal=False,
        )
        self.db.add(receipt)
        self.db.commit()
        return VerifiedWork(
            receipt_id=receipt.id,
            attempt_id=attempt.id,
            evidence_hash=evidence_hash,
            observed_revision=observed_revision,
            replayed=False,
        )

    def close(
        self,
        work_item_id: uuid.UUID,
        *,
        attempt_id: uuid.UUID,
        verify_receipt_id: uuid.UUID,
        idempotency_key: str,
        signer_type: str,
        signer_id: str,
        after_revision: str,
    ) -> WorkReceipt:
        self._validate_signer(signer_type, signer_id)
        item = self._locked_item(work_item_id)
        attempt = self.db.get(WorkAttempt, attempt_id)
        if attempt is None or attempt.work_item_id != item.id:
            raise WorkNotFound(str(attempt_id))
        verified = self.db.get(WorkReceipt, verify_receipt_id)
        request_binding = {
            "work_item_id": str(item.id),
            "attempt_id": str(attempt.id),
            "verify_receipt_id": str(verify_receipt_id),
            "idempotency_key": idempotency_key,
            "signer_type": signer_type,
            "signer_id": signer_id,
            "after_revision": after_revision,
        }
        request_hash = canonical_hash(request_binding)
        existing = (
            self.db.query(WorkReceipt)
            .filter_by(
                work_item_id=item.id,
                receipt_type="close",
                idempotency_key=idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            self._assert_exact_replay(existing, request_hash)
            self._append_observation(
                item,
                attempt_id=existing.attempt_id,
                event_type="close.replayed",
                actor_id=signer_id,
                reason="idempotency key resolved to exact close receipt",
            )
            self.db.commit()
            return existing
        if WorkStatus(item.status) is not WorkStatus.VERIFYING:
            raise InvalidWorkTransition("close requires VERIFYING")
        if (
            verified is None
            or verified.work_item_id != item.id
            or verified.attempt_id != attempt.id
            or verified.receipt_type != "verify"
            or verified.exit_status != 0
            or not verified.acceptance_evidence
            or verified.after_revision != after_revision
        ):
            raise DriftDetected("close requires an exact successful verify receipt")
        applied = self.db.get(WorkReceipt, verified.parent_receipt_id)
        if (
            applied is None
            or applied.work_item_id != item.id
            or applied.attempt_id != attempt.id
            or applied.receipt_type != "apply"
            or applied.exit_status != 0
            or applied.claim_id is None
            or applied.after_revision != verified.after_revision
        ):
            raise DriftDetected("verify receipt is not bound to an exact apply receipt")
        now = ensure_utc(self.clock.now())
        claim = self.db.get(WorkClaim, applied.claim_id)
        self._assert_current_claim(item, attempt, claim, now=now)
        assert claim is not None
        self._assert_approvals(item, claim, now)
        receipt = WorkReceipt(
            work_item_id=item.id,
            attempt_id=attempt.id,
            parent_receipt_id=verified.id,
            receipt_type="close",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            command="cv work close",
            tool="context-vault",
            action="close",
            started_at=now,
            ended_at=now,
            exit_status=0,
            output_artifact_hash=verified.output_artifact_hash,
            before_revision=attempt.expected_revision,
            after_revision=after_revision,
            test_evidence_refs=verified.test_evidence_refs,
            acceptance_evidence=verified.acceptance_evidence,
            signer_type=signer_type,
            signer_id=signer_id,
            attestation={
                "work_item_id": str(item.id),
                "verify_receipt_id": str(verified.id),
                "request_binding": request_binding,
            },
            is_terminal=True,
        )
        self.db.add(receipt)
        self.db.flush([receipt])
        validate_transition(
            WorkStatus.VERIFYING,
            WorkStatus.COMPLETED,
            has_terminal_receipt=True,
            has_acceptance_evidence=True,
        )
        attempt.status = "COMPLETED"
        attempt.finished_at = now
        attempt.terminal_reason = "acceptance_verified"
        claim.status = "released"
        claim.released_reason = "work_completed"
        claim.released_at = now
        item.active_fencing_token = None
        self._transition(
            item,
            WorkStatus.COMPLETED,
            actor_id=signer_id,
            reason="terminal receipt and acceptance evidence recorded",
            attempt_id=attempt.id,
            has_terminal_receipt=True,
            has_acceptance_evidence=True,
        )
        self.db.commit()
        return receipt

    def reconcile_stale_leases(self, *, limit: int = 100) -> list[uuid.UUID]:
        now = ensure_utc(self.clock.now())
        stale = (
            self.db.query(WorkClaim)
            .filter(WorkClaim.status == "active", WorkClaim.expires_at <= now)
            .order_by(WorkClaim.expires_at, WorkClaim.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .all()
        )
        reconciled: list[uuid.UUID] = []
        for claim in stale:
            item = self._locked_item(claim.work_item_id)
            attempt = self.db.get(WorkAttempt, claim.attempt_id)
            claim.status = "reconciled"
            claim.reconciled_reason = "stale_lease"
            claim.released_at = now
            if item.active_fencing_token == claim.fencing_token:
                item.active_fencing_token = None
            if attempt is not None:
                attempt.status = "RECOVERY_REQUIRED"
                attempt.finished_at = now
                attempt.terminal_reason = "stale_lease"
            if WorkStatus(item.status) is WorkStatus.RUNNING:
                self._transition(
                    item,
                    WorkStatus.BLOCKED,
                    actor_id="reconciler",
                    reason="stale lease; effect outcome requires inspection",
                    attempt_id=claim.attempt_id,
                )
            elif WorkStatus(item.status) is WorkStatus.VERIFYING:
                self._transition(
                    item,
                    WorkStatus.RECOVERY_REQUIRED,
                    actor_id="reconciler",
                    reason="verification authority expired before terminal outcome",
                    attempt_id=claim.attempt_id,
                )
            else:
                self._append_observation(
                    item,
                    attempt_id=claim.attempt_id,
                    event_type="claim.reconciled",
                    actor_id="reconciler",
                    reason="stale lease; no completion inferred",
                )
            reconciled.append(claim.id)
        self.db.commit()
        return reconciled

    def rollback(
        self,
        work_item_id: uuid.UUID,
        *,
        attempt_id: uuid.UUID,
        claim_id: uuid.UUID,
        fencing_token: int,
        apply_receipt_id: uuid.UUID,
        idempotency_key: str,
        signer_type: str,
        signer_id: str,
        effect_request: EffectRequest,
    ) -> WorkReceipt:
        self._validate_signer(signer_type, signer_id)
        item = self._locked_item(work_item_id)
        attempt = (
            self.db.query(WorkAttempt)
            .filter(WorkAttempt.id == attempt_id)
            .with_for_update()
            .one_or_none()
        )
        applied = (
            self.db.query(WorkReceipt)
            .filter(WorkReceipt.id == apply_receipt_id)
            .with_for_update()
            .one_or_none()
        )
        if (
            attempt is None
            or attempt.work_item_id != item.id
            or applied is None
            or applied.work_item_id != item.id
            or applied.attempt_id != attempt.id
            or applied.receipt_type != "apply"
            or applied.exit_status != 0
            or applied.output_artifact_hash is None
            or applied.claim_id != claim_id
        ):
            raise WorkNotFound("successful apply receipt chain is unavailable")
        claim = (
            self.db.query(WorkClaim)
            .filter(WorkClaim.id == claim_id)
            .with_for_update()
            .one_or_none()
        )
        if claim is None or claim.attempt_id != attempt.id:
            raise WorkNotFound(str(claim_id))
        applied_binding = (applied.attestation or {}).get("request_binding")
        if (
            not isinstance(applied_binding, dict)
            or applied_binding.get("effect") != effect_request.digest_payload()
            or canonical_hash(applied_binding) != applied.request_hash
        ):
            raise IdempotencyConflict(
                "rollback request differs from exact apply effect"
            )
        request_binding = {
            "work_item_id": str(item.id),
            "attempt_id": str(attempt.id),
            "claim_id": str(claim.id),
            "fencing_token": fencing_token,
            "apply_receipt_id": str(applied.id),
            "idempotency_key": idempotency_key,
            "signer_type": signer_type,
            "signer_id": signer_id,
            "expected_after_hash": applied.output_artifact_hash,
            "effect": effect_request.digest_payload(),
        }
        request_hash = canonical_hash(request_binding)
        existing = (
            self.db.query(WorkReceipt)
            .filter_by(
                work_item_id=item.id,
                receipt_type="rollback",
                idempotency_key=idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            self._assert_exact_replay(existing, request_hash)
            self._append_observation(
                item,
                attempt_id=attempt.id,
                event_type="rollback.replayed",
                actor_id=signer_id,
                reason="idempotency key resolved to exact rollback receipt",
            )
            self.db.commit()
            return existing
        if attempt.rollback_started_at is not None:
            if (
                attempt.rollback_idempotency_key != idempotency_key
                or attempt.rollback_request_hash != request_hash
            ):
                raise IdempotencyConflict(
                    "rollback attempt was bound to a different exact request"
                )
            self._append_observation(
                item,
                attempt_id=attempt.id,
                event_type="rollback.duplicate_blocked",
                actor_id=signer_id,
                reason="rollback intent exists without a canonical result receipt",
            )
            self.db.commit()
            raise EffectAlreadyStarted(
                "idempotent rollback already started; reconcile before any retry"
            )
        if WorkStatus(item.status) is not WorkStatus.VERIFYING:
            raise InvalidWorkTransition("rollback requires VERIFYING")
        now = ensure_utc(self.clock.now())
        self._assert_current_claim(
            item,
            attempt,
            claim,
            now=now,
            presented_fencing_token=fencing_token,
        )
        self._assert_approvals(item, claim, now)
        assert self.dispatcher is not None
        context = self._scoped_effect_context(item, claim)
        attempt.rollback_idempotency_key = idempotency_key
        attempt.rollback_request_hash = request_hash
        attempt.rollback_started_at = now
        self._append_observation(
            item,
            attempt_id=attempt.id,
            event_type="rollback.intent_recorded",
            actor_id=signer_id,
            reason="rollback intent committed before external cleanup",
        )
        self.db.commit()
        started_at = now
        outcome = self.dispatcher.rollback(
            context,
            effect_request,
            expected_after_hash=applied.output_artifact_hash,
        )
        item = self._locked_item(work_item_id)
        attempt = (
            self.db.query(WorkAttempt)
            .filter(WorkAttempt.id == attempt_id)
            .with_for_update()
            .one()
        )
        claim = (
            self.db.query(WorkClaim)
            .filter(WorkClaim.id == claim_id)
            .with_for_update()
            .one()
        )
        ended_at = ensure_utc(self.clock.now())
        self._assert_current_claim(
            item,
            attempt,
            claim,
            now=ended_at,
            presented_fencing_token=fencing_token,
        )
        receipt = WorkReceipt(
            work_item_id=item.id,
            attempt_id=attempt.id,
            claim_id=claim.id,
            parent_receipt_id=applied.id,
            receipt_type="rollback",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            command="cv work rollback",
            tool="context-vault",
            action="rollback",
            started_at=started_at,
            ended_at=ended_at,
            exit_status=0,
            input_artifact_hash=outcome.before_artifact_hash,
            output_artifact_hash=outcome.output_artifact_hash,
            before_revision=applied.after_revision,
            after_revision=outcome.after_revision,
            test_evidence_refs=[],
            acceptance_evidence=[],
            rollback_result=outcome.rollback_result,
            signer_type=signer_type,
            signer_id=signer_id,
            attestation={**outcome.attestation, "request_binding": request_binding},
            is_terminal=False,
        )
        self.db.add(receipt)
        self.db.flush([receipt])
        attempt.status = "RECOVERY_REQUIRED"
        attempt.finished_at = ended_at
        attempt.terminal_reason = "effect_rolled_back"
        claim.status = "released"
        claim.released_reason = "effect_rolled_back"
        claim.released_at = ended_at
        item.active_fencing_token = None
        self._transition(
            item,
            WorkStatus.RECOVERY_REQUIRED,
            actor_id=signer_id,
            reason="effect was rolled back with exact cleanup receipt",
            attempt_id=attempt.id,
        )
        self.db.commit()
        return receipt

    def _validate_prepared(self, item: WorkItem, prepared: PreparedWork) -> None:
        if prepared.work_item_id != item.id:
            raise DriftDetected("prepare token belongs to another work item")
        expected = canonical_hash(
            {
                "work_item_id": str(item.id),
                "work_item_revision": prepared.work_item_revision,
                "expected_revision": item.expected_revision,
                "scope_hash": canonical_hash(item.scope_json),
            }
        )
        if (
            prepared.work_item_revision != item.revision
            or prepared.expected_revision != item.expected_revision
            or prepared.drift_token != expected
        ):
            raise DriftDetected("work item changed after prepare")

    def _assert_approvals(
        self, item: WorkItem, claim: WorkClaim, now: datetime
    ) -> None:
        required = set(item.required_approvals or [])
        if not required:
            return
        approvals = (
            self.db.query(WorkApproval)
            .filter_by(
                work_item_id=item.id,
                scope_hash=claim.scope_hash,
                decision="approved",
            )
            .all()
        )
        admitted = {
            row.approval_type
            for row in approvals
            if row.actor_type in {"human", "policy"}
            and row.revoked_at is None
            and (row.expires_at is None or ensure_utc(row.expires_at) > now)
        }
        if not required.issubset(admitted):
            raise ApprovalRequired(
                f"missing approvals: {sorted(required.difference(admitted))}"
            )

    def _assert_trusted_approval_actor(
        self,
        item: WorkItem,
        *,
        approval_type: str,
        actor_type: str,
        actor_id: str,
    ) -> None:
        if item.project_id is None:
            raise ApprovalRequired(
                "workspace-only work has no registered project approval policy"
            )
        project = self.db.get(Project, item.project_id)
        if project is None or project.workspace_id != item.workspace_id:
            raise ApprovalRequired("work project is outside its workspace")
        manifest = (
            self.db.query(ContextSource)
            .filter_by(
                project_id=project.id,
                source_id=f"project-manifest:{project.id}",
                status="ACTIVE",
            )
            .order_by(ContextSource.version.desc())
            .first()
        )
        raw_policy = (
            None
            if manifest is None
            else (manifest.metadata_json or {}).get("approval_policy")
        )
        if not isinstance(raw_policy, dict):
            raise ApprovalRequired("project approval policy is not registered")
        owner = raw_policy.get("owner")
        approvers = raw_policy.get("approvers")
        human_required_for = raw_policy.get("human_required_for")
        if (
            not isinstance(owner, str)
            or not isinstance(approvers, list)
            or not all(isinstance(value, str) and value.strip() for value in approvers)
            or not isinstance(human_required_for, list)
            or not all(
                isinstance(value, str) and value.strip() for value in human_required_for
            )
        ):
            raise ApprovalRequired("project approval policy is invalid")

        if actor_type == "policy":
            if (
                approval_type in human_required_for
                or actor_id not in self.trusted_policy_ids
                or f"policy:{actor_id}" not in approvers
            ):
                raise ApprovalRequired(
                    "policy actor is not trusted and registered for this project"
                )
            return

        if self.authenticated_principal_id is None:
            raise ApprovalRequired("human approval requires an authenticated principal")
        principal = self.db.get(Principal, self.authenticated_principal_id)
        membership = self.db.get(
            WorkspaceMembership,
            {
                "workspace_id": item.workspace_id,
                "principal_id": self.authenticated_principal_id,
            },
        )
        if (
            principal is None
            or not principal.is_active
            or principal.subject != actor_id
            or membership is None
            or membership.role not in {"admin", "member"}
            or actor_id
            not in {
                owner,
                *(value for value in approvers if not value.startswith("policy:")),
            }
        ):
            raise ApprovalRequired(
                "human actor is not the authenticated project approval authority"
            )

    @staticmethod
    def _assert_current_claim(
        item: WorkItem,
        attempt: WorkAttempt,
        claim: WorkClaim | None,
        *,
        now: datetime,
        presented_fencing_token: int | None = None,
    ) -> None:
        if (
            claim is None
            or claim.work_item_id != item.id
            or claim.attempt_id != attempt.id
            or claim.status != "active"
            or ensure_utc(claim.expires_at) <= now
            or item.active_attempt_id != attempt.id
            or item.active_fencing_token != claim.fencing_token
            or (
                presented_fencing_token is not None
                and presented_fencing_token != claim.fencing_token
            )
        ):
            raise LeaseExpired(
                "operation requires the current active claim and fencing authority"
            )

    def _locked_item(self, work_item_id: uuid.UUID) -> WorkItem:
        item = (
            self.db.query(WorkItem)
            .filter(WorkItem.id == work_item_id)
            .with_for_update()
            .one_or_none()
        )
        if item is None:
            raise WorkNotFound(str(work_item_id))
        return item

    def _locked_apply_rows(
        self, work_item_id: uuid.UUID, claim_id: uuid.UUID
    ) -> tuple[WorkItem, WorkClaim, WorkAttempt]:
        item = self._locked_item(work_item_id)
        claim = (
            self.db.query(WorkClaim)
            .filter(WorkClaim.id == claim_id, WorkClaim.work_item_id == item.id)
            .with_for_update()
            .one_or_none()
        )
        if claim is None:
            raise WorkNotFound(str(claim_id))
        attempt = self.db.get(WorkAttempt, claim.attempt_id)
        if attempt is None:
            raise WorkNotFound(str(claim.attempt_id))
        return item, claim, attempt

    def _transition(
        self,
        item: WorkItem,
        new_status: WorkStatus,
        *,
        actor_id: str,
        reason: str,
        attempt_id: uuid.UUID | None = None,
        has_terminal_receipt: bool = False,
        has_acceptance_evidence: bool = False,
    ) -> None:
        previous = WorkStatus(item.status)
        validate_transition(
            previous,
            new_status,
            has_terminal_receipt=has_terminal_receipt,
            has_acceptance_evidence=has_acceptance_evidence,
        )
        next_revision = item.revision + 1
        event = self._append_event(
            item,
            attempt_id=attempt_id,
            event_sequence=next_revision,
            event_type="work.transition",
            actor_type="service",
            actor_id=actor_id,
            reason=reason,
            previous_state=previous.value,
            new_state=new_status.value,
        )
        self.db.flush([event])
        item.status = new_status.value
        item.revision = next_revision
        item.updated_at = self.clock.now()

    def _append_observation(
        self,
        item: WorkItem,
        *,
        attempt_id: uuid.UUID | None,
        event_type: str,
        actor_id: str,
        reason: str,
    ) -> None:
        item.revision += 1
        item.updated_at = self.clock.now()
        self._append_event(
            item,
            attempt_id=attempt_id,
            event_type=event_type,
            actor_type="service",
            actor_id=actor_id,
            reason=reason,
            previous_state=item.status,
            new_state=item.status,
        )

    def _append_event(
        self,
        item: WorkItem,
        *,
        event_type: str,
        actor_type: str,
        actor_id: str,
        reason: str,
        previous_state: str | None,
        new_state: str | None,
        attempt_id: uuid.UUID | None = None,
        event_sequence: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> WorkEvent:
        body = payload or {}
        event = WorkEvent(
            work_item_id=item.id,
            attempt_id=attempt_id,
            event_sequence=(
                event_sequence
                if event_sequence is not None
                else item.revision + 1
                if item.revision == 0
                else item.revision
            ),
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            correlation_id=uuid.uuid4(),
            previous_state=previous_state,
            new_state=new_state,
            payload_schema_version="work-event-v1",
            payload_hash=canonical_hash(body),
            payload_json=body,
            created_at=self.clock.now(),
        )
        if item.revision == 0:
            item.revision = 1
        self.db.add(event)
        return event

    def _find_apply_receipt(
        self, work_item_id: uuid.UUID, idempotency_key: str
    ) -> WorkReceipt | None:
        return (
            self.db.query(WorkReceipt)
            .filter_by(
                work_item_id=work_item_id,
                receipt_type="apply",
                idempotency_key=idempotency_key,
            )
            .one_or_none()
        )

    def _scoped_effect_context(
        self, item: WorkItem, claim: WorkClaim
    ) -> ScopedEffectContext:
        if self.dispatcher is None:
            raise ScopeViolation("apply requires a configured typed effect dispatcher")
        if canonical_hash(item.scope_json) != claim.scope_hash:
            raise ScopeViolation(
                "claim scope no longer matches authoritative work scope"
            )
        raw_paths = claim.resource_scope.get("paths")
        raw_capabilities = claim.resource_scope.get("capabilities")
        if not isinstance(raw_paths, list) or not all(
            isinstance(path, str) and path for path in raw_paths
        ):
            raise ScopeViolation("claim scope requires an explicit path allowlist")
        if not isinstance(raw_capabilities, list):
            raise ScopeViolation(
                "claim scope requires an explicit capability allowlist"
            )
        try:
            capabilities = tuple(EffectCapability(value) for value in raw_capabilities)
        except (TypeError, ValueError) as exc:
            raise ScopeViolation("claim contains an unknown effect capability") from exc
        return ScopedEffectContext(
            root=self.dispatcher.root,
            allowed_paths=tuple(raw_paths),
            capabilities=capabilities,
        )

    @staticmethod
    def _assert_exact_replay(receipt: WorkReceipt, request_hash: str) -> None:
        persisted_binding = (receipt.attestation or {}).get("request_binding")
        if (
            not isinstance(persisted_binding, dict)
            or canonical_hash(persisted_binding) != receipt.request_hash
            or receipt.request_hash != request_hash
        ):
            raise IdempotencyConflict(
                "idempotency key was previously bound to a different exact request"
            )

    @staticmethod
    def _validate_signer(signer_type: str, signer_id: str) -> None:
        if signer_type not in {"human", "policy", "service", "cli"}:
            raise ValueError("receipt signer must be human, policy, service or cli")
        if not signer_id.strip():
            raise ValueError("receipt signer_id is required")

    @staticmethod
    def _is_sha256(value: str) -> bool:
        return len(value) == 64 and all(
            character in "0123456789abcdef" for character in value
        )

    @staticmethod
    def _apply_result(receipt: WorkReceipt, *, replayed: bool) -> ApplyResult:
        return ApplyResult(
            receipt_id=receipt.id,
            attempt_id=receipt.attempt_id,
            replayed=replayed,
            exit_status=receipt.exit_status,
            output_artifact_hash=receipt.output_artifact_hash,
            after_revision=receipt.after_revision,
        )

    def _record_uncertain_effect(
        self,
        *,
        work_item_id: uuid.UUID,
        claim_id: uuid.UUID,
        attempt_id: uuid.UUID,
        idempotency_key: str,
        command: str,
        tool: str,
        action: str,
        signer_type: str,
        signer_id: str,
        started_at: datetime,
        request_hash: str,
        request_binding: dict[str, Any],
        reason: str,
    ) -> None:
        item, claim, attempt = self._locked_apply_rows(work_item_id, claim_id)
        ended_at = ensure_utc(self.clock.now())
        receipt = WorkReceipt(
            work_item_id=item.id,
            attempt_id=attempt_id,
            claim_id=claim.id,
            receipt_type="apply",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            command=command,
            tool=tool,
            action=action,
            started_at=started_at,
            ended_at=ended_at,
            exit_status=70,
            input_artifact_hash=ScopedMutationDispatcher.ABSENT_HASH,
            before_revision=attempt.expected_revision,
            after_revision=attempt.expected_revision,
            test_evidence_refs=[],
            acceptance_evidence=[],
            rollback_result={"status": "unknown"},
            signer_type=signer_type,
            signer_id=signer_id,
            attestation={
                "failure_type": reason,
                "request_binding": request_binding,
            },
            is_terminal=False,
        )
        self.db.add(receipt)
        claim.status = "reconciled"
        claim.reconciled_reason = "effect_failed_or_unknown"
        claim.released_at = ended_at
        attempt.status = "RECOVERY_REQUIRED"
        attempt.finished_at = ended_at
        attempt.terminal_reason = "effect_failed_or_unknown"
        self._transition(
            item,
            WorkStatus.RECOVERY_REQUIRED,
            actor_id=signer_id,
            reason="effect outcome uncertain; recovery required",
            attempt_id=attempt.id,
        )
        self.db.commit()

    def _record_lost_fence(
        self,
        *,
        item: WorkItem,
        claim: WorkClaim,
        attempt: WorkAttempt,
        outcome: EffectResult,
        idempotency_key: str,
        command: str,
        tool: str,
        action: str,
        signer_type: str,
        signer_id: str,
        started_at: datetime,
        ended_at: datetime,
        request_hash: str,
        request_binding: dict[str, Any],
    ) -> None:
        receipt = WorkReceipt(
            work_item_id=item.id,
            attempt_id=attempt.id,
            claim_id=claim.id,
            receipt_type="apply",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            command=command,
            tool=tool,
            action=action,
            started_at=started_at,
            ended_at=ended_at,
            exit_status=75,
            input_artifact_hash=outcome.before_artifact_hash,
            output_artifact_hash=outcome.output_artifact_hash,
            before_revision=attempt.expected_revision,
            after_revision=outcome.after_revision,
            test_evidence_refs=list(outcome.test_evidence_refs),
            acceptance_evidence=[],
            rollback_result={"status": "required"},
            signer_type=signer_type,
            signer_id=signer_id,
            attestation={
                "failure_type": "fencing_authority_lost",
                "request_binding": request_binding,
            },
            is_terminal=False,
        )
        self.db.add(receipt)
        if claim.status == "active":
            claim.status = "reconciled"
            claim.reconciled_reason = "fencing_authority_lost"
            claim.released_at = ended_at
        attempt.status = "RECOVERY_REQUIRED"
        attempt.finished_at = ended_at
        attempt.terminal_reason = "fencing_authority_lost"
        if WorkStatus(item.status) is WorkStatus.RUNNING:
            self._transition(
                item,
                WorkStatus.RECOVERY_REQUIRED,
                actor_id=signer_id,
                reason="effect completed without current fencing authority",
                attempt_id=attempt.id,
            )
        self.db.commit()
