"""Single application service coordinating ingestion side effects and state."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy.orm import Session

from ..domain.clock import Clock, SYSTEM_CLOCK
from ..domain.ingestion import (
    ContentPolicyDecision,
    SourceDescriptor,
    decide_content_policy,
    source_fingerprint,
)
from ..infrastructure.storage import object_keys
from ..infrastructure.observability import metrics, traced
from ..models import (
    AuditEvent,
    ContentPolicyDecisionRecord,
    Document,
    DocumentArtifact,
    DocumentVersion,
    EmbeddingProfile,
    IngestionAttempt,
    IngestionJob,
    IngestionReceipt,
    OutboxEvent,
    Project,
    StorageObject,
)


PARSER_PROFILE = "parser-router-v1"
CHUNKER_PROFILE = "context-vault-chunker-v4"
OUTBOX_EVENT_TYPE = "ingestion.job.requested"


class IngestionAcceptanceError(RuntimeError):
    pass


class SourceQuarantined(IngestionAcceptanceError):
    def __init__(self, reason: str):
        super().__init__(f"source quarantined: {reason}")
        self.reason = reason


@dataclass(frozen=True)
class AcceptSourceCommand:
    project: Project
    filename: str
    content: bytes
    descriptor: SourceDescriptor
    idempotency_key: str
    actor_principal_id: uuid.UUID
    workspace_id: uuid.UUID
    existing_document: Optional[Document] = None
    scan_config: Optional[dict] = None


@dataclass(frozen=True)
class AcceptedSource:
    document_id: uuid.UUID
    version_id: uuid.UUID
    job_id: uuid.UUID
    outbox_event_id: Optional[uuid.UUID]
    status: str
    replayed: bool = False
    quarantine_reason: Optional[str] = None


class OutboxDispatcher:
    """Publish committed events; a failed publish remains durably retryable."""

    def __init__(self, db: Session, publish: Callable[[str, str], None], clock: Clock):
        self.db = db
        self.publish = publish
        self.clock = clock

    @traced("outbox.dispatch")
    def dispatch_one(self, event_id: uuid.UUID) -> bool:
        event = (
            self.db.query(OutboxEvent)
            .filter(OutboxEvent.id == event_id)
            .with_for_update()
            .first()
        )
        if event is None:
            raise IngestionAcceptanceError("outbox event not found")
        if event.status == "published":
            return True
        from ..config import settings

        now = self.clock.now()
        stale_before = now - timedelta(
            seconds=settings.INGESTION_OUTBOX_CLAIM_TIMEOUT_SECONDS
        )
        if (
            event.status == "dispatching"
            and event.claimed_at is not None
            and event.claimed_at > stale_before
        ):
            return False
        event.status = "dispatching"
        event.claimed_at = now
        event.attempts = (event.attempts or 0) + 1
        self.db.commit()
        try:
            self.publish(str(event.aggregate_id), event.idempotency_key)
        except Exception as exc:
            metrics.incr("outbox.failures")
            event = self.db.get(OutboxEvent, event_id)
            event.status = "failed"
            event.error_code = type(exc).__name__
            event.error_message = str(exc)[:1000]
            event.available_at = self.clock.now() + timedelta(
                seconds=settings.INGESTION_OUTBOX_RETRY_SECONDS
            )
            self.db.commit()
            return False
        event = self.db.get(OutboxEvent, event_id)
        event.status = "published"
        event.error_code = None
        event.error_message = None
        event.published_at = self.clock.now()
        self.db.commit()
        metrics.incr("outbox.published")
        return True

    def dispatch_pending(self, limit: int = 100) -> int:
        from ..config import settings

        now = self.clock.now()
        stale_before = now - timedelta(
            seconds=settings.INGESTION_OUTBOX_CLAIM_TIMEOUT_SECONDS
        )
        stale = (
            self.db.query(OutboxEvent)
            .filter(
                OutboxEvent.status == "dispatching",
                OutboxEvent.claimed_at <= stale_before,
            )
            .all()
        )
        for event in stale:
            event.status = "failed"
            event.error_code = "stale_dispatch_claim"
            event.error_message = "outbox dispatch claim expired before publish receipt"
            event.available_at = now
        if stale:
            self.db.commit()
        events = (
            self.db.query(OutboxEvent)
            .filter(
                OutboxEvent.status.in_(("pending", "failed")),
                OutboxEvent.available_at <= now,
            )
            .order_by(OutboxEvent.created_at.asc())
            .limit(limit)
            .all()
        )
        metrics.set_gauge("outbox.backlog", len(events))
        return sum(1 for event in events if self.dispatch_one(event.id))


class IngestionOrchestrator:
    """Canonical upload/retry coordinator.

    Parser/chunker/embedding/indexing execution is invoked through
    :meth:`process_job`; API code never performs those stages itself.
    """

    def __init__(
        self,
        db: Session,
        storage,
        *,
        clock: Clock = SYSTEM_CLOCK,
        publisher: Optional[Callable[[str, str], None]] = None,
    ):
        self.db = db
        self.storage = storage
        self.clock = clock
        self.publisher = publisher

    def _active_profile(self) -> EmbeddingProfile:
        profile = (
            self.db.query(EmbeddingProfile)
            .filter(EmbeddingProfile.is_active.is_(True))
            .one_or_none()
        )
        if profile is None:
            raise IngestionAcceptanceError(
                "exactly one active embedding profile is required"
            )
        return profile

    @staticmethod
    def _policy_record(
        *,
        policy_id: uuid.UUID,
        document_id: uuid.UUID,
        descriptor: SourceDescriptor,
        decision: ContentPolicyDecision,
        now,
    ) -> ContentPolicyDecisionRecord:
        return ContentPolicyDecisionRecord(
            id=policy_id,
            document_id=document_id,
            classification=decision.classification,
            contains_credentials=decision.contains_credentials,
            contains_private_key=decision.contains_private_key,
            contains_pii=decision.contains_pii,
            permit_original_storage=decision.permit_original_storage,
            permit_normalized_storage=decision.permit_normalized_storage,
            permit_local_embedding=decision.permit_local_embedding,
            permit_remote_embedding=decision.permit_remote_embedding,
            permit_local_generation=decision.permit_local_generation,
            permit_remote_generation=decision.permit_remote_generation,
            redaction_required=decision.redaction_required,
            quarantine_reason=decision.quarantine_reason,
            policy_version=decision.policy_version,
            source_fingerprint=source_fingerprint(descriptor),
            created_at=now,
        )

    def accept_source(self, command: AcceptSourceCommand) -> AcceptedSource:
        existing = (
            self.db.query(IngestionJob)
            .filter(IngestionJob.idempotency_key == command.idempotency_key)
            .first()
        )
        if existing is not None:
            version = self.db.get(DocumentVersion, existing.version_id)
            return AcceptedSource(
                document_id=version.document_id,
                version_id=version.id,
                job_id=existing.id,
                outbox_event_id=None,
                status=existing.status,
                replayed=True,
            )

        decision = decide_content_policy(
            command.content,
            classification=command.descriptor.data_classification_hint,
        )
        now = self.clock.now()
        document_id = (
            command.existing_document.id
            if command.existing_document is not None
            else uuid.uuid4()
        )
        version_id, policy_id, job_id = (uuid.uuid4() for _ in range(3))
        profile = self._active_profile()
        is_new_document = command.existing_document is None
        if is_new_document:
            document = Document(
                id=document_id,
                project_id=command.project.id,
                name=command.filename,
                size=command.descriptor.content_length,
                status="error" if decision.quarantined else "uploaded",
                error_code="content_quarantined" if decision.quarantined else None,
                error_message=(
                    f"Source requires review: {decision.quarantine_reason}"
                    if decision.quarantined
                    else None
                ),
                uploaded_at=now,
                source_type=command.descriptor.source_type,
                origin_uri=command.descriptor.origin,
                mime_type=command.descriptor.detected_mime,
                checksum=command.descriptor.content_hash,
                data_classification=decision.classification,
                created_at=now,
                updated_at=now,
            )
        else:
            document = command.existing_document
            if document.project_id != command.project.id:
                raise IngestionAcceptanceError(
                    "existing document is outside the requested project"
                )
        existing_versions = (
            self.db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == document_id)
            .all()
        )
        version_no = (
            max(
                (int(candidate.version_no) for candidate in existing_versions),
                default=0,
            )
            + 1
        )
        policy = self._policy_record(
            policy_id=policy_id,
            document_id=document_id,
            descriptor=command.descriptor,
            decision=decision,
            now=now,
        )
        version = DocumentVersion(
            id=version_id,
            document_id=document_id,
            version_no=version_no,
            source_revision=command.descriptor.revision,
            status="failed" if decision.quarantined else "pending",
            parser_profile=PARSER_PROFILE,
            chunker_profile=CHUNKER_PROFILE,
            embedding_profile_id=profile.id,
            content_policy_decision_id=policy_id,
            error_code="content_quarantined" if decision.quarantined else None,
            error_message=(
                f"Source requires review: {decision.quarantine_reason}"
                if decision.quarantined
                else None
            ),
            created_at=now,
        )
        job = IngestionJob(
            id=job_id,
            idempotency_key=command.idempotency_key,
            version_id=version_id,
            actor_principal_id=command.actor_principal_id,
            workspace_id=command.workspace_id,
            status="failed" if decision.quarantined else "queued",
            stage="validating",
            progress=0,
            attempt=0,
            error_code="content_quarantined" if decision.quarantined else None,
            error_message=(
                f"Source requires review: {decision.quarantine_reason}"
                if decision.quarantined
                else None
            ),
            finished_at=now if decision.quarantined else None,
            created_at=now,
        )
        # The metadata graph intentionally contains a circular
        # documents.active_version_id <-> document_versions.document_id
        # relationship.  Flush the initial rows in their real FK order while
        # keeping them in the same transaction; relying on ORM table sorting
        # can otherwise emit the policy/version before its document.
        if is_new_document:
            self.db.add(document)
            self.db.flush([document])
        self.db.add(policy)
        self.db.flush([policy])
        self.db.add(version)
        self.db.flush([version])
        if command.scan_config is not None:
            self.db.add(
                DocumentArtifact(
                    id=uuid.uuid4(),
                    version_id=version_id,
                    artifact_type="scan_config",
                    storage_key=f"inline:scan_config:{version_id}",
                    checksum=hashlib.sha256(
                        json.dumps(
                            command.scan_config,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                    size_bytes=0,
                    metadata_json=command.scan_config,
                    created_at=now,
                )
            )
        self.db.add(job)
        self.db.flush([job])

        if decision.quarantined:
            attempt_id = uuid.uuid4()
            self.db.add(
                IngestionAttempt(
                    id=attempt_id,
                    job_id=job_id,
                    attempt_no=0,
                    worker_id="policy-gate",
                    status="failed",
                    claimed_at=now,
                    lease_expires_at=now,
                    heartbeat_at=now,
                    finished_at=now,
                    error_code="content_quarantined",
                    error_message=job.error_message,
                )
            )
            self.db.add(
                IngestionReceipt(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    attempt_id=attempt_id,
                    stage="validating",
                    status="failed",
                    error_code="content_quarantined",
                    evidence_hash=command.descriptor.content_hash,
                    metadata_json={"quarantine_reason": decision.quarantine_reason},
                    created_at=now,
                )
            )
            self.db.add(
                AuditEvent(
                    id=uuid.uuid4(),
                    actor_principal_id=command.actor_principal_id,
                    workspace_id=command.workspace_id,
                    project_id=command.project.id,
                    event_type="ingestion.content_quarantined",
                    metadata_json={
                        "document_id": str(document_id),
                        "reason": decision.quarantine_reason,
                        "source_fingerprint": source_fingerprint(command.descriptor),
                    },
                    created_at=now,
                )
            )
            self.db.commit()
            return AcceptedSource(
                document_id=document_id,
                version_id=version_id,
                job_id=job_id,
                outbox_event_id=None,
                status="failed",
                quarantine_reason=decision.quarantine_reason,
            )

        staging_key = object_keys.staging_key(
            command.idempotency_key,
            command.descriptor.content_hash,
            command.filename,
        )
        self.storage.put(
            staging_key, command.content, content_type=command.descriptor.detected_mime
        )
        stored_checksum = hashlib.sha256(self.storage.get(staging_key)).hexdigest()
        if stored_checksum != command.descriptor.content_hash:
            raise IngestionAcceptanceError("staging object checksum mismatch")
        version.storage_key = staging_key
        artifact_id = uuid.uuid4()
        artifact = DocumentArtifact(
            id=artifact_id,
            version_id=version_id,
            artifact_type="original",
            storage_key=staging_key,
            checksum=stored_checksum,
            size_bytes=len(command.content),
            metadata_json={"state": "staged"},
            created_at=now,
        )
        self.db.add(artifact)
        self.db.flush([artifact])
        self.db.add(
            StorageObject(
                id=uuid.uuid4(),
                storage_key=staging_key,
                checksum=stored_checksum,
                size_bytes=len(command.content),
                status="staged",
                version_id=version_id,
                artifact_id=artifact_id,
                created_at=now,
            )
        )
        outbox_id = uuid.uuid4()
        self.db.add(
            OutboxEvent(
                id=outbox_id,
                aggregate_type="ingestion_job",
                aggregate_id=job_id,
                event_type=OUTBOX_EVENT_TYPE,
                idempotency_key=f"dispatch:{command.idempotency_key}",
                payload_json={"job_id": str(job_id)},
                status="pending",
                attempts=0,
                available_at=now,
                created_at=now,
            )
        )
        for event_type in command.descriptor.policy_events:
            self.db.add(
                AuditEvent(
                    id=uuid.uuid4(),
                    actor_principal_id=command.actor_principal_id,
                    workspace_id=command.workspace_id,
                    project_id=command.project.id,
                    event_type=f"ingestion.{event_type}",
                    metadata_json={
                        "source_fingerprint": source_fingerprint(command.descriptor)
                    },
                    created_at=now,
                )
            )
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        if self.publisher is not None:
            OutboxDispatcher(self.db, self.publisher, self.clock).dispatch_one(
                outbox_id
            )
        return AcceptedSource(
            document_id=document_id,
            version_id=version_id,
            job_id=job_id,
            outbox_event_id=outbox_id,
            status="queued",
        )

    def process_job(self, job_id, **kwargs):
        """Delegate worker execution through the canonical service boundary."""
        from ..workers.ingestion_tasks import run_ingestion_job

        return run_ingestion_job(
            db=self.db,
            job_id=job_id,
            storage=self.storage,
            clock=self.clock,
            **kwargs,
        )
