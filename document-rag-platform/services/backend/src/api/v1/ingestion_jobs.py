"""Ingestion job status/event endpoints (Aşama 2.4).

Read-only views onto the ``ingestion_jobs`` / ``ingestion_events`` tables
written by ``workers.ingestion_tasks.run_ingestion_job``. Nothing here
mutates job state — jobs are only ever advanced by the worker.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import IngestionEvent, IngestionJob

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


@router.get("/{job_id}")
def get_ingestion_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return _serialize_job(job)


@router.get("/{job_id}/events")
def list_ingestion_job_events(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    events = (
        db.query(IngestionEvent)
        .filter(IngestionEvent.job_id == job_id)
        .order_by(IngestionEvent.created_at.asc())
        .all()
    )
    return [_serialize_event(e) for e in events]
