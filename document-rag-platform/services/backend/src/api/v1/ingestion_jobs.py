"""Ingestion job status/event endpoints (Aşama 2.4).

Read-only views onto the ``ingestion_jobs`` / ``ingestion_events`` tables
written by ``workers.ingestion_tasks.run_ingestion_job``. Nothing here
mutates job state — jobs are only ever advanced by the worker.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from ...db import get_db
from ...models import Document, DocumentVersion, IngestionEvent, IngestionJob
from src.domain.identity import PrincipalContext
from src.infrastructure.security.auth import get_principal_context, require_project_access

router = APIRouter(prefix="/ingestion-jobs", tags=["ingestion-jobs"])


def _serialize_job(job: IngestionJob) -> dict:
    return {
        "id": str(job.id),
        "version_id": str(job.version_id),
        "document_id": str(job.version.document_id) if job.version else None,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
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


@router.get("/{job_id}")
def get_ingestion_job(
    job_id: UUID,
    project_id: UUID,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    require_project_access(db, principal, project_id)
    job = _scoped_job(db, project_id, job_id)
    return _serialize_job(job)


@router.get("/{job_id}/events")
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
