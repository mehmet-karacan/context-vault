"""Ingestion job status/event endpoints (Aşama 2.4).

Scoped views and the idempotent cancellation command for durable ingestion
jobs. Running work observes cancellation only at safe stage boundaries.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID
from .contracts import IngestionEventResponse, IngestionJobResponse

from ...db import get_db
from ...domain.clock import utc_now
from ...domain.ingestion_state import JobStatus, transition_job
from ...models import (
    Document,
    DocumentVersion,
    IngestionAttempt,
    IngestionEvent,
    IngestionJob,
    IngestionReceipt,
)
from src.domain.identity import PrincipalContext
from src.infrastructure.security.auth import (
    get_principal_context,
    require_project_access,
)

router = APIRouter(prefix="/ingestion-jobs", tags=["ingestion-jobs"])


def _serialize_job(job: IngestionJob) -> dict:
    return {
        "id": str(job.id),
        "version_id": str(job.version_id),
        "document_id": str(job.version.document_id) if job.version else None,
        "status": job.status,
        "stage": job.stage,
        # No measured sub-stage percentage exists yet. Terminal completion is
        # authoritative; a default/stale database zero is not progress evidence.
        "progress": 100 if job.status == "completed" else None,
        "attempt": job.attempt,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


def _serialize_event(event: IngestionEvent) -> dict:
    return {
        "id": str(event.id),
        "job_id": str(event.job_id),
        "stage": event.stage,
        "status": event.status,
        "message": event.message,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _scoped_job(db: Session, project_id: UUID, job_id: UUID) -> IngestionJob:
    job = (
        db.query(IngestionJob)
        .join(DocumentVersion, IngestionJob.version_id == DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        .filter(IngestionJob.id == job_id, Document.project_id == project_id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return job


@router.get("/{job_id}", response_model=IngestionJobResponse)
def get_ingestion_job(
    job_id: UUID,
    project_id: UUID,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    require_project_access(db, principal, project_id)
    job = _scoped_job(db, project_id, job_id)
    return _serialize_job(job)


@router.get("/{job_id}/events", response_model=list[IngestionEventResponse])
def list_ingestion_job_events(
    job_id: UUID,
    project_id: UUID,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    require_project_access(db, principal, project_id)
    job = _scoped_job(db, project_id, job_id)
    events = (
        db.query(IngestionEvent)
        .filter(IngestionEvent.job_id == job.id)
        .order_by(IngestionEvent.created_at.asc())
        .all()
    )
    return [_serialize_event(e) for e in events]


@router.post("/{job_id}/cancel", response_model=IngestionJobResponse)
def cancel_ingestion_job(
    job_id: UUID,
    project_id: UUID,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    """Request cancellation without interrupting an unsafe partial effect."""
    require_project_access(db, principal, project_id)
    job = _scoped_job(db, project_id, job_id)
    now = utc_now()
    if job.status == "cancelled":
        return _serialize_job(job)
    if job.status in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail="Ingestion job is terminal")
    if job.status == "running":
        if job.cancel_requested_at is None:
            job.cancel_requested_at = now
            db.add(
                IngestionEvent(
                    id=uuid.uuid4(),
                    job_id=job.id,
                    stage=job.stage or "validating",
                    status="running",
                    message="cancellation requested; waiting for a safe boundary",
                    created_at=now,
                )
            )
            db.commit()
        return _serialize_job(job)

    # queued/retrying jobs have no in-flight effect and can be closed now.
    transition_job(job, JobStatus.CANCELLED)
    job.attempt = (job.attempt or 0) + 1
    job.finished_at = now
    job.cancel_requested_at = now
    job.error_code = "cancelled_by_user"
    job.error_message = "ingestion cancelled before worker claim"
    attempt = IngestionAttempt(
        id=uuid.uuid4(),
        job_id=job.id,
        attempt_no=job.attempt,
        worker_id="cancellation-command",
        status="cancelled",
        claimed_at=now,
        lease_expires_at=now,
        heartbeat_at=now,
        finished_at=now,
    )
    version = db.get(DocumentVersion, job.version_id)
    if version is not None:
        version.status = "failed"
        version.error_code = "cancelled_by_user"
        version.error_message = job.error_message
        document = db.get(Document, version.document_id)
        if document is not None and document.active_version_id is None:
            document.status = "error"
            document.error_code = "cancelled_by_user"
            document.error_message = job.error_message
            document.updated_at = now
    db.add(attempt)
    db.flush([attempt])
    db.add_all(
        [
            IngestionReceipt(
                id=uuid.uuid4(),
                job_id=job.id,
                attempt_id=attempt.id,
                stage=job.stage or "validating",
                status="cancelled",
                error_code="cancelled_by_user",
                metadata_json={},
                created_at=now,
            ),
            IngestionEvent(
                id=uuid.uuid4(),
                job_id=job.id,
                stage=job.stage or "validating",
                status="cancelled",
                message="ingestion cancelled before worker claim",
                created_at=now,
            ),
        ]
    )
    db.commit()
    return _serialize_job(job)
