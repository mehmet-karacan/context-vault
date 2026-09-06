from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import time
from uuid import UUID, uuid4

import pytest

from src.context_vault.context_compiler import DataClassification
from src.context_vault.knowledge import (
    Actor,
    ActorKind,
    ApprovalPolicy,
    ConflictStatus,
    KnowledgeLedger,
    KnowledgePolicyError,
    KnowledgeStatus,
)


NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
MODEL = Actor(ActorKind.MODEL, "local-qwen")
AUTHOR = Actor(ActorKind.HUMAN, "author")
REVIEWER = Actor(ActorKind.HUMAN, "reviewer")
APPROVER = Actor(ActorKind.HUMAN, "approver")
OUTSIDER = Actor(ActorKind.HUMAN, "outsider")
POLICY = ApprovalPolicy(authorized_humans=frozenset({"approver"}))
BROAD_POLICY = ApprovalPolicy(authorized_humans=frozenset({"approver", "outsider"}))


def _propose(
    ledger: KnowledgeLedger,
    *,
    item_id: UUID | None = None,
    content: str = "PostgreSQL Work Graph is authoritative.",
    proposed_by: Actor = MODEL,
    supersedes_revision_id: UUID | None = None,
):
    return ledger.propose(
        item_id=item_id or uuid4(),
        content=content,
        source_ids=("adr:work-graph",),
        evidence_ids=("receipt:test-001",),
        owner="platform",
        scope="project",
        confidence=0.98,
        classification=DataClassification.INTERNAL,
        proposed_by=proposed_by,
        valid_from=NOW,
        supersedes_revision_id=supersedes_revision_id,
        now=NOW,
    )


def _approve(ledger: KnowledgeLedger, revision_id: UUID):
    ledger.review(revision_id, reviewer=REVIEWER)
    return ledger.approve(revision_id, approver=APPROVER, policy=POLICY)


def test_model_extraction_can_only_propose_and_cannot_self_approve() -> None:
    ledger = KnowledgeLedger()
    proposed = _propose(ledger)

    assert proposed.status is KnowledgeStatus.PROPOSED
    with pytest.raises(KnowledgePolicyError, match="model"):
        ledger.review(proposed.revision_id, reviewer=MODEL)

    ledger.review(proposed.revision_id, reviewer=REVIEWER)
    with pytest.raises(KnowledgePolicyError, match="model"):
        ledger.approve(proposed.revision_id, approver=MODEL, policy=POLICY)


def test_approval_is_authorized_independent_and_versioned() -> None:
    ledger = KnowledgeLedger()
    proposed = _propose(ledger, proposed_by=AUTHOR)

    with pytest.raises(KnowledgePolicyError, match="own"):
        ledger.review(proposed.revision_id, reviewer=AUTHOR)
    reviewed = ledger.review(proposed.revision_id, reviewer=REVIEWER)
    assert reviewed.status is KnowledgeStatus.REVIEWED
    with pytest.raises(KnowledgePolicyError, match="authorized"):
        ledger.approve(proposed.revision_id, approver=AUTHOR, policy=POLICY)

    approved = ledger.approve(proposed.revision_id, approver=APPROVER, policy=POLICY)
    assert approved.status is KnowledgeStatus.APPROVED
    assert approved.version == 1
    assert approved.source_ids and approved.evidence_ids
    assert approved.owner == "platform"
    assert approved.scope == "project"
    assert approved.confidence == 0.98


def test_approved_replacement_supersedes_prior_revision_without_overwrite() -> None:
    ledger = KnowledgeLedger()
    item_id = uuid4()
    first = _propose(ledger, item_id=item_id, content="version one")
    _approve(ledger, first.revision_id)
    second = _propose(
        ledger,
        item_id=item_id,
        content="version two",
        supersedes_revision_id=first.revision_id,
    )
    approved_second = _approve(ledger, second.revision_id)

    by_id = {revision.revision_id: revision for revision in ledger.revisions}
    assert by_id[first.revision_id].status is KnowledgeStatus.SUPERSEDED
    assert by_id[first.revision_id].content == "version one"
    assert by_id[first.revision_id].terminal_by == APPROVER
    assert by_id[first.revision_id].terminal_at is not None
    assert approved_second.status is KnowledgeStatus.APPROVED
    assert approved_second.version == 2
    assert [item.content for item in ledger.approved_projection(at=NOW)] == [
        "version two"
    ]


def test_conflicting_information_opens_record_and_cannot_be_approved() -> None:
    ledger = KnowledgeLedger()
    item_id = uuid4()
    first = _propose(ledger, item_id=item_id, content="region is eu")
    _approve(ledger, first.revision_id)
    conflicting = _propose(ledger, item_id=item_id, content="region is us")

    assert len(ledger.conflicts) == 1
    assert ledger.conflicts[0].status is ConflictStatus.OPEN
    ledger.review(conflicting.revision_id, reviewer=REVIEWER)
    with pytest.raises(KnowledgePolicyError, match="conflicting"):
        ledger.approve(conflicting.revision_id, approver=APPROVER, policy=POLICY)
    assert {revision.content for revision in ledger.revisions} == {
        "region is eu",
        "region is us",
    }


def test_markdown_projection_edit_reenters_lifecycle_as_proposed() -> None:
    ledger = KnowledgeLedger()
    revision = ledger.propose_projection_edit(
        item_id=uuid4(),
        content="edited in Obsidian",
        source_ids=("projection:obsidian",),
        evidence_ids=("artifact:projection-diff",),
        owner="platform",
        scope="project",
        confidence=0.5,
        classification=DataClassification.INTERNAL,
        proposed_by=AUTHOR,
        valid_from=NOW,
        now=NOW,
    )

    assert revision.status is KnowledgeStatus.PROPOSED
    assert ledger.approved_projection(at=NOW) == ()


def test_expiry_revoke_and_validity_interval_remove_projection_authority() -> None:
    ledger = KnowledgeLedger()
    expiring = ledger.propose(
        item_id=uuid4(),
        content="temporary fact",
        source_ids=("source:1",),
        evidence_ids=("evidence:1",),
        owner="owner",
        scope="project",
        confidence=0.8,
        classification=DataClassification.CONFIDENTIAL,
        proposed_by=MODEL,
        valid_from=NOW,
        valid_until=NOW + timedelta(hours=1),
        now=NOW,
    )
    _approve(ledger, expiring.revision_id)
    assert ledger.approved_projection(at=NOW + timedelta(minutes=30))
    assert ledger.approved_projection(at=NOW + timedelta(hours=2)) == ()

    ledger.expire(expiring.revision_id, actor=APPROVER, policy=POLICY, now=NOW)
    assert ledger.approved_projection(at=NOW) == ()

    revocable = _propose(ledger)
    _approve(ledger, revocable.revision_id)
    ledger.revoke(revocable.revision_id, actor=APPROVER, policy=POLICY, now=NOW)
    assert ledger.approved_projection(at=NOW) == ()


def test_revision_requires_evidence_and_timezone_aware_validity() -> None:
    ledger = KnowledgeLedger()
    with pytest.raises(KnowledgePolicyError, match="source and evidence"):
        ledger.propose(
            item_id=uuid4(),
            content="no evidence",
            source_ids=(),
            evidence_ids=(),
            owner="owner",
            scope="project",
            confidence=0.5,
            classification=DataClassification.PUBLIC,
            proposed_by=MODEL,
            valid_from=NOW,
            now=NOW,
        )
    with pytest.raises(KnowledgePolicyError, match="timezone-aware"):
        _propose(ledger, item_id=uuid4()).is_valid_at(datetime(2026, 9, 6))


@pytest.mark.parametrize(
    ("source_ids", "evidence_ids"),
    [(("",), ("evidence",)), (("source",), ("  ",))],
)
def test_revision_rejects_blank_source_or_evidence_references(
    source_ids, evidence_ids
) -> None:
    ledger = KnowledgeLedger()
    with pytest.raises(KnowledgePolicyError, match="non-empty"):
        ledger.propose(
            item_id=uuid4(),
            content="fact",
            source_ids=source_ids,
            evidence_ids=evidence_ids,
            owner="owner",
            scope="project",
            confidence=0.5,
            classification=DataClassification.PUBLIC,
            proposed_by=MODEL,
            valid_from=NOW,
            now=NOW,
        )


def test_two_replacements_cannot_both_become_active_heads() -> None:
    ledger = KnowledgeLedger()
    item_id = uuid4()
    predecessor = _propose(ledger, item_id=item_id, content="v1")
    _approve(ledger, predecessor.revision_id)
    first = _propose(
        ledger,
        item_id=item_id,
        content="v2-a",
        supersedes_revision_id=predecessor.revision_id,
    )
    second = _propose(
        ledger,
        item_id=item_id,
        content="v2-b",
        supersedes_revision_id=predecessor.revision_id,
    )
    ledger.review(first.revision_id, reviewer=REVIEWER)
    ledger.review(second.revision_id, reviewer=REVIEWER)
    ledger.approve(first.revision_id, approver=APPROVER, policy=POLICY, now=NOW)

    with pytest.raises(KnowledgePolicyError, match="stale predecessor"):
        ledger.approve(second.revision_id, approver=APPROVER, policy=POLICY, now=NOW)
    assert [revision.content for revision in ledger.approved_projection(at=NOW)] == [
        "v2-a"
    ]


def test_concurrent_replacement_approval_has_exactly_one_winner() -> None:
    ledger = KnowledgeLedger()
    item_id = uuid4()
    predecessor = _propose(ledger, item_id=item_id, content="v1")
    _approve(ledger, predecessor.revision_id)
    replacements = tuple(
        _propose(
            ledger,
            item_id=item_id,
            content=f"v2-{suffix}",
            supersedes_revision_id=predecessor.revision_id,
        )
        for suffix in ("a", "b")
    )
    for revision in replacements:
        ledger.review(revision.revision_id, reviewer=REVIEWER)

    def attempt(revision_id: UUID) -> str:
        try:
            ledger.approve(revision_id, approver=APPROVER, policy=POLICY, now=NOW)
        except KnowledgePolicyError:
            return "rejected"
        return "approved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            executor.map(attempt, (item.revision_id for item in replacements))
        )

    assert sorted(outcomes) == ["approved", "rejected"]
    assert len(ledger.approved_projection(at=NOW)) == 1


def test_concurrent_initial_proposals_cannot_create_two_heads() -> None:
    ledger = KnowledgeLedger()
    item_id = uuid4()
    proposals = tuple(
        _propose(ledger, item_id=item_id, content=f"candidate-{suffix}")
        for suffix in ("a", "b")
    )
    for revision in proposals:
        ledger.review(revision.revision_id, reviewer=REVIEWER)

    ledger.approve(proposals[0].revision_id, approver=APPROVER, policy=POLICY, now=NOW)
    with pytest.raises(KnowledgePolicyError, match="stale predecessor"):
        ledger.approve(
            proposals[1].revision_id,
            approver=APPROVER,
            policy=POLICY,
            now=NOW,
        )
    assert len(ledger.approved_projection(at=NOW)) == 1


def test_concurrent_proposals_receive_unique_monotonic_versions() -> None:
    class SlowGetDict(dict):
        def get(self, key, default=None):
            result = super().get(key, default)
            time.sleep(0.02)
            return result

    ledger = KnowledgeLedger()
    ledger._item_revisions = SlowGetDict()  # type: ignore[attr-defined]
    item_id = uuid4()

    with ThreadPoolExecutor(max_workers=2) as executor:
        revisions = tuple(
            executor.map(
                lambda suffix: _propose(
                    ledger,
                    item_id=item_id,
                    content=f"candidate-{suffix}",
                ),
                ("a", "b"),
            )
        )

    assert sorted(revision.version for revision in revisions) == [1, 2]


def test_terminal_transitions_require_policy_authority_and_record_actor_time() -> None:
    ledger = KnowledgeLedger()
    revision = _propose(ledger)
    _approve(ledger, revision.revision_id)

    with pytest.raises(KnowledgePolicyError, match="authorized"):
        ledger.revoke(
            revision.revision_id,
            actor=OUTSIDER,
            policy=POLICY,
            now=NOW,
        )
    with pytest.raises(KnowledgePolicyError, match="owner or an approving"):
        ledger.revoke(
            revision.revision_id,
            actor=OUTSIDER,
            policy=BROAD_POLICY,
            now=NOW,
        )
    revoked = ledger.revoke(
        revision.revision_id,
        actor=APPROVER,
        policy=POLICY,
        now=NOW,
    )
    assert revoked.terminal_by == APPROVER
    assert revoked.terminal_at == NOW


def test_conflict_resolution_requires_authorized_related_actor() -> None:
    ledger = KnowledgeLedger()
    item_id = uuid4()
    predecessor = _propose(ledger, item_id=item_id, content="eu")
    _approve(ledger, predecessor.revision_id)
    _propose(ledger, item_id=item_id, content="us")
    resolution = _propose(
        ledger,
        item_id=item_id,
        content="eu-primary",
        supersedes_revision_id=predecessor.revision_id,
    )

    with pytest.raises(KnowledgePolicyError, match="authorized"):
        ledger.resolve_conflict(
            ledger.conflicts[0].conflict_id,
            resolution_revision_id=resolution.revision_id,
            resolver=OUTSIDER,
            policy=POLICY,
            now=NOW,
        )
    with pytest.raises(KnowledgePolicyError, match="owner or an approving"):
        ledger.resolve_conflict(
            ledger.conflicts[0].conflict_id,
            resolution_revision_id=resolution.revision_id,
            resolver=OUTSIDER,
            policy=BROAD_POLICY,
            now=NOW,
        )

    resolved = ledger.resolve_conflict(
        ledger.conflicts[0].conflict_id,
        resolution_revision_id=resolution.revision_id,
        resolver=APPROVER,
        policy=POLICY,
        now=NOW,
    )
    assert resolved.status is ConflictStatus.RESOLVED
    assert resolved.resolved_by == APPROVER
    assert resolved.resolved_at == NOW


def test_mutable_provenance_inputs_are_copied_to_immutable_tuples() -> None:
    sources = ["source:1"]
    evidence = ["evidence:1"]
    ledger = KnowledgeLedger()
    revision = ledger.propose(
        item_id=uuid4(),
        content="immutable provenance",
        source_ids=sources,  # type: ignore[arg-type]
        evidence_ids=evidence,  # type: ignore[arg-type]
        owner="platform",
        scope="project",
        confidence=0.9,
        classification=DataClassification.INTERNAL,
        proposed_by=MODEL,
        valid_from=NOW,
        now=NOW,
    )
    sources[0] = "attacker:changed"
    evidence.append("attacker:added")

    assert revision.source_ids == ("source:1",)
    assert revision.evidence_ids == ("evidence:1",)


def test_actor_revision_and_policy_reject_type_confusion_and_mutable_sets() -> None:
    with pytest.raises(KnowledgePolicyError, match="ActorKind"):
        Actor("HUMAN", "user")  # type: ignore[arg-type]

    humans = {"approver"}
    policy = ApprovalPolicy(
        authorized_humans=humans,  # type: ignore[arg-type]
        require_independent_approval=True,
    )
    humans.clear()
    assert policy.authorized_humans == frozenset({"approver"})
    assert policy.permits(APPROVER)
    with pytest.raises(KnowledgePolicyError, match="boolean"):
        ApprovalPolicy(
            authorized_humans=frozenset({"approver"}),
            require_independent_approval=1,  # type: ignore[arg-type]
        )

    proposed = _propose(KnowledgeLedger())
    with pytest.raises(KnowledgePolicyError, match="DataClassification"):
        replace(proposed, classification="INTERNAL")  # type: ignore[arg-type]
    with pytest.raises(KnowledgePolicyError, match="KnowledgeStatus"):
        replace(proposed, status="PROPOSED")  # type: ignore[arg-type]


def test_direct_terminal_and_conflict_construction_rejects_model_actor() -> None:
    ledger = KnowledgeLedger()
    item_id = uuid4()
    predecessor = _propose(ledger, item_id=item_id, content="eu")
    approved = _approve(ledger, predecessor.revision_id)
    with pytest.raises(KnowledgePolicyError, match="model"):
        replace(
            approved,
            status=KnowledgeStatus.REVOKED,
            terminal_by=MODEL,
            terminal_at=NOW,
        )

    conflicting = _propose(ledger, item_id=item_id, content="us")
    conflict = ledger.conflicts[0]
    with pytest.raises(KnowledgePolicyError, match="model"):
        replace(
            conflict,
            status=ConflictStatus.RESOLVED,
            resolution_revision_id=conflicting.revision_id,
            resolved_by=MODEL,
            resolved_at=NOW,
        )
