from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.application.ingestion_maintenance import (
    reconcile_stale_leases,
    run_storage_gc,
    sweep_orphan_staging,
)
from src.application.ingestion_bundle import BUNDLE_MIME, build_scan_bundle
from src.application.ingestion_orchestrator import (
    AcceptSourceCommand,
    IngestionOrchestrator,
    OutboxDispatcher,
)
from src.api.v1.ingestion_jobs import cancel_ingestion_job
from src.api.v1.documents import delete_document
from src.domain.clock import FixedClock
from src.domain.identity import PrincipalContext
from src.domain.ingestion import SourceDescriptor
from src.infrastructure.chunkers.base import ChunkCandidate
from src.infrastructure.repositories.scan_result import ScanResult, ScannedFile
from src.models import (
    AuditEvent,
    Chunk,
    Conversation,
    Document,
    DocumentArtifact,
    DocumentVersion,
    InboxReceipt,
    IngestionAttempt,
    IngestionJob,
    IngestionReceipt,
    Message,
    MessageCitation,
    OutboxEvent,
    Principal,
    Project,
    StorageGcReceipt,
    StorageObject,
    SourceFile,
    Workspace,
    WorkspaceMembership,
)
from src.workers.ingestion_tasks import RetryableIngestionError


class MemoryStorage:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.modified: dict[str, datetime] = {}

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        del content_type
        self.data[key] = data
        self.modified[key] = datetime.now(timezone.utc)
        return key

    def get(self, key: str) -> bytes:
        return self.data[key]

    def delete(self, key: str) -> None:
        self.data.pop(key, None)
        self.modified.pop(key, None)

    def list_keys(self, prefix: str = "") -> list[str]:
        return sorted(key for key in self.data if key.startswith(prefix))

    def list_entries(self, prefix: str = "") -> list[tuple[str, datetime]]:
        return [(key, self.modified[key]) for key in self.list_keys(prefix)]


def _descriptor(
    content: bytes, *, classification: str = "internal"
) -> SourceDescriptor:
    import hashlib

    return SourceDescriptor(
        source_type="document",
        origin="integration.txt",
        revision=None,
        content_length=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
        detected_mime="text/plain",
        declared_mime="text/plain",
        data_classification_hint=classification,
    )


def _candidate(text: str) -> ChunkCandidate:
    return ChunkCandidate(
        chunk_id=str(uuid.uuid4()),
        source_id="integration",
        chunk_type="document",
        content=text,
        embedding_text=text,
        locator={"line_start": 1, "line_end": 1},
    )


@pytest.mark.integration
def test_accept_dispatch_process_and_redelivery_are_idempotent() -> None:
    import os

    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    storage = MemoryStorage()
    now = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)
    clock = FixedClock(now)
    principal_id, workspace_id, project_id = (uuid.uuid4() for _ in range(3))
    published: list[tuple[str, str]] = []

    try:
        principal = Principal(id=principal_id, subject=f"a6:{principal_id}")
        workspace = Workspace(id=workspace_id, name=f"A6 {workspace_id}")
        project = Project(id=project_id, workspace_id=workspace_id, name="A6")
        db.add_all([principal, workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace_id,
                    principal_id=principal_id,
                    role="admin",
                ),
                project,
            ]
        )
        db.commit()

        content = b"canonical ingestion integration payload"
        command = AcceptSourceCommand(
            project=project,
            filename="integration.txt",
            content=content,
            descriptor=_descriptor(content),
            idempotency_key=f"upload:{uuid.uuid4()}",
            actor_principal_id=principal_id,
            workspace_id=workspace_id,
        )
        accepted = IngestionOrchestrator(
            db,
            storage,
            clock=clock,
            publisher=lambda job_id, key: published.append((job_id, key)),
        ).accept_source(command)
        replay = IngestionOrchestrator(db, storage, clock=clock).accept_source(command)

        assert accepted.status == "queued"
        assert replay.replayed is True
        assert replay.job_id == accepted.job_id
        assert db.query(IngestionJob).filter_by(id=accepted.job_id).count() == 1
        assert db.query(DocumentVersion).filter_by(id=accepted.version_id).count() == 1
        assert len(published) == 1
        assert db.get(OutboxEvent, accepted.outbox_event_id).status == "published"

        result = IngestionOrchestrator(db, storage, clock=clock).process_job(
            accepted.job_id,
            extract_text_fn=lambda _path, _name: "normalized integration payload",
            chunk_text_fn=lambda _source, **_kwargs: [_candidate("one chunk")],
            embed_texts_fn=lambda texts, instruction="": [[0.01] * 1024 for _ in texts],
            embedding_is_remote=True,
            inbox_idempotency_key=f"dispatch:{command.idempotency_key}",
            worker_id="integration-worker",
        )
        redelivery = IngestionOrchestrator(db, storage, clock=clock).process_job(
            accepted.job_id,
            extract_text_fn=lambda *_args: pytest.fail("redelivery parsed content"),
            chunk_text_fn=lambda *_args, **_kwargs: pytest.fail(
                "redelivery chunked content"
            ),
            embed_texts_fn=lambda *_args, **_kwargs: pytest.fail(
                "redelivery embedded content"
            ),
            embedding_is_remote=False,
            inbox_idempotency_key=f"dispatch:{command.idempotency_key}",
        )

        db.expire_all()
        document = db.get(Document, accepted.document_id)
        version = db.get(DocumentVersion, accepted.version_id)
        job = db.get(IngestionJob, accepted.job_id)
        assert result["status"] == "completed"
        assert redelivery["skipped"] is True
        assert document.active_version_id == version.id
        assert document.status == "indexed"
        assert version.status == "ready"
        assert job.status == "completed"
        assert db.query(Chunk).filter_by(version_id=version.id).count() == 1
        assert db.query(IngestionAttempt).filter_by(job_id=job.id).count() == 1
        assert db.query(InboxReceipt).filter_by(job_id=job.id).count() == 1
        assert (
            db.query(IngestionReceipt)
            .filter_by(job_id=job.id, stage="activating", status="completed")
            .count()
            == 1
        )
        artifacts = db.query(DocumentArtifact).filter_by(version_id=version.id).all()
        assert {item.artifact_type for item in artifacts} == {
            "original",
            "normalized_json",
            "normalized_md",
        }
        registered = db.query(StorageObject).filter_by(version_id=version.id).all()
        assert {row.status for row in registered} == {"deleted", "referenced"}
        assert len([row for row in registered if row.status == "referenced"]) == 3
        assert not any(key.startswith("staging/") for key in storage.data)
        remote_audit = (
            db.query(AuditEvent)
            .filter_by(
                event_type="ingestion.remote_embedding_authorized",
                project_id=project_id,
            )
            .one()
        )
        assert remote_audit.actor_principal_id == principal_id
        assert remote_audit.workspace_id == workspace_id
        assert remote_audit.metadata_json["classification"] == "internal"
        assert remote_audit.metadata_json["document_id"] == str(document.id)
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_policy_outbox_lease_and_orphan_recovery_paths() -> None:
    import os

    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    storage = MemoryStorage()
    now = datetime(2026, 9, 2, 21, 0, tzinfo=timezone.utc)
    clock = FixedClock(now)
    principal_id, workspace_id, project_id = (uuid.uuid4() for _ in range(3))

    try:
        principal = Principal(id=principal_id, subject=f"a6-recovery:{principal_id}")
        workspace = Workspace(id=workspace_id, name=f"A6 recovery {workspace_id}")
        project = Project(id=project_id, workspace_id=workspace_id, name="A6 recovery")
        db.add_all([principal, workspace])
        db.flush()
        db.add(project)
        db.commit()

        secret = b"API_KEY=definitely-sensitive-value"
        quarantined = IngestionOrchestrator(
            db,
            storage,
            clock=clock,
            publisher=lambda *_args: pytest.fail("quarantine published a job"),
        ).accept_source(
            AcceptSourceCommand(
                project=project,
                filename="secret.txt",
                content=secret,
                descriptor=_descriptor(secret, classification="confidential"),
                idempotency_key=f"secret:{uuid.uuid4()}",
                actor_principal_id=principal_id,
                workspace_id=workspace_id,
            )
        )
        assert quarantined.status == "failed"
        assert quarantined.quarantine_reason == "credential_detected"
        assert storage.data == {}
        assert (
            db.query(IngestionReceipt)
            .filter_by(job_id=quarantined.job_id, error_code="content_quarantined")
            .count()
            == 1
        )

        content = b"durable outbox payload"
        accepted = IngestionOrchestrator(
            db,
            storage,
            clock=clock,
            publisher=lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
        ).accept_source(
            AcceptSourceCommand(
                project=project,
                filename="outbox.txt",
                content=content,
                descriptor=_descriptor(content),
                idempotency_key=f"outbox:{uuid.uuid4()}",
                actor_principal_id=principal_id,
                workspace_id=workspace_id,
            )
        )
        event = db.get(OutboxEvent, accepted.outbox_event_id)
        assert event.status == "failed"
        assert event.error_code == "RuntimeError"
        delivered: list[str] = []
        clock.current += timedelta(seconds=31)
        assert (
            OutboxDispatcher(
                db, lambda job_id, _key: delivered.append(job_id), clock
            ).dispatch_pending()
            == 1
        )
        assert delivered == [str(accepted.job_id)]
        assert db.get(OutboxEvent, accepted.outbox_event_id).status == "published"

        stale_event = OutboxEvent(
            id=uuid.uuid4(),
            aggregate_type="ingestion_job",
            aggregate_id=accepted.job_id,
            event_type="ingestion.job.requested",
            idempotency_key=f"stale:{uuid.uuid4()}",
            payload_json={"job_id": str(accepted.job_id)},
            status="dispatching",
            attempts=1,
            available_at=clock.now(),
            claimed_at=clock.now() - timedelta(minutes=3),
            created_at=clock.now() - timedelta(minutes=3),
        )
        db.add(stale_event)
        db.commit()
        stale_delivered: list[str] = []
        assert (
            OutboxDispatcher(
                db, lambda job_id, _key: stale_delivered.append(job_id), clock
            ).dispatch_pending()
            == 1
        )
        assert stale_delivered == [str(accepted.job_id)]
        assert db.get(OutboxEvent, stale_event.id).status == "published"

        cancel_content = b"cancel before worker claim"
        cancel_target = IngestionOrchestrator(db, storage, clock=clock).accept_source(
            AcceptSourceCommand(
                project=project,
                filename="cancel.txt",
                content=cancel_content,
                descriptor=_descriptor(cancel_content),
                idempotency_key=f"cancel:{uuid.uuid4()}",
                actor_principal_id=principal_id,
                workspace_id=workspace_id,
            )
        )
        principal_context = PrincipalContext(
            principal_id=principal_id,
            workspace_id=workspace_id,
            roles=frozenset({"admin"}),
            auth_mode="api_key",
        )
        cancelled = cancel_ingestion_job(
            cancel_target.job_id, project_id, db, principal_context
        )
        repeated_cancel = cancel_ingestion_job(
            cancel_target.job_id, project_id, db, principal_context
        )
        assert cancelled["status"] == "cancelled"
        assert repeated_cancel["status"] == "cancelled"
        assert (
            db.query(IngestionReceipt)
            .filter_by(job_id=cancel_target.job_id, status="cancelled")
            .count()
            == 1
        )
        assert db.get(DocumentVersion, cancel_target.version_id).status == "failed"
        cancelled_staging = db.get(
            DocumentVersion, cancel_target.version_id
        ).storage_key
        storage.modified[cancelled_staging] = clock.now() - timedelta(hours=2)
        assert sweep_orphan_staging(
            db, storage, clock=clock, grace_seconds=3600, dry_run=False
        ) == [cancelled_staging]
        assert cancelled_staging not in storage.data
        assert (
            db.query(StorageObject)
            .filter_by(storage_key=cancelled_staging, status="deleted")
            .count()
            == 1
        )

        job = db.get(IngestionJob, accepted.job_id)
        job.status = "running"
        job.lease_owner = "dead-worker"
        job.lease_expires_at = clock.now() - timedelta(seconds=1)
        job.heartbeat_at = clock.now() - timedelta(seconds=30)
        attempt = IngestionAttempt(
            id=uuid.uuid4(),
            job_id=job.id,
            attempt_no=1,
            worker_id="dead-worker",
            status="running",
            claimed_at=clock.now() - timedelta(minutes=2),
            lease_expires_at=clock.now() - timedelta(seconds=1),
            heartbeat_at=clock.now() - timedelta(seconds=30),
        )
        db.add(attempt)
        db.commit()
        assert reconcile_stale_leases(db, clock=clock) == 1
        db.expire_all()
        assert db.get(IngestionJob, job.id).status == "retrying"
        assert db.get(IngestionAttempt, attempt.id).status == "stale"

        orphan_key = "staging/unregistered/old-object"
        storage.put(orphan_key, b"orphan")
        storage.modified[orphan_key] = clock.now() - timedelta(hours=2)
        assert sweep_orphan_staging(
            db, storage, clock=clock, grace_seconds=3600, dry_run=True
        ) == [orphan_key]
        assert orphan_key in storage.data
        assert sweep_orphan_staging(
            db, storage, clock=clock, grace_seconds=3600, dry_run=False
        ) == [orphan_key]
        assert orphan_key not in storage.data
        assert db.query(StorageGcReceipt).filter_by(action="orphan_sweep").count() == 3
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_commit_failure_leaves_only_a_sweepable_staging_orphan(monkeypatch) -> None:
    import os

    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    storage = MemoryStorage()
    now = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    clock = FixedClock(now)
    principal_id, workspace_id, project_id = (uuid.uuid4() for _ in range(3))

    try:
        principal = Principal(id=principal_id, subject=f"a6-commit:{principal_id}")
        workspace = Workspace(id=workspace_id, name=f"A6 commit {workspace_id}")
        project = Project(id=project_id, workspace_id=workspace_id, name="A6 commit")
        db.add_all([principal, workspace])
        db.flush()
        db.add(project)
        db.commit()

        original_commit = db.commit

        def fail_commit() -> None:
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        content = b"must become a recoverable orphan"
        with pytest.raises(RuntimeError, match="injected commit failure"):
            IngestionOrchestrator(db, storage, clock=clock).accept_source(
                AcceptSourceCommand(
                    project=project,
                    filename="commit-failure.txt",
                    content=content,
                    descriptor=_descriptor(content),
                    idempotency_key=f"commit-failure:{uuid.uuid4()}",
                    actor_principal_id=principal_id,
                    workspace_id=workspace_id,
                )
            )
        monkeypatch.setattr(db, "commit", original_commit)
        assert len(storage.data) == 1
        orphan_key = next(iter(storage.data))
        assert orphan_key.startswith("staging/")
        storage.modified[orphan_key] = clock.now() - timedelta(hours=2)
        assert sweep_orphan_staging(
            db, storage, clock=clock, grace_seconds=3600, dry_run=False
        ) == [orphan_key]
        assert storage.data == {}
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_reindex_uses_same_orchestrator_and_preserves_active_version_on_failure(
    tmp_path,
) -> None:
    import hashlib
    import os

    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    storage = MemoryStorage()
    clock = FixedClock(datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc))
    principal_id, workspace_id, project_id = (uuid.uuid4() for _ in range(3))

    def scan_for(revision: str, text: str) -> ScanResult:
        source = tmp_path / "source.py"
        source.write_text(text, encoding="utf-8")
        raw = source.read_bytes()
        return ScanResult(
            source_type="directory",
            source_revision=revision,
            root_dir=str(tmp_path),
            files=[
                ScannedFile(
                    relative_path="source.py",
                    abs_path=str(source),
                    size_bytes=len(raw),
                    content_hash=hashlib.sha256(raw).hexdigest(),
                    language="python",
                    mime_type="text/x-python",
                )
            ],
        )

    def command_for(project, scan, *, existing=None) -> AcceptSourceCommand:
        bundle = build_scan_bundle(scan)
        checksum = hashlib.sha256(bundle).hexdigest()
        return AcceptSourceCommand(
            project=project,
            filename="source-bundle.json",
            content=bundle,
            descriptor=SourceDescriptor(
                source_type="directory",
                origin="workspace:source",
                revision=scan.source_revision,
                content_length=len(bundle),
                content_hash=checksum,
                detected_mime=BUNDLE_MIME,
                declared_mime=BUNDLE_MIME,
                data_classification_hint="internal",
            ),
            idempotency_key=f"scan:{scan.source_revision}:{checksum}",
            actor_principal_id=principal_id,
            workspace_id=workspace_id,
            existing_document=existing,
            scan_config={"relative_path": "source"},
        )

    try:
        db.add_all(
            [
                Principal(id=principal_id, subject=f"a6-reindex:{principal_id}"),
                Workspace(id=workspace_id, name=f"A6 reindex {workspace_id}"),
            ]
        )
        db.flush()
        project = Project(id=project_id, workspace_id=workspace_id, name="A6 reindex")
        db.add(project)
        db.commit()

        first_scan = scan_for("rev-1", "def first():\n    return 1\n")
        first = IngestionOrchestrator(db, storage, clock=clock).accept_source(
            command_for(project, first_scan)
        )
        IngestionOrchestrator(db, storage, clock=clock).process_job(
            first.job_id,
            embed_texts_fn=lambda texts, instruction="": [[0.03] * 1024 for _ in texts],
            embedding_is_remote=False,
            worker_id="reindex-worker-1",
        )
        db.expire_all()
        document = db.get(Document, first.document_id)
        first_active = document.active_version_id
        assert first_active == first.version_id
        assert db.query(SourceFile).filter_by(version_id=first.version_id).count() == 1
        assert (
            db.query(Chunk)
            .filter(
                Chunk.version_id == first.version_id, Chunk.source_file_id.is_not(None)
            )
            .count()
            > 0
        )

        second_scan = scan_for("rev-2", "def second():\n    return 2\n")
        second = IngestionOrchestrator(db, storage, clock=clock).accept_source(
            command_for(project, second_scan, existing=document)
        )
        assert db.get(Document, document.id).active_version_id == first_active
        with pytest.raises(RetryableIngestionError, match="provider unavailable"):
            IngestionOrchestrator(db, storage, clock=clock).process_job(
                second.job_id,
                embed_texts_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("provider unavailable")
                ),
                embedding_is_remote=False,
                worker_id="reindex-worker-failed",
            )
        db.expire_all()
        assert db.get(Document, document.id).active_version_id == first_active
        assert db.get(Document, document.id).status == "indexed"

        IngestionOrchestrator(db, storage, clock=clock).process_job(
            second.job_id,
            embed_texts_fn=lambda texts, instruction="": [[0.04] * 1024 for _ in texts],
            embedding_is_remote=False,
            worker_id="reindex-worker-2",
        )
        db.expire_all()
        assert db.get(Document, document.id).active_version_id == second.version_id
        assert db.get(DocumentVersion, first.version_id).status == "superseded"
        assert db.get(DocumentVersion, second.version_id).status == "ready"
        assert db.query(SourceFile).filter_by(version_id=second.version_id).count() == 1
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_delete_retention_citation_hold_and_gc_are_safe_and_idempotent() -> None:
    import os

    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    storage = MemoryStorage()
    clock = FixedClock(datetime(2026, 9, 3, 2, 0, tzinfo=timezone.utc))
    principal_id, workspace_id, project_id = (uuid.uuid4() for _ in range(3))

    try:
        db.add_all(
            [
                Principal(id=principal_id, subject=f"a6-gc:{principal_id}"),
                Workspace(id=workspace_id, name=f"A6 GC {workspace_id}"),
            ]
        )
        db.flush()
        project = Project(id=project_id, workspace_id=workspace_id, name="A6 GC")
        db.add_all(
            [
                project,
                WorkspaceMembership(
                    workspace_id=workspace_id,
                    principal_id=principal_id,
                    role="admin",
                ),
            ]
        )
        db.commit()

        content = b"retention protected payload"
        accepted = IngestionOrchestrator(db, storage, clock=clock).accept_source(
            AcceptSourceCommand(
                project=project,
                filename="retention.txt",
                content=content,
                descriptor=_descriptor(content),
                idempotency_key=f"retention:{uuid.uuid4()}",
                actor_principal_id=principal_id,
                workspace_id=workspace_id,
            )
        )
        IngestionOrchestrator(db, storage, clock=clock).process_job(
            accepted.job_id,
            extract_text_fn=lambda _path, _name: "retained chunk",
            chunk_text_fn=lambda _source, **_kwargs: [_candidate("retained chunk")],
            embed_texts_fn=lambda texts, instruction="": [[0.05] * 1024 for _ in texts],
            embedding_is_remote=False,
            worker_id="gc-worker",
        )

        principal = PrincipalContext(
            principal_id=principal_id,
            workspace_id=workspace_id,
            roles=frozenset({"admin"}),
            auth_mode="api_key",
        )
        deleted = delete_document(accepted.document_id, project_id, db, principal)
        repeated = delete_document(accepted.document_id, project_id, db, principal)
        assert deleted["retention_until"]
        assert repeated["success"] is True

        registered = (
            db.query(StorageObject)
            .filter_by(version_id=accepted.version_id, status="referenced")
            .all()
        )
        assert len(registered) == 3
        assert all(row.retention_until is not None for row in registered)
        registered[0].legal_hold = True
        for row in registered:
            row.retention_until = clock.now() - timedelta(seconds=1)

        conversation = Conversation(
            id=uuid.uuid4(), project_id=project_id, title="citation hold"
        )
        message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            role="assistant",
            content="grounded answer",
        )
        citation = MessageCitation(
            id=uuid.uuid4(),
            message_id=message.id,
            document_id=accepted.document_id,
            version_id=accepted.version_id,
            citation_label="S1",
        )
        db.add(conversation)
        db.flush([conversation])
        db.add(message)
        db.flush([message])
        db.add(citation)
        db.commit()

        original_keys = set(storage.data)
        assert run_storage_gc(db, storage, clock=clock, dry_run=True) == []
        assert run_storage_gc(db, storage, clock=clock, dry_run=False) == []
        assert set(storage.data) == original_keys

        db.delete(citation)
        db.commit()
        planned = run_storage_gc(db, storage, clock=clock, dry_run=True)
        assert set(planned) == {row.storage_key for row in registered[1:]}
        assert set(storage.data) == original_keys

        deleted_keys = run_storage_gc(db, storage, clock=clock, dry_run=False)
        assert set(deleted_keys) == set(planned)
        assert registered[0].storage_key in storage.data
        assert db.query(Chunk).filter_by(version_id=accepted.version_id).count() == 1

        registered[0].legal_hold = False
        db.commit()
        assert run_storage_gc(db, storage, clock=clock, dry_run=False) == [
            registered[0].storage_key
        ]
        assert storage.data == {}
        assert db.query(Chunk).filter_by(version_id=accepted.version_id).count() == 0
        assert (
            db.query(DocumentArtifact).filter_by(version_id=accepted.version_id).count()
            == 0
        )
        assert run_storage_gc(db, storage, clock=clock, dry_run=False) == []
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    "checkpoint",
    [
        "after_storing",
        "after_parsing",
        "after_embedding",
        "after_indexing",
        "after_activation",
    ],
)
def test_checkpoint_crash_retry_converges_without_duplicate_data(checkpoint) -> None:
    import os

    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    storage = MemoryStorage()
    clock = FixedClock(datetime(2026, 9, 3, 3, 0, tzinfo=timezone.utc))
    principal_id, workspace_id, project_id = (uuid.uuid4() for _ in range(3))

    try:
        db.add_all(
            [
                Principal(id=principal_id, subject=f"a6-fault:{uuid.uuid4()}"),
                Workspace(id=workspace_id, name=f"A6 fault {workspace_id}"),
            ]
        )
        db.flush()
        project = Project(id=project_id, workspace_id=workspace_id, name="A6 fault")
        db.add(project)
        db.commit()

        content = f"fault injection at {checkpoint}".encode()
        accepted = IngestionOrchestrator(db, storage, clock=clock).accept_source(
            AcceptSourceCommand(
                project=project,
                filename=f"{checkpoint}.txt",
                content=content,
                descriptor=_descriptor(content),
                idempotency_key=f"fault:{checkpoint}:{uuid.uuid4()}",
                actor_principal_id=principal_id,
                workspace_id=workspace_id,
            )
        )

        def crash_at(actual: str) -> None:
            if actual == checkpoint:
                raise RuntimeError(f"injected worker crash at {actual}")

        with pytest.raises(RetryableIngestionError, match="injected worker crash"):
            IngestionOrchestrator(db, storage, clock=clock).process_job(
                accepted.job_id,
                extract_text_fn=lambda _path, _name: "stable normalized text",
                chunk_text_fn=lambda _source, **_kwargs: [_candidate("stable chunk")],
                embed_texts_fn=lambda texts, instruction="": [
                    [0.06] * 1024 for _ in texts
                ],
                embedding_is_remote=False,
                worker_id=f"fault-worker-{checkpoint}",
                checkpoint_hook=crash_at,
            )

        db.expire_all()
        assert db.get(IngestionJob, accepted.job_id).status == "retrying"
        result = IngestionOrchestrator(db, storage, clock=clock).process_job(
            accepted.job_id,
            extract_text_fn=lambda _path, _name: "stable normalized text",
            chunk_text_fn=lambda _source, **_kwargs: [_candidate("stable chunk")],
            embed_texts_fn=lambda texts, instruction="": [[0.06] * 1024 for _ in texts],
            embedding_is_remote=False,
            worker_id=f"recovery-worker-{checkpoint}",
        )

        db.expire_all()
        assert result["status"] == "completed"
        assert (
            db.get(Document, accepted.document_id).active_version_id
            == accepted.version_id
        )
        assert db.query(DocumentVersion).filter_by(id=accepted.version_id).count() == 1
        assert db.query(Chunk).filter_by(version_id=accepted.version_id).count() == 1
        assert db.query(IngestionAttempt).filter_by(job_id=accepted.job_id).count() == 2
        assert (
            db.query(DocumentArtifact).filter_by(version_id=accepted.version_id).count()
            == 3
        )
        assert (
            len(
                db.query(StorageObject)
                .filter_by(version_id=accepted.version_id, status="referenced")
                .all()
            )
            == 3
        )
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()
