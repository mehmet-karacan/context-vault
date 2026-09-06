"""Human-controlled, versioned knowledge lifecycle domain model."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from threading import RLock
from typing import cast
from uuid import UUID, uuid4

from src.context_vault.context_compiler import DataClassification


class KnowledgePolicyError(ValueError):
    """A knowledge transition is not authorized by the lifecycle policy."""


class ActorKind(StrEnum):
    HUMAN = "HUMAN"
    POLICY = "POLICY"
    MODEL = "MODEL"


class KnowledgeStatus(StrEnum):
    PROPOSED = "PROPOSED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class ConflictStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    actor_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActorKind):
            raise KnowledgePolicyError("actor kind must be an exact ActorKind")
        if not isinstance(self.actor_id, str) or not self.actor_id.strip():
            raise KnowledgePolicyError("actor_id cannot be empty")


@dataclass(frozen=True)
class ApprovalPolicy:
    authorized_humans: frozenset[str]
    authorized_policies: frozenset[str] = frozenset()
    require_independent_approval: bool = True

    def __post_init__(self) -> None:
        humans = _immutable_actor_ids(self.authorized_humans, "authorized_humans")
        policies = _immutable_actor_ids(self.authorized_policies, "authorized_policies")
        if not isinstance(self.require_independent_approval, bool):
            raise KnowledgePolicyError(
                "require_independent_approval must be an exact boolean"
            )
        object.__setattr__(self, "authorized_humans", humans)
        object.__setattr__(self, "authorized_policies", policies)

    def permits(self, actor: Actor) -> bool:
        _require_exact_actor(actor, "actor")
        if actor.kind is ActorKind.HUMAN:
            return actor.actor_id in self.authorized_humans
        if actor.kind is ActorKind.POLICY:
            return actor.actor_id in self.authorized_policies
        return False


@dataclass(frozen=True)
class KnowledgeRevision:
    revision_id: UUID
    item_id: UUID
    version: int
    status: KnowledgeStatus
    content: str
    content_hash: str
    source_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    owner: str
    scope: str
    confidence: float
    classification: DataClassification
    valid_from: datetime
    valid_until: datetime | None
    supersedes_revision_id: UUID | None
    proposed_by: Actor
    reviewed_by: Actor | None
    approved_by: Actor | None
    created_at: datetime
    terminal_by: Actor | None = None
    terminal_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.revision_id, UUID) or not isinstance(self.item_id, UUID):
            raise KnowledgePolicyError("revision_id and item_id must be exact UUIDs")
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version <= 0
        ):
            raise KnowledgePolicyError("knowledge version must be positive")
        if not isinstance(self.status, KnowledgeStatus):
            raise KnowledgePolicyError("status must be an exact KnowledgeStatus")
        if not isinstance(self.classification, DataClassification):
            raise KnowledgePolicyError(
                "classification must be an exact DataClassification"
            )
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.content, self.owner, self.scope)
        ):
            raise KnowledgePolicyError("content, owner and scope cannot be empty")
        if not isinstance(self.content_hash, str):
            raise KnowledgePolicyError("knowledge content_hash must be a string")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(float(self.confidence))
        ):
            raise KnowledgePolicyError("confidence must be a finite number")
        object.__setattr__(self, "confidence", float(self.confidence))
        if self.supersedes_revision_id is not None and (
            not isinstance(self.supersedes_revision_id, UUID)
        ):
            raise KnowledgePolicyError("supersedes_revision_id must be an exact UUID")
        _require_exact_actor(self.proposed_by, "proposed_by")
        for label, authority in (
            ("reviewed_by", self.reviewed_by),
            ("approved_by", self.approved_by),
            ("terminal_by", self.terminal_by),
        ):
            if authority is not None:
                _require_exact_actor(authority, label)
        source_ids = _immutable_references(self.source_ids, "source")
        evidence_ids = _immutable_references(self.evidence_ids, "evidence")
        object.__setattr__(self, "source_ids", source_ids)
        object.__setattr__(self, "evidence_ids", evidence_ids)
        if hashlib.sha256(self.content.encode()).hexdigest() != self.content_hash:
            raise KnowledgePolicyError("knowledge content_hash does not match content")
        if not self.source_ids or not self.evidence_ids:
            raise KnowledgePolicyError("source and evidence references are required")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise KnowledgePolicyError("source references must be unique")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise KnowledgePolicyError("evidence references must be unique")
        if not 0.0 <= self.confidence <= 1.0:
            raise KnowledgePolicyError("confidence must be between 0 and 1")
        _require_aware(self.valid_from, "valid_from")
        _require_aware(self.created_at, "created_at")
        if self.valid_until is not None:
            _require_aware(self.valid_until, "valid_until")
            if self.valid_until <= self.valid_from:
                raise KnowledgePolicyError("valid_until must be after valid_from")
        if self.status is KnowledgeStatus.PROPOSED and (
            self.reviewed_by is not None or self.approved_by is not None
        ):
            raise KnowledgePolicyError(
                "proposed knowledge cannot carry review or approval"
            )
        if self.status is KnowledgeStatus.REVIEWED and (
            self.reviewed_by is None or self.approved_by is not None
        ):
            raise KnowledgePolicyError("reviewed knowledge requires only a reviewer")
        if self.status in {
            KnowledgeStatus.APPROVED,
            KnowledgeStatus.SUPERSEDED,
            KnowledgeStatus.REVOKED,
            KnowledgeStatus.EXPIRED,
        } and (self.reviewed_by is None or self.approved_by is None):
            raise KnowledgePolicyError(
                "authoritative knowledge requires review and approval"
            )
        terminal_statuses = {
            KnowledgeStatus.SUPERSEDED,
            KnowledgeStatus.REVOKED,
            KnowledgeStatus.EXPIRED,
        }
        if self.status in terminal_statuses:
            if self.terminal_by is None or self.terminal_at is None:
                raise KnowledgePolicyError(
                    "terminal knowledge requires actor and timestamp"
                )
            _require_aware(self.terminal_at, "terminal_at")
        elif self.terminal_by is not None or self.terminal_at is not None:
            raise KnowledgePolicyError(
                "non-terminal knowledge cannot carry terminal audit"
            )
        for authority in (self.reviewed_by, self.approved_by):
            if authority is not None and authority.kind is ActorKind.MODEL:
                raise KnowledgePolicyError("model cannot be a knowledge authority")
        if self.terminal_by is not None and self.terminal_by.kind is ActorKind.MODEL:
            raise KnowledgePolicyError("model cannot perform a terminal transition")

    def is_valid_at(self, instant: datetime) -> bool:
        _require_aware(instant, "instant")
        return self.valid_from <= instant and (
            self.valid_until is None or instant < self.valid_until
        )


@dataclass(frozen=True)
class KnowledgeConflict:
    conflict_id: UUID
    item_id: UUID
    existing_revision_id: UUID
    proposed_revision_id: UUID
    reason: str
    status: ConflictStatus
    resolution_revision_id: UUID | None
    created_at: datetime
    resolved_by: Actor | None
    resolved_at: datetime | None

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (
                self.conflict_id,
                self.item_id,
                self.existing_revision_id,
                self.proposed_revision_id,
            )
        ):
            raise KnowledgePolicyError("conflict identifiers must be exact UUIDs")
        if not isinstance(self.status, ConflictStatus):
            raise KnowledgePolicyError("status must be an exact ConflictStatus")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise KnowledgePolicyError("conflict reason cannot be empty")
        if self.resolution_revision_id is not None and (
            not isinstance(self.resolution_revision_id, UUID)
        ):
            raise KnowledgePolicyError("resolution_revision_id must be an exact UUID")
        if self.resolved_by is not None:
            _require_exact_actor(self.resolved_by, "resolved_by")
            if self.resolved_by.kind is ActorKind.MODEL:
                raise KnowledgePolicyError("model cannot resolve a knowledge conflict")
        _require_aware(self.created_at, "created_at")
        if self.status is ConflictStatus.OPEN and any(
            value is not None
            for value in (
                self.resolution_revision_id,
                self.resolved_by,
                self.resolved_at,
            )
        ):
            raise KnowledgePolicyError("open conflict cannot carry resolution audit")
        if self.status is ConflictStatus.RESOLVED:
            if (
                self.resolution_revision_id is None
                or self.resolved_by is None
                or self.resolved_at is None
            ):
                raise KnowledgePolicyError("resolved conflict requires complete audit")
            _require_aware(self.resolved_at, "resolved_at")


class KnowledgeLedger:
    """In-memory domain aggregate; persistence adapters may store each transition."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._revisions: dict[UUID, KnowledgeRevision] = {}
        self._item_revisions: dict[UUID, list[UUID]] = {}
        self._conflicts: dict[UUID, KnowledgeConflict] = {}

    @property
    def revisions(self) -> tuple[KnowledgeRevision, ...]:
        return tuple(
            sorted(
                self._revisions.values(),
                key=lambda revision: (str(revision.item_id), revision.version),
            )
        )

    @property
    def conflicts(self) -> tuple[KnowledgeConflict, ...]:
        return tuple(
            sorted(self._conflicts.values(), key=lambda value: str(value.conflict_id))
        )

    def propose(
        self,
        *,
        item_id: UUID,
        content: str,
        source_ids: tuple[str, ...],
        evidence_ids: tuple[str, ...],
        owner: str,
        scope: str,
        confidence: float,
        classification: DataClassification,
        proposed_by: Actor,
        valid_from: datetime,
        valid_until: datetime | None = None,
        supersedes_revision_id: UUID | None = None,
        now: datetime | None = None,
        revision_id: UUID | None = None,
    ) -> KnowledgeRevision:
        """Atomically create a proposal and its conflict records."""

        with self._lock:
            return self._propose_locked(
                item_id=item_id,
                content=content,
                source_ids=source_ids,
                evidence_ids=evidence_ids,
                owner=owner,
                scope=scope,
                confidence=confidence,
                classification=classification,
                proposed_by=proposed_by,
                valid_from=valid_from,
                valid_until=valid_until,
                supersedes_revision_id=supersedes_revision_id,
                now=now,
                revision_id=revision_id,
            )

    def _propose_locked(
        self,
        *,
        item_id: UUID,
        content: str,
        source_ids: tuple[str, ...],
        evidence_ids: tuple[str, ...],
        owner: str,
        scope: str,
        confidence: float,
        classification: DataClassification,
        proposed_by: Actor,
        valid_from: datetime,
        valid_until: datetime | None = None,
        supersedes_revision_id: UUID | None = None,
        now: datetime | None = None,
        revision_id: UUID | None = None,
    ) -> KnowledgeRevision:
        """Create only a proposal, including when the source actor is a model."""

        created_at = now or datetime.now(timezone.utc)
        _require_aware(created_at, "now")
        item_revision_ids = self._item_revisions.get(item_id, [])
        version = len(item_revision_ids) + 1
        if supersedes_revision_id is not None:
            predecessor = self._get(supersedes_revision_id)
            if predecessor.item_id != item_id:
                raise KnowledgePolicyError(
                    "superseded revision belongs to another item"
                )
            if predecessor.status is not KnowledgeStatus.APPROVED:
                raise KnowledgePolicyError("only approved knowledge can be superseded")

        revision = KnowledgeRevision(
            revision_id=revision_id or uuid4(),
            item_id=item_id,
            version=version,
            status=KnowledgeStatus.PROPOSED,
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            source_ids=source_ids,
            evidence_ids=evidence_ids,
            owner=owner,
            scope=scope,
            confidence=confidence,
            classification=classification,
            valid_from=valid_from,
            valid_until=valid_until,
            supersedes_revision_id=supersedes_revision_id,
            proposed_by=proposed_by,
            reviewed_by=None,
            approved_by=None,
            created_at=created_at,
        )
        if revision.revision_id in self._revisions:
            raise KnowledgePolicyError("revision_id already exists")
        self._revisions[revision.revision_id] = revision
        self._item_revisions.setdefault(item_id, []).append(revision.revision_id)
        self._record_conflicts(revision, created_at)
        return revision

    def propose_projection_edit(self, **kwargs: object) -> KnowledgeRevision:
        """Import a Markdown/Obsidian edit as a proposal, never as authority."""

        proposed_by = kwargs.get("proposed_by")
        if not isinstance(proposed_by, Actor):
            raise KnowledgePolicyError("projection import requires a proposed_by actor")
        return self.propose(**kwargs)  # type: ignore[arg-type]

    def review(self, revision_id: UUID, *, reviewer: Actor) -> KnowledgeRevision:
        revision = self._get(revision_id)
        if revision.status is not KnowledgeStatus.PROPOSED:
            raise KnowledgePolicyError("only proposed knowledge can be reviewed")
        self._require_authority_kind(reviewer)
        if reviewer == revision.proposed_by:
            raise KnowledgePolicyError("proposer cannot review its own knowledge")
        reviewed = replace(
            revision,
            status=KnowledgeStatus.REVIEWED,
            reviewed_by=reviewer,
        )
        self._revisions[revision_id] = reviewed
        return reviewed

    def approve(
        self,
        revision_id: UUID,
        *,
        approver: Actor,
        policy: ApprovalPolicy,
        now: datetime | None = None,
    ) -> KnowledgeRevision:
        """Atomically revalidate the active head and apply approval/supersession."""

        with self._lock:
            return self._approve_locked(
                revision_id,
                approver=approver,
                policy=policy,
                now=now,
            )

    def _approve_locked(
        self,
        revision_id: UUID,
        *,
        approver: Actor,
        policy: ApprovalPolicy,
        now: datetime | None = None,
    ) -> KnowledgeRevision:
        if not isinstance(policy, ApprovalPolicy):
            raise KnowledgePolicyError("policy must be an exact ApprovalPolicy")
        revision = self._get(revision_id)
        if revision.status is not KnowledgeStatus.REVIEWED:
            raise KnowledgePolicyError("only reviewed knowledge can be approved")
        self._require_authority_kind(approver)
        if not policy.permits(approver):
            raise KnowledgePolicyError("actor is not authorized to approve knowledge")
        if policy.require_independent_approval and (
            approver == revision.proposed_by or approver == revision.reviewed_by
        ):
            raise KnowledgePolicyError("knowledge approval must be independent")
        open_conflict = next(
            (
                conflict
                for conflict in self._conflicts.values()
                if conflict.proposed_revision_id == revision_id
                and conflict.status is ConflictStatus.OPEN
            ),
            None,
        )
        if open_conflict is not None:
            raise KnowledgePolicyError(
                "conflicting knowledge must be resolved before approval"
            )

        transition_at = now or datetime.now(timezone.utc)
        _require_aware(transition_at, "now")
        active_heads = tuple(
            candidate
            for candidate in self.revisions
            if candidate.item_id == revision.item_id
            and candidate.status is KnowledgeStatus.APPROVED
        )
        predecessor: KnowledgeRevision | None = None
        if revision.supersedes_revision_id is not None:
            predecessor = self._get(revision.supersedes_revision_id)
            if predecessor.status is not KnowledgeStatus.APPROVED or active_heads != (
                predecessor,
            ):
                raise KnowledgePolicyError(
                    "stale predecessor: approved knowledge head changed"
                )
        elif active_heads:
            raise KnowledgePolicyError(
                "stale predecessor: proposal did not bind the approved knowledge head"
            )

        approved = replace(
            revision,
            status=KnowledgeStatus.APPROVED,
            approved_by=approver,
        )
        self._revisions[revision_id] = approved
        if predecessor is not None:
            self._revisions[predecessor.revision_id] = replace(
                predecessor,
                status=KnowledgeStatus.SUPERSEDED,
                terminal_by=approver,
                terminal_at=transition_at,
            )
        return approved

    def resolve_conflict(
        self,
        conflict_id: UUID,
        *,
        resolution_revision_id: UUID,
        resolver: Actor,
        policy: ApprovalPolicy,
        now: datetime | None = None,
    ) -> KnowledgeConflict:
        with self._lock:
            return self._resolve_conflict_locked(
                conflict_id,
                resolution_revision_id=resolution_revision_id,
                resolver=resolver,
                policy=policy,
                now=now,
            )

    def _resolve_conflict_locked(
        self,
        conflict_id: UUID,
        *,
        resolution_revision_id: UUID,
        resolver: Actor,
        policy: ApprovalPolicy,
        now: datetime | None = None,
    ) -> KnowledgeConflict:
        if not isinstance(policy, ApprovalPolicy):
            raise KnowledgePolicyError("policy must be an exact ApprovalPolicy")
        self._require_authority_kind(resolver)
        conflict = self._conflicts.get(conflict_id)
        if conflict is None:
            raise KnowledgePolicyError("unknown conflict")
        if conflict.status is not ConflictStatus.OPEN:
            raise KnowledgePolicyError("conflict is already resolved")
        existing = self._get(conflict.existing_revision_id)
        if existing.status is not KnowledgeStatus.APPROVED:
            raise KnowledgePolicyError("conflict predecessor is no longer active")
        self._require_lifecycle_authority(existing, actor=resolver, policy=policy)
        resolution = self._get(resolution_revision_id)
        if resolution.item_id != conflict.item_id:
            raise KnowledgePolicyError("conflict resolution belongs to another item")
        if resolution.status not in {
            KnowledgeStatus.PROPOSED,
            KnowledgeStatus.REVIEWED,
        }:
            raise KnowledgePolicyError("conflict resolution is not pending approval")
        if resolution.supersedes_revision_id != conflict.existing_revision_id:
            raise KnowledgePolicyError(
                "conflict resolution must explicitly supersede the existing revision"
            )
        resolved_at = now or datetime.now(timezone.utc)
        _require_aware(resolved_at, "now")
        resolved = replace(
            conflict,
            status=ConflictStatus.RESOLVED,
            resolution_revision_id=resolution_revision_id,
            resolved_by=resolver,
            resolved_at=resolved_at,
        )
        self._conflicts[conflict_id] = resolved
        return resolved

    def revoke(
        self,
        revision_id: UUID,
        *,
        actor: Actor,
        policy: ApprovalPolicy,
        now: datetime | None = None,
    ) -> KnowledgeRevision:
        return self._terminal_transition(
            revision_id,
            actor=actor,
            policy=policy,
            status=KnowledgeStatus.REVOKED,
            now=now,
        )

    def expire(
        self,
        revision_id: UUID,
        *,
        actor: Actor,
        policy: ApprovalPolicy,
        now: datetime | None = None,
    ) -> KnowledgeRevision:
        return self._terminal_transition(
            revision_id,
            actor=actor,
            policy=policy,
            status=KnowledgeStatus.EXPIRED,
            now=now,
        )

    def approved_projection(self, *, at: datetime) -> tuple[KnowledgeRevision, ...]:
        """Return only current approved knowledge for read-only projections."""

        _require_aware(at, "at")
        return tuple(
            revision
            for revision in self.revisions
            if revision.status is KnowledgeStatus.APPROVED and revision.is_valid_at(at)
        )

    def _terminal_transition(
        self,
        revision_id: UUID,
        *,
        actor: Actor,
        policy: ApprovalPolicy,
        status: KnowledgeStatus,
        now: datetime | None,
    ) -> KnowledgeRevision:
        with self._lock:
            self._require_authority_kind(actor)
            revision = self._get(revision_id)
            self._require_lifecycle_authority(revision, actor=actor, policy=policy)
            if revision.status is not KnowledgeStatus.APPROVED:
                raise KnowledgePolicyError(
                    "only approved knowledge can enter a terminal state"
                )
            transition_at = now or datetime.now(timezone.utc)
            _require_aware(transition_at, "now")
            terminal = replace(
                revision,
                status=status,
                terminal_by=actor,
                terminal_at=transition_at,
            )
            self._revisions[revision_id] = terminal
            return terminal

    def _record_conflicts(self, proposal: KnowledgeRevision, now: datetime) -> None:
        for existing in self.revisions:
            if existing.revision_id == proposal.revision_id:
                continue
            if existing.item_id != proposal.item_id:
                continue
            if existing.status is not KnowledgeStatus.APPROVED:
                continue
            if existing.content_hash == proposal.content_hash:
                continue
            if proposal.supersedes_revision_id == existing.revision_id:
                continue
            conflict = KnowledgeConflict(
                conflict_id=uuid4(),
                item_id=proposal.item_id,
                existing_revision_id=existing.revision_id,
                proposed_revision_id=proposal.revision_id,
                reason="proposed content conflicts with active approved revision",
                status=ConflictStatus.OPEN,
                resolution_revision_id=None,
                created_at=now,
                resolved_by=None,
                resolved_at=None,
            )
            self._conflicts[conflict.conflict_id] = conflict

    def _get(self, revision_id: UUID) -> KnowledgeRevision:
        try:
            return self._revisions[revision_id]
        except KeyError as exc:
            raise KnowledgePolicyError("unknown knowledge revision") from exc

    @staticmethod
    def _require_authority_kind(actor: Actor) -> None:
        _require_exact_actor(actor, "actor")
        if actor.kind not in {ActorKind.HUMAN, ActorKind.POLICY}:
            raise KnowledgePolicyError(
                "model actors cannot review or approve knowledge"
            )

    @staticmethod
    def _require_lifecycle_authority(
        revision: KnowledgeRevision,
        *,
        actor: Actor,
        policy: ApprovalPolicy,
    ) -> None:
        if not isinstance(policy, ApprovalPolicy):
            raise KnowledgePolicyError("policy must be an exact ApprovalPolicy")
        if not policy.permits(actor):
            raise KnowledgePolicyError("actor is not authorized by approval policy")
        related = actor.actor_id == revision.owner or actor in {
            revision.reviewed_by,
            revision.approved_by,
        }
        if actor.kind is not ActorKind.POLICY and not related:
            raise KnowledgePolicyError(
                "actor is not the owner or an approving authority"
            )


def _require_aware(value: datetime, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise KnowledgePolicyError(f"{label} must be timezone-aware")


def _require_exact_actor(value: object, label: str) -> None:
    if not isinstance(value, Actor):
        raise KnowledgePolicyError(f"{label} must be an exact Actor")


def _immutable_actor_ids(value: object, label: str) -> frozenset[str]:
    if isinstance(value, (str, bytes)):
        raise KnowledgePolicyError(f"{label} must be a collection of actor ids")
    if not isinstance(value, Iterable):
        raise KnowledgePolicyError(f"{label} must be a collection of actor ids")
    normalized: frozenset[object] = frozenset(value)
    if any(
        not isinstance(actor_id, str) or not actor_id.strip() for actor_id in normalized
    ):
        raise KnowledgePolicyError(f"{label} must contain non-empty strings")
    return cast(frozenset[str], normalized)


def _immutable_references(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise KnowledgePolicyError(f"{label} references must be a collection")
    if not isinstance(value, Iterable):
        raise KnowledgePolicyError(f"{label} references must be a collection")
    normalized: tuple[object, ...] = tuple(value)
    if any(
        not isinstance(reference, str) or not reference.strip()
        for reference in normalized
    ):
        raise KnowledgePolicyError(f"{label} references must be non-empty strings")
    return cast(tuple[str, ...], normalized)
