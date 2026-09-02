"""Document upload, listing and lifecycle endpoints."""

import hashlib
from datetime import timedelta
from typing import Optional
from uuid import UUID
from .contracts import DocumentResponse, UploadResponse
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...config import settings
from ...db import get_db
from ...application.ingestion_orchestrator import (
    AcceptSourceCommand,
    IngestionOrchestrator,
)
from ...infrastructure.security import (
    SourcePolicyRejected,
    UploadValidationResult,
    describe_upload,
    validate_upload,
)
from ...infrastructure.storage.minio_storage import MinioObjectStorage
from ...models import Document, DocumentVersion, IngestionJob, Project, StorageObject
from src.domain.identity import PrincipalContext
from src.domain.clock import utc_now
from src.infrastructure.rate_limiter import rate_limiter
from src.infrastructure.security.auth import (
    get_principal_context,
    require_project_access,
)

router = APIRouter(tags=["documents"])


def _build_object_storage() -> MinioObjectStorage:
    """Same construction the worker uses (Aşama 2.2's ``MinioObjectStorage``)
    so API acceptance and worker processing share one storage policy."""
    return MinioObjectStorage(
        endpoint=settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        bucket=settings.MINIO_BUCKET,
        encryption_key=settings.OBJECT_STORAGE_ENCRYPTION_KEY,
        allow_legacy_plaintext_reads=settings.OBJECT_STORAGE_ALLOW_LEGACY_PLAINTEXT_READS,
    )


# --- Serialization -----------------------------------------------------------


def _latest_job_for_document(db: Session, document_id) -> Optional[IngestionJob]:
    """Returns the most recently created IngestionJob for any version of
    ``document_id``, or None if the document has never gone through the
    canonical ingestion pipeline (e.g. was uploaded before Aşama 2.4).

    Additive lookup only — never used to decide anything about the document
    row itself, just to surface job progress alongside it (Aşama 2.4 kabul
    kriteri: existing fields are never removed or repurposed).
    """
    return (
        db.query(IngestionJob)
        .join(DocumentVersion, IngestionJob.version_id == DocumentVersion.id)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(IngestionJob.created_at.desc())
        .first()
    )


def serialize_document(
    doc: Document,
    chunks_count: Optional[int] = None,
    job: Optional[IngestionJob] = None,
) -> dict:
    return {
        "id": str(doc.id),
        "name": doc.name,
        "size": doc.size,
        "status": doc.status,
        "uploaded_at": doc.uploaded_at.strftime("%Y-%m-%d %H:%M"),
        "chunks_count": chunks_count if chunks_count is not None else len(doc.chunks),
        "error_message": doc.error_message,
        "project_id": str(doc.project_id),
        "active_version_id": str(doc.active_version_id)
        if doc.active_version_id
        else None,
        "project_name": doc.project.name if doc.project else None,
        # --- Aşama 2.4 additive fields: only populated when this document
        # has an associated IngestionJob (async pipeline). None for
        # documents ingested synchronously / before this stage.
        "job_id": str(job.id) if job is not None else None,
        "job_status": job.status if job is not None else None,
        "job_stage": job.stage if job is not None else None,
        "job_error": job.error_message if job is not None else None,
    }


# --- Security ----------------------------------------------------------------


def guard_upload(file_bytes: bytes, filename: str, mime_type: str) -> None:
    """Reject an upload that fails MIME/magic/size/safety validation.

    Aşama 9.5 guard: on violation we raise an HTTP 400 with a short, safe
    message — never a stack trace. Non-breaking: only rejects clearly unsafe
    inputs (oversize, extension-vs-magic mismatch, total-limit breach).
    """
    result: UploadValidationResult = validate_upload(file_bytes, filename, mime_type)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "Upload rejected")


# --- Routes --------------------------------------------------------------


def _upload_document_async(
    file: UploadFile,
    project: Project,
    db: Session,
    principal: PrincipalContext,
    *,
    idempotency_key: Optional[str] = None,
    data_classification: str = "internal",
) -> dict:
    """Accept a source through the one job/outbox-based ingestion path."""
    from ...workers.ingestion_tasks import process_ingestion_job

    file_bytes = file.file.read()
    filename = file.filename or "file"
    try:
        descriptor = describe_upload(
            file_bytes,
            filename=filename,
            declared_mime=file.content_type,
            classification=data_classification,
        )
    except SourcePolicyRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    key = idempotency_key or (
        f"upload:{project.id}:{hashlib.sha256(file_bytes).hexdigest()}:{filename}"
    )
    orchestrator = IngestionOrchestrator(
        db,
        _build_object_storage(),
        publisher=lambda job_id, dispatch_key: process_ingestion_job.delay(
            job_id, dispatch_key
        ),
    )
    accepted = orchestrator.accept_source(
        AcceptSourceCommand(
            project=project,
            filename=filename,
            content=file_bytes,
            descriptor=descriptor,
            idempotency_key=key,
            actor_principal_id=principal.principal_id,
            workspace_id=principal.workspace_id,
        )
    )
    document = db.get(Document, accepted.document_id)
    job = db.get(IngestionJob, accepted.job_id)

    return {
        "job_id": str(accepted.job_id),
        "document_id": str(accepted.document_id),
        "version_id": str(accepted.version_id),
        "status": accepted.status,
        "replayed": accepted.replayed,
        "quarantine_reason": accepted.quarantine_reason,
        "document": serialize_document(document, chunks_count=0, job=job),
    }


@router.post("/documents/upload", response_model=UploadResponse)
def upload_document(
    file: UploadFile = File(...),
    project_id: UUID = Form(...),
    data_classification: Literal[
        "public", "internal", "confidential", "restricted"
    ] = Form("internal"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    _: None = Depends(rate_limiter),
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    project = require_project_access(db, principal, project_id)

    return _upload_document_async(
        file,
        project,
        db,
        principal,
        idempotency_key=idempotency_key,
        data_classification=data_classification,
    )


@router.get("/documents", response_model=list[DocumentResponse])
def list_documents(
    project_id: UUID,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    require_project_access(db, principal, project_id)
    query = db.query(Document).filter(
        Document.project_id == project_id,
        Document.deleted_at.is_(None),
    )
    documents = query.order_by(Document.uploaded_at.desc()).all()
    return [
        serialize_document(doc, job=_latest_job_for_document(db, doc.id))
        for doc in documents
    ]


def _scoped_document(db: Session, project_id: UUID, doc_id: UUID) -> Document:
    document = (
        db.query(Document)
        .filter(
            Document.id == doc_id,
            Document.project_id == project_id,
            Document.deleted_at.is_(None),
        )
        .first()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/documents/{doc_id}", response_model=DocumentResponse)
def get_document(
    doc_id: UUID,
    project_id: UUID,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    require_project_access(db, principal, project_id)
    document = _scoped_document(db, project_id, doc_id)
    return serialize_document(document, job=_latest_job_for_document(db, document.id))


@router.get("/documents/{doc_id}/status")
def get_document_status(
    doc_id: UUID,
    project_id: UUID,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    require_project_access(db, principal, project_id)
    document = _scoped_document(db, project_id, doc_id)
    return {
        "id": str(document.id),
        "status": document.status,
        "chunks_count": len(document.chunks),
        "progress": 100 if document.status == "indexed" else None,
    }


@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: UUID,
    project_id: UUID,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    require_project_access(db, principal, project_id)
    document = (
        db.query(Document)
        .filter(Document.id == doc_id, Document.project_id == project_id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.deleted_at is not None:
        return {"success": True, "message": f"Document {doc_id} deleted"}
    now = utc_now()
    retention_until = now + timedelta(days=settings.STORAGE_RETENTION_DAYS)
    document.deleted_at = now
    document.updated_at = now
    document.status = "deleted"
    version_ids = [version.id for version in document.versions]
    if version_ids:
        (
            db.query(StorageObject)
            .filter(
                StorageObject.version_id.in_(version_ids),
                StorageObject.status == "referenced",
            )
            .update(
                {StorageObject.retention_until: retention_until},
                synchronize_session=False,
            )
        )
    db.commit()
    return {
        "success": True,
        "message": f"Document {doc_id} deleted",
        "retention_until": retention_until.isoformat(),
    }
