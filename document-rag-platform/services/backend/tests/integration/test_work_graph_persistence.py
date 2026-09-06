from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from src.application.work_graph import (
    EffectAlreadyStarted,
    EffectCapability,
    EffectRequest,
    IdempotencyConflict,
    ScopeViolation,
    ScopedMutationDispatcher,
    WorkGraphService,
)
from src.domain.clock import FixedClock
from src.domain.work_graph import DriftDetected, LeaseExpired, WorkStatus
from src.models import (
    ContextSource,
    Principal,
    Project,
    WorkAttempt,
    WorkClaim,
    WorkEvent,
    WorkItem,
    WorkReceipt,
    Workspace,
    WorkspaceMembership,
)
from src.domain.work_graph import ApprovalRequired


SCOPE = {
    "paths": ["evidence/marker.json"],
    "capabilities": [EffectCapability.WRITE_RECEIPT_MARKER.value],
}


def _request(content: bytes = b'{"status":"verified"}\n') -> EffectRequest:
    return EffectRequest(
        capability=EffectCapability.WRITE_RECEIPT_MARKER,
        relative_path="evidence/marker.json",
        content=content,
    )


def _service(
    db: Session,
    clock: FixedClock,
    root: Path,
    *,
    dispatcher: ScopedMutationDispatcher | None = None,
) -> tuple[WorkGraphService, WorkItem]:
    (root / "evidence").mkdir(parents=True, exist_ok=True)
    principal = Principal(subject=f"work-graph:{uuid.uuid4()}")
    workspace = Workspace(name=f"work-graph-{uuid.uuid4()}")
    db.add_all([principal, workspace])
    db.flush()
    project = Project(
        workspace_id=workspace.id,
        name=f"work-graph-{uuid.uuid4()}",
    )
    db.add(project)
    db.flush()
    service = WorkGraphService(db, clock, dispatcher or ScopedMutationDispatcher(root))
    item = service.create_work_item(
        workspace_id=workspace.id,
        project_id=project.id,
        title="Bounded mutation",
        objective="Prove claim-before-effect and receipt closure",
        scope=SCOPE,
        expected_revision="rev-before",
        created_by_principal_id=principal.id,
        acceptance_criteria=[{"id": "tests", "required": True}],
        evidence_requirements=["pytest"],
    )
    service.mark_ready(item.id, actor_id=str(principal.id))
    return service, item


@pytest.mark.integration
def test_fencing_idempotency_verify_and_terminal_receipt_are_authoritative(
    tmp_path: Path,
) -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    clock = FixedClock(datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc))
    try:
        service, item = _service(db, clock, tmp_path)
        prepared = service.prepare(item.id, observed_revision="rev-before")
        first = service.claim(
            item.id,
            prepared=prepared,
            claimant_id="cli-a",
            executor_id="codex-a",
            resource_scope=SCOPE,
            lease_duration=timedelta(seconds=5),
            idempotency_key="attempt-a",
        )
        replayed_first = service.claim(
            item.id,
            prepared=prepared,
            claimant_id="cli-a",
            executor_id="codex-a",
            resource_scope=SCOPE,
            lease_duration=timedelta(seconds=5),
            idempotency_key="attempt-a",
        )
        assert replayed_first == first
        assert (
            db.query(WorkEvent)
            .filter_by(work_item_id=item.id, event_type="claim.replayed")
            .count()
            == 1
        )
        with pytest.raises(IdempotencyConflict):
            service.claim(
                item.id,
                prepared=prepared,
                claimant_id="different-cli",
                executor_id="codex-a",
                resource_scope=SCOPE,
                lease_duration=timedelta(seconds=5),
                idempotency_key="attempt-a",
            )

        clock.current += timedelta(seconds=6)
        prepared_after_expiry = service.prepare(item.id, observed_revision="rev-before")
        second = service.claim(
            item.id,
            prepared=prepared_after_expiry,
            claimant_id="cli-b",
            executor_id="codex-b",
            resource_scope=SCOPE,
            lease_duration=timedelta(minutes=1),
            idempotency_key="attempt-b",
        )
        assert second.fencing_token > first.fencing_token
        assert db.get(WorkClaim, first.claim_id).status == "reconciled"
        assert db.get(WorkAttempt, first.attempt_id).status == "RECOVERY_REQUIRED"

        with pytest.raises(LeaseExpired):
            service.apply(
                item.id,
                claim_id=first.claim_id,
                fencing_token=first.fencing_token,
                observed_revision="rev-before",
                idempotency_key="apply-once",
                command="mutate",
                tool="fixture",
                action="write",
                signer_type="cli",
                signer_id="cli-a",
                effect_request=_request(),
            )
        assert not (tmp_path / "evidence/marker.json").exists()

        applied = service.apply(
            item.id,
            claim_id=second.claim_id,
            fencing_token=second.fencing_token,
            observed_revision="rev-before",
            idempotency_key="apply-once",
            command="mutate",
            tool="fixture",
            action="write",
            signer_type="cli",
            signer_id="cli-b",
            effect_request=_request(),
        )
        replayed = service.apply(
            item.id,
            claim_id=second.claim_id,
            fencing_token=second.fencing_token,
            observed_revision="rev-before",
            idempotency_key="apply-once",
            command="mutate",
            tool="fixture",
            action="write",
            signer_type="cli",
            signer_id="cli-b",
            effect_request=_request(),
        )
        assert (tmp_path / "evidence/marker.json").read_bytes() == _request().content
        assert replayed.replayed is True
        assert replayed.receipt_id == applied.receipt_id
        assert (
            db.query(WorkReceipt)
            .filter_by(work_item_id=item.id, receipt_type="apply")
            .count()
            == 1
        )
        with pytest.raises(IdempotencyConflict):
            service.apply(
                item.id,
                claim_id=second.claim_id,
                fencing_token=second.fencing_token,
                observed_revision="rev-before",
                idempotency_key="apply-once",
                command="mutate",
                tool="fixture",
                action="different-action",
                signer_type="cli",
                signer_id="cli-b",
                effect_request=_request(),
            )
        assert (
            db.query(WorkEvent)
            .filter_by(work_item_id=item.id, event_type="apply.replayed")
            .count()
            == 1
        )

        verified = service.verify(
            item.id,
            attempt_id=second.attempt_id,
            idempotency_key="verify-once",
            acceptance_evidence=["pytest:work-graph"],
            test_evidence_refs=["sha256:test-report"],
            signer_type="service",
            signer_id="verifier",
            observed_revision=applied.after_revision,
        )
        verified_replay = service.verify(
            item.id,
            attempt_id=second.attempt_id,
            idempotency_key="verify-once",
            acceptance_evidence=["pytest:work-graph"],
            test_evidence_refs=["sha256:test-report"],
            signer_type="service",
            signer_id="verifier",
            observed_revision=applied.after_revision,
        )
        assert verified_replay.receipt_id == verified.receipt_id
        assert verified_replay.replayed is True
        with pytest.raises(IdempotencyConflict):
            service.verify(
                item.id,
                attempt_id=second.attempt_id,
                idempotency_key="verify-once",
                acceptance_evidence=["different-evidence"],
                test_evidence_refs=["sha256:test-report"],
                signer_type="service",
                signer_id="verifier",
                observed_revision=applied.after_revision,
            )
        closed = service.close(
            item.id,
            attempt_id=second.attempt_id,
            verify_receipt_id=verified.receipt_id,
            idempotency_key="close-once",
            signer_type="service",
            signer_id="verifier",
            after_revision=applied.after_revision,
        )
        replayed_close = service.close(
            item.id,
            attempt_id=second.attempt_id,
            verify_receipt_id=verified.receipt_id,
            idempotency_key="close-once",
            signer_type="service",
            signer_id="verifier",
            after_revision=applied.after_revision,
        )
        db.refresh(item)
        assert item.status == WorkStatus.COMPLETED.value
        assert closed.is_terminal is True
        assert replayed_close.id == closed.id
        assert closed.parent_receipt_id == verified.receipt_id
        assert (
            db.get(WorkReceipt, verified.receipt_id).parent_receipt_id
            == applied.receipt_id
        )
        assert closed.attestation["verify_receipt_id"] == str(verified.receipt_id)
        with pytest.raises(IdempotencyConflict):
            service.close(
                item.id,
                attempt_id=second.attempt_id,
                verify_receipt_id=verified.receipt_id,
                idempotency_key="close-once",
                signer_type="service",
                signer_id="different-signer",
                after_revision=applied.after_revision,
            )
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_drift_and_stale_process_never_create_effect_or_fake_completion(
    tmp_path: Path,
) -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    clock = FixedClock(datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc))
    try:
        service, drifted = _service(db, clock, tmp_path)
        prepared = service.prepare(drifted.id, observed_revision="rev-before")
        claim = service.claim(
            drifted.id,
            prepared=prepared,
            claimant_id="cli",
            executor_id="codex",
            resource_scope=SCOPE,
            lease_duration=timedelta(minutes=1),
            idempotency_key="drift-attempt",
        )
        with pytest.raises(DriftDetected):
            service.apply(
                drifted.id,
                claim_id=claim.claim_id,
                fencing_token=claim.fencing_token,
                observed_revision="unexpected-revision",
                idempotency_key="must-not-run",
                command="mutate",
                tool="fixture",
                action="write",
                signer_type="cli",
                signer_id="cli",
                effect_request=_request(),
            )
        db.refresh(drifted)
        assert not (tmp_path / "evidence/marker.json").exists()
        assert drifted.status == WorkStatus.RECOVERY_REQUIRED.value

        service, abandoned = _service(db, clock, tmp_path)
        prepared = service.prepare(abandoned.id, observed_revision="rev-before")
        stale = service.claim(
            abandoned.id,
            prepared=prepared,
            claimant_id="dead-cli",
            executor_id="codex",
            resource_scope=SCOPE,
            lease_duration=timedelta(seconds=1),
            idempotency_key="dead-attempt",
        )
        clock.current += timedelta(seconds=2)
        assert service.reconcile_stale_leases() == [stale.claim_id]
        db.refresh(abandoned)
        assert abandoned.status == WorkStatus.CLAIMED.value
        assert db.get(WorkAttempt, stale.attempt_id).status == "RECOVERY_REQUIRED"
        assert db.get(WorkClaim, stale.claim_id).status == "reconciled"
        assert db.query(WorkReceipt).filter_by(work_item_id=abandoned.id).count() == 0
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_database_rejects_unreceipted_completion_and_mutating_ledgers(
    tmp_path: Path,
) -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    clock = FixedClock(datetime(2026, 9, 6, 14, 0, tzinfo=timezone.utc))
    try:
        service, item = _service(db, clock, tmp_path)
        prepared = service.prepare(item.id, observed_revision="rev-before")
        claim = service.claim(
            item.id,
            prepared=prepared,
            claimant_id="cli",
            executor_id="codex",
            resource_scope=SCOPE,
            lease_duration=timedelta(minutes=1),
            idempotency_key="db-trigger-attempt",
        )
        applied = service.apply(
            item.id,
            claim_id=claim.claim_id,
            fencing_token=claim.fencing_token,
            observed_revision="rev-before",
            idempotency_key="db-trigger-apply",
            command="mutate",
            tool="fixture",
            action="write",
            signer_type="cli",
            signer_id="cli",
            effect_request=_request(),
        )
        db.refresh(item)
        fake_receipt_sql = text(
            """
                INSERT INTO work_receipts(
                  id, work_item_id, attempt_id, claim_id, parent_receipt_id,
                  receipt_type, idempotency_key, request_hash, command, tool,
                  action, started_at, ended_at, exit_status,
                  input_artifact_hash, output_artifact_hash,
                  before_revision, after_revision, test_evidence_refs,
                  acceptance_evidence, signer_type, signer_id, attestation,
                  is_terminal, created_at
                ) VALUES (
                  :id, :work_item_id, :attempt_id, :claim_id, :parent_receipt_id,
                  :receipt_type, :idempotency_key, :request_hash, 'fake', 'fixture',
                  'verify', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0,
                  :input_hash, :output_hash, 'rev-before', :after_revision,
                  '["fake-test"]'::jsonb, '["fake-evidence"]'::jsonb,
                  'service', 'tamper-test', '{}'::jsonb, true, CURRENT_TIMESTAMP
                )
            """
        )

        def fake_receipt(receipt_type: str, idempotency_key: str):
            return db.execute(
                fake_receipt_sql,
                {
                    "id": uuid.uuid4(),
                    "work_item_id": item.id,
                    "attempt_id": claim.attempt_id,
                    "claim_id": claim.claim_id,
                    "parent_receipt_id": applied.receipt_id,
                    "receipt_type": receipt_type,
                    "idempotency_key": idempotency_key,
                    "request_hash": "1" * 64,
                    "input_hash": "2" * 64,
                    "output_hash": "3" * 64,
                    "after_revision": applied.after_revision,
                },
            )

        with pytest.raises(DatabaseError, match="terminal_close"):
            fake_receipt("verify", "fake-terminal-wrong-type")
        db.rollback()
        fake_receipt("close", "fake-terminal-wrong-parent")
        db.execute(
            text(
                """
                INSERT INTO work_events(
                  id, work_item_id, attempt_id, event_sequence, event_type,
                  actor_type, actor_id, reason, correlation_id,
                  previous_state, new_state, payload_schema_version,
                  payload_hash, payload_json, created_at
                ) VALUES (
                  :id, :work_item_id, :attempt_id, :event_sequence,
                  'work.transition', 'service', 'tamper-test', 'invalid close',
                  :correlation_id, 'VERIFYING', 'COMPLETED', 'work-event-v1',
                  :payload_hash, '{}'::jsonb, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "work_item_id": item.id,
                "attempt_id": claim.attempt_id,
                "event_sequence": item.revision + 1,
                "correlation_id": uuid.uuid4(),
                "payload_hash": "0" * 64,
            },
        )
        with pytest.raises(DatabaseError, match="receipt chain"):
            db.execute(
                text("UPDATE work_items SET status='COMPLETED' WHERE id=:id"),
                {"id": item.id},
            )
        db.rollback()

        with pytest.raises(DatabaseError, match="event binding"):
            db.execute(
                text("UPDATE work_items SET status='COMPLETED' WHERE id=:id"),
                {"id": item.id},
            )
        db.rollback()

        event_id = db.query(WorkEvent.id).filter_by(work_item_id=item.id).first()[0]
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(
                text("UPDATE work_events SET reason='tampered' WHERE id=:id"),
                {"id": event_id},
            )
        db.rollback()
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_concurrent_claims_serialize_on_the_work_item_row(tmp_path: Path) -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    setup = Session(bind=engine)
    clock = FixedClock(datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc))
    service, item = _service(setup, clock, tmp_path)
    prepared = service.prepare(item.id, observed_revision="rev-before")
    item_id = item.id

    def compete(index: int):
        session = Session(bind=engine)
        try:
            return WorkGraphService(session, clock).claim(
                item_id,
                prepared=prepared,
                claimant_id=f"cli-{index}",
                executor_id=f"codex-{index}",
                resource_scope=SCOPE,
                lease_duration=timedelta(minutes=1),
                idempotency_key=f"concurrent-{index}",
            )
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(compete, index) for index in (1, 2)]
        outcomes: list[object] = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except DriftDetected as exc:
                outcomes.append(exc)

        assert sum(not isinstance(value, Exception) for value in outcomes) == 1
        assert sum(isinstance(value, DriftDetected) for value in outcomes) == 1
        setup.expire_all()
        claims = setup.query(WorkClaim).filter_by(work_item_id=item_id).all()
        assert len(claims) == 1
        assert claims[0].status == "active"
        assert (
            setup.get(WorkItem, item_id).active_fencing_token == claims[0].fencing_token
        )
        claims[0].status = "released"
        claims[0].released_reason = "integration_test_finished"
        claims[0].released_at = clock.now()
        setup.commit()
    finally:
        setup.rollback()
        setup.close()
        engine.dispose()


@pytest.mark.integration
def test_process_death_after_effect_intent_cannot_repeat_the_effect(
    tmp_path: Path,
) -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    clock = FixedClock(datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc))
    effects: list[str] = []

    class CrashingDispatcher(ScopedMutationDispatcher):
        def dispatch(self, context, request):
            effects.append("started")
            raise KeyboardInterrupt

    try:
        service, item = _service(
            db,
            clock,
            tmp_path,
            dispatcher=CrashingDispatcher(tmp_path),
        )
        prepared = service.prepare(item.id, observed_revision="rev-before")
        claim = service.claim(
            item.id,
            prepared=prepared,
            claimant_id="crashing-cli",
            executor_id="codex",
            resource_scope=SCOPE,
            lease_duration=timedelta(seconds=5),
            idempotency_key="crash-attempt",
        )

        with pytest.raises(KeyboardInterrupt):
            service.apply(
                item.id,
                claim_id=claim.claim_id,
                fencing_token=claim.fencing_token,
                observed_revision="rev-before",
                idempotency_key="crash-effect-once",
                command="mutate",
                tool="fixture",
                action="write",
                signer_type="cli",
                signer_id="crashing-cli",
                effect_request=_request(),
            )
        with pytest.raises(EffectAlreadyStarted):
            service.apply(
                item.id,
                claim_id=claim.claim_id,
                fencing_token=claim.fencing_token,
                observed_revision="rev-before",
                idempotency_key="crash-effect-once",
                command="mutate",
                tool="fixture",
                action="write",
                signer_type="cli",
                signer_id="replacement-cli",
                effect_request=_request(),
            )
        assert effects == ["started"]
        clock.current += timedelta(seconds=6)
        assert claim.claim_id in service.reconcile_stale_leases()
        db.refresh(item)
        assert item.status == WorkStatus.BLOCKED.value
        assert db.query(WorkReceipt).filter_by(work_item_id=item.id).count() == 0
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_typed_marker_mutation_has_exact_rollback_cleanup_receipt(
    tmp_path: Path,
) -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    clock = FixedClock(datetime(2026, 9, 6, 17, 0, tzinfo=timezone.utc))
    try:
        service, item = _service(db, clock, tmp_path)
        prepared = service.prepare(item.id, observed_revision="rev-before")
        claim = service.claim(
            item.id,
            prepared=prepared,
            claimant_id="rollback-cli",
            executor_id="codex",
            resource_scope=SCOPE,
            lease_duration=timedelta(minutes=1),
            idempotency_key="rollback-attempt",
        )
        with pytest.raises(ScopeViolation):
            service.apply(
                item.id,
                claim_id=claim.claim_id,
                fencing_token=claim.fencing_token,
                observed_revision="rev-before",
                idempotency_key="escaped-apply",
                command="escape",
                tool="scoped-dispatcher",
                action="write_receipt_marker",
                signer_type="cli",
                signer_id="rollback-cli",
                effect_request=EffectRequest(
                    capability=EffectCapability.WRITE_RECEIPT_MARKER,
                    relative_path="../escaped-marker",
                    content=b"blocked",
                ),
            )
        db.refresh(item)
        assert item.status == WorkStatus.CLAIMED.value
        assert db.get(WorkAttempt, claim.attempt_id).effect_started_at is None
        applied = service.apply(
            item.id,
            claim_id=claim.claim_id,
            fencing_token=claim.fencing_token,
            observed_revision="rev-before",
            idempotency_key="rollback-apply",
            command="write marker",
            tool="scoped-dispatcher",
            action="write_receipt_marker",
            signer_type="cli",
            signer_id="rollback-cli",
            effect_request=_request(),
        )
        marker = tmp_path / "evidence/marker.json"
        assert marker.is_file()
        persisted_claim = db.get(WorkClaim, claim.claim_id)
        assert persisted_claim.status == "active"
        persisted_claim.status = "released"
        persisted_claim.released_reason = "simulated_stale_authority"
        persisted_claim.released_at = clock.now()
        db.commit()
        with pytest.raises(LeaseExpired):
            service.rollback(
                item.id,
                attempt_id=claim.attempt_id,
                claim_id=claim.claim_id,
                fencing_token=claim.fencing_token,
                apply_receipt_id=applied.receipt_id,
                idempotency_key="stale-rollback-cleanup",
                signer_type="service",
                signer_id="rollback-verifier",
                effect_request=_request(),
            )
        assert marker.is_file()
        persisted_claim.status = "active"
        persisted_claim.released_reason = None
        persisted_claim.released_at = None
        db.commit()
        with pytest.raises(LeaseExpired):
            service.rollback(
                item.id,
                attempt_id=claim.attempt_id,
                claim_id=claim.claim_id,
                fencing_token=claim.fencing_token + 1,
                apply_receipt_id=applied.receipt_id,
                idempotency_key="wrong-fence-rollback-cleanup",
                signer_type="service",
                signer_id="rollback-verifier",
                effect_request=_request(),
            )
        assert marker.is_file()
        rollback = service.rollback(
            item.id,
            attempt_id=claim.attempt_id,
            claim_id=claim.claim_id,
            fencing_token=claim.fencing_token,
            apply_receipt_id=applied.receipt_id,
            idempotency_key="rollback-cleanup",
            signer_type="service",
            signer_id="rollback-verifier",
            effect_request=_request(),
        )
        replay = service.rollback(
            item.id,
            attempt_id=claim.attempt_id,
            claim_id=claim.claim_id,
            fencing_token=claim.fencing_token,
            apply_receipt_id=applied.receipt_id,
            idempotency_key="rollback-cleanup",
            signer_type="service",
            signer_id="rollback-verifier",
            effect_request=_request(),
        )
        db.refresh(item)
        assert not marker.exists()
        assert rollback.receipt_type == "rollback"
        assert rollback.parent_receipt_id == applied.receipt_id
        assert rollback.input_artifact_hash == applied.output_artifact_hash
        assert rollback.rollback_result == {"status": "completed", "action": "unlink"}
        assert replay.id == rollback.id
        assert item.status == WorkStatus.RECOVERY_REQUIRED.value
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_work_approval_requires_authenticated_registered_project_authority(
    tmp_path: Path,
) -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    clock = FixedClock(datetime(2026, 9, 6, 17, 30, tzinfo=timezone.utc))
    try:
        service, item = _service(db, clock, tmp_path)
        item.required_approvals = ["destructive", "release"]
        principal = db.get(Principal, item.created_by_principal_id)
        assert principal is not None
        db.add(
            WorkspaceMembership(
                workspace_id=item.workspace_id,
                principal_id=principal.id,
                role="admin",
            )
        )
        db.add(
            ContextSource(
                project_id=item.project_id,
                source_id=f"project-manifest:{item.project_id}",
                version=1,
                content_hash="a" * 64,
                load_tier="MUST_LOAD",
                classification="INTERNAL",
                status="ACTIVE",
                provider_policy={},
                token_cost=0,
                metadata_json={
                    "approval_policy": {
                        "owner": principal.subject,
                        "approvers": [principal.subject, "policy:release-gate"],
                        "human_required_for": ["destructive"],
                    }
                },
            )
        )
        db.commit()
        prepared = service.prepare(item.id, observed_revision="rev-before")

        with pytest.raises(ApprovalRequired, match="authenticated"):
            service.grant_approval(
                item.id,
                approval_type="destructive",
                scope_hash=prepared.scope_hash,
                actor_type="human",
                actor_id=principal.subject,
                reason="reviewed",
                evidence_refs=["receipt://review/1"],
            )

        authenticated = WorkGraphService(
            db,
            clock,
            ScopedMutationDispatcher(tmp_path),
            authenticated_principal_id=principal.id,
        )
        with pytest.raises(ApprovalRequired, match="authenticated project"):
            authenticated.grant_approval(
                item.id,
                approval_type="destructive",
                scope_hash=prepared.scope_hash,
                actor_type="human",
                actor_id="spoofed-owner",
                reason="reviewed",
                evidence_refs=["receipt://review/1"],
            )
        with pytest.raises(ApprovalRequired, match="scope"):
            authenticated.grant_approval(
                item.id,
                approval_type="destructive",
                scope_hash="0" * 64,
                actor_type="human",
                actor_id=principal.subject,
                reason="reviewed",
                evidence_refs=["receipt://review/1"],
            )

        approval = authenticated.grant_approval(
            item.id,
            approval_type="destructive",
            scope_hash=prepared.scope_hash,
            actor_type="human",
            actor_id=principal.subject,
            reason="reviewed",
            evidence_refs=["receipt://review/1"],
        )
        assert approval.actor_id == principal.subject
        assert approval.scope_hash == prepared.scope_hash

        with pytest.raises(ApprovalRequired, match="trusted"):
            authenticated.grant_approval(
                item.id,
                approval_type="release",
                scope_hash=prepared.scope_hash,
                actor_type="policy",
                actor_id="release-gate",
                reason="automated release checks passed",
                evidence_refs=["receipt://policy/release/1"],
            )
        trusted_policy = WorkGraphService(
            db,
            clock,
            ScopedMutationDispatcher(tmp_path),
            trusted_policy_ids=frozenset({"release-gate"}),
        )
        policy_approval = trusted_policy.grant_approval(
            item.id,
            approval_type="release",
            scope_hash=prepared.scope_hash,
            actor_type="policy",
            actor_id="release-gate",
            reason="automated release checks passed",
            evidence_refs=["receipt://policy/release/1"],
        )
        assert policy_approval.actor_type == "policy"
        with pytest.raises(ApprovalRequired, match="trusted"):
            trusted_policy.grant_approval(
                item.id,
                approval_type="destructive",
                scope_hash=prepared.scope_hash,
                actor_type="policy",
                actor_id="release-gate",
                reason="must remain human-gated",
                evidence_refs=["receipt://policy/destructive/1"],
            )
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_process_death_after_rollback_intent_cannot_repeat_unlink(
    tmp_path: Path,
) -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    clock = FixedClock(datetime(2026, 9, 6, 18, 0, tzinfo=timezone.utc))
    rollbacks: list[str] = []

    class CrashingRollbackDispatcher(ScopedMutationDispatcher):
        def rollback(self, context, request, *, expected_after_hash):
            rollbacks.append("started")
            super().rollback(
                context,
                request,
                expected_after_hash=expected_after_hash,
            )
            raise KeyboardInterrupt

    try:
        service, item = _service(
            db,
            clock,
            tmp_path,
            dispatcher=CrashingRollbackDispatcher(tmp_path),
        )
        prepared = service.prepare(item.id, observed_revision="rev-before")
        claim = service.claim(
            item.id,
            prepared=prepared,
            claimant_id="rollback-crash-cli",
            executor_id="codex",
            resource_scope=SCOPE,
            lease_duration=timedelta(minutes=1),
            idempotency_key="rollback-crash-attempt",
        )
        applied = service.apply(
            item.id,
            claim_id=claim.claim_id,
            fencing_token=claim.fencing_token,
            observed_revision="rev-before",
            idempotency_key="rollback-crash-apply",
            command="write marker",
            tool="scoped-dispatcher",
            action="write_receipt_marker",
            signer_type="cli",
            signer_id="rollback-crash-cli",
            effect_request=_request(),
        )

        with pytest.raises(KeyboardInterrupt):
            service.rollback(
                item.id,
                attempt_id=claim.attempt_id,
                claim_id=claim.claim_id,
                fencing_token=claim.fencing_token,
                apply_receipt_id=applied.receipt_id,
                idempotency_key="rollback-crash-cleanup",
                signer_type="service",
                signer_id="rollback-verifier",
                effect_request=_request(),
            )
        assert not (tmp_path / "evidence/marker.json").exists()
        with pytest.raises(EffectAlreadyStarted):
            service.rollback(
                item.id,
                attempt_id=claim.attempt_id,
                claim_id=claim.claim_id,
                fencing_token=claim.fencing_token,
                apply_receipt_id=applied.receipt_id,
                idempotency_key="rollback-crash-cleanup",
                signer_type="service",
                signer_id="rollback-verifier",
                effect_request=_request(),
            )
        assert rollbacks == ["started"]
        persisted_attempt = db.get(WorkAttempt, claim.attempt_id)
        assert persisted_attempt.rollback_started_at is not None
        assert persisted_attempt.rollback_request_hash is not None
        assert (
            db.query(WorkReceipt)
            .filter_by(work_item_id=item.id, receipt_type="rollback")
            .count()
            == 0
        )
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()
