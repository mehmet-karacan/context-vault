"""Ingestion Celery tasks (Aşama 2.3).

Defines ``process_ingestion_job``, the worker-side task that turns a queued
``IngestionJob`` into fully-indexed chunks for a ``DocumentVersion``.

This module deliberately does NOT duplicate the parsing/chunking logic that
already exists in the current synchronous upload endpoint — it imports and
reuses ``extract_text`` / ``chunk_text`` from ``api.v1.documents``, and
``embed_texts`` / ``PASSAGE_INSTRUCTION`` from ``src.llm``, exactly as that
endpoint does today. Structural (DOCX table/heading-aware, PDF/Docling, OCR)
parsing arrives in later stages (Aşama 3) and will replace ``extract_text``
without changing this task's stage machine.

Contract this task assumes (owned by whichever caller enqueues the job — the
job-based upload endpoint, which is a *later* stage and is NOT wired up as
of Aşama 2.3; the current synchronous ``/documents/upload`` endpoint is left
untouched):

1. A ``Document`` row exists.
2. A ``DocumentVersion`` row exists for it (``version_no`` set, ``status``
   typically "pending") with ``storage_key`` already pointing at the
   *original* file object already written to MinIO (Aşama 2.2's
   ``object_keys.original_key(...)``).
3. An ``IngestionJob`` row exists referencing that version, with
   ``status="queued"``.

This task NEVER changes ``documents.active_version_id`` until the new
version is fully indexed and ready (Aşama 2 kabul kriteri): the previously
active version keeps serving reads for the entire run, and is only swapped
in the final "activating" stage once every chunk/embedding has been written
successfully.

IMPORTANT (Aşama 2.3 scope / known limitation): the tables this task reads
and writes (``ingestion_jobs``, ``document_versions``, ``source_files``,
``document_artifacts``, plus the additive columns on ``documents``/``chunks``)
are defined in ``models.py`` / the Alembic migrations from Aşama 2.1 but have
NOT been applied to the real database yet (see MIGRATION_RUNBOOK.md at the
repo root). Do not run this task against the real Postgres until that
migration has been applied — it will fail with
``UndefinedTable``/``UndefinedColumn`` errors, which is expected. Use
``tests/test_ingestion_tasks.py`` (fully mocked DB session) to validate the
stage-machine and idempotency logic in the meantime.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from datetime import datetime
from typing import Callable, List, Optional

from sqlalchemy.orm import Session

from ..api.v1.documents import chunk_text, extract_text
from ..config import settings
from ..db import SessionLocal
from ..infrastructure.storage import object_keys
from ..infrastructure.storage.minio_storage import MinioObjectStorage
from ..llm import PASSAGE_INSTRUCTION, embed_texts
from ..models import (
    EMBEDDING_DIMENSION,
    Chunk,
    ChunkEmbedding,
    Document,
    DocumentArtifact,
    DocumentVersion,
    EmbeddingProfile,
    IngestionEvent,
    IngestionJob,
)
from .celery_app import celery_app

# Stage order for a "document" ingestion (Aşama 2.3 scope). Repository/OCR
# specific stages (``ocr``, ``normalizing`` for structural parsing — see the
# broader enum on ``IngestionJob.stage`` in models.py) are introduced in
# Aşama 3+ without changing this list's shape.
STAGES = (
    "validating",
    "storing",
    "parsing",
    "chunking",
    "embedding",
    "indexing",
    "activating",
)

DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50


class IngestionJobError(RuntimeError):
    """Raised for validation-level failures (missing job/version/document,
    unreadable source content, etc.)."""


def _build_storage() -> MinioObjectStorage:
    return MinioObjectStorage(
        endpoint=settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        bucket=settings.MINIO_BUCKET,
    )


def _emit_event(
    db: Session, job: IngestionJob, stage: str, status: str, message: Optional[str] = None
) -> None:
    db.add(
        IngestionEvent(
            id=uuid.uuid4(),
            job_id=job.id,
            stage=stage,
            status=status,
            message=message,
            created_at=datetime.utcnow(),
        )
    )


def _advance_stage(db: Session, job: IngestionJob, stage: str) -> None:
    job.stage = stage
    _emit_event(db, job, stage=stage, status="started")
    db.commit()


def _get_or_create_active_embedding_profile(db: Session) -> EmbeddingProfile:
    """Returns the single active embedding profile, creating it from the
    current ``settings.EMBEDDING_MODEL`` config if none exists yet.

    Never creates a second active profile: if one is already active it is
    reused as-is, even if its recorded ``model``/``dimension`` differ from
    current settings — a genuine model/profile change is a controlled
    re-index operation (AKTIF_GOREV.md Bölüm 4.10), not something this task
    decides unilaterally mid-ingestion.
    """
    profile = (
        db.query(EmbeddingProfile)
        .filter(EmbeddingProfile.is_active.is_(True))
        .order_by(EmbeddingProfile.created_at.desc())
        .first()
    )
    if profile is not None:
        return profile

    profile = EmbeddingProfile(
        id=uuid.uuid4(),
        provider="openai-compatible",
        model=settings.EMBEDDING_MODEL,
        dimension=EMBEDDING_DIMENSION,
        distance_metric="cosine",
        profile_version=1,
        is_active=True,
        created_at=datetime.utcnow(),
    )
    db.add(profile)
    db.flush()
    return profile


def _clear_existing_chunks_for_version(db: Session, version_id) -> None:
    """Idempotency guard: wipes any chunks a previous (partial or complete)
    attempt at this same job/version may have already written, before
    re-inserting fresh ones.

    ``chunk_embeddings`` rows cascade at the DB FK level
    (``ondelete="CASCADE"`` on ``chunk_embeddings.chunk_id``), so deleting
    ``Chunk`` rows is sufficient — no separate ``ChunkEmbedding`` delete is
    needed.

    This makes retries/redeliveries safe: whether the previous attempt died
    mid-embedding or after a full success, re-running never leaves duplicate
    chunk rows behind — it always converges on exactly one chunk set per
    version (Aşama 2 kabul kriteri: "Aynı job tekrar alınırsa duplicate
    chunk/embedding oluşmaz").
    """
    db.query(Chunk).filter(Chunk.version_id == version_id).delete(synchronize_session=False)
    db.commit()


def run_ingestion_job(
    db: Session,
    job_id,
    storage,
    extract_text_fn: Callable[[str, str], str] = extract_text,
    chunk_text_fn: Callable[..., List[str]] = chunk_text,
    embed_texts_fn: Callable[..., List[List[float]]] = embed_texts,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> dict:
    """Runs one ingestion job to completion (or failure) on ``db``.

    This is the pure(ish) core of ``process_ingestion_job`` — every external
    dependency (``db``, ``storage``, text extraction, chunking, embedding) is
    injected so this function is unit-testable against a fully mocked
    session/storage without a real Postgres, MinIO or LLM gateway (see
    ``tests/test_ingestion_tasks.py``).
    """
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise IngestionJobError(f"IngestionJob {job_id} not found")

    # --- Idempotency: a job that already finished successfully is a pure
    # no-op on redelivery/retry. Never re-embed, re-write artifacts, or
    # re-activate a version that's already active.
    if job.status == "completed":
        return {"job_id": str(job_id), "status": "completed", "skipped": True}

    version = db.get(DocumentVersion, job.version_id)
    if version is None:
        job.status = "failed"
        job.error_code = "version_not_found"
        job.error_message = f"DocumentVersion {job.version_id} not found"
        job.finished_at = datetime.utcnow()
        _emit_event(db, job, stage="validating", status="failed", message=job.error_message)
        db.commit()
        raise IngestionJobError(job.error_message)

    document = db.get(Document, version.document_id)
    if document is None:
        job.status = "failed"
        job.error_code = "document_not_found"
        job.error_message = f"Document {version.document_id} not found"
        job.finished_at = datetime.utcnow()
        _emit_event(db, job, stage="validating", status="failed", message=job.error_message)
        db.commit()
        raise IngestionJobError(job.error_message)

    job.status = "running"
    job.attempt = (job.attempt or 0) + 1
    job.started_at = datetime.utcnow()
    job.error_code = None
    job.error_message = None
    db.commit()

    try:
        # --- validating -----------------------------------------------
        _advance_stage(db, job, "validating")
        if not version.storage_key:
            raise IngestionJobError(
                "DocumentVersion.storage_key is empty; original was never stored"
            )

        # --- storing (fetch + register the original artifact) ----------
        _advance_stage(db, job, "storing")
        original_bytes = storage.get(version.storage_key)
        original_artifact = (
            db.query(DocumentArtifact)
            .filter(
                DocumentArtifact.version_id == version.id,
                DocumentArtifact.artifact_type == "original",
            )
            .first()
        )
        if original_artifact is None:
            db.add(
                DocumentArtifact(
                    id=uuid.uuid4(),
                    version_id=version.id,
                    artifact_type="original",
                    storage_key=version.storage_key,
                    size_bytes=len(original_bytes),
                    created_at=datetime.utcnow(),
                )
            )
            db.commit()

        # --- parsing -----------------------------------------------------
        _advance_stage(db, job, "parsing")
        suffix = os.path.splitext(document.name or "")[1]
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                tmp_file.write(original_bytes)
                tmp_path = tmp_file.name
            text = extract_text_fn(tmp_path, document.name)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        if not text.strip():
            raise IngestionJobError("Belgenin okunabilir metin içeriği bulunamadı.")

        # Persist the normalized artifact. Plain-text passthrough at this
        # stage — structural DOCX/PDF -> Markdown conversion arrives with
        # the Aşama 3 parser router without changing this task's shape.
        normalized_key = object_keys.normalized_markdown_key(
            str(document.project_id), str(document.id), str(version.id)
        )
        storage.put(normalized_key, text.encode("utf-8"), content_type="text/markdown")

        normalized_artifact = (
            db.query(DocumentArtifact)
            .filter(
                DocumentArtifact.version_id == version.id,
                DocumentArtifact.artifact_type == "normalized_md",
            )
            .first()
        )
        if normalized_artifact is None:
            normalized_artifact = DocumentArtifact(
                id=uuid.uuid4(),
                version_id=version.id,
                artifact_type="normalized_md",
                storage_key=normalized_key,
                size_bytes=len(text.encode("utf-8")),
                created_at=datetime.utcnow(),
            )
            db.add(normalized_artifact)
            db.flush()
        version.normalized_artifact_id = normalized_artifact.id
        db.commit()

        # --- chunking ------------------------------------------------------
        _advance_stage(db, job, "chunking")
        chunks = chunk_text_fn(text, chunk_size=chunk_size, overlap=chunk_overlap)
        if not chunks:
            raise IngestionJobError("Chunking produced zero chunks")

        # --- embedding -----------------------------------------------------
        _advance_stage(db, job, "embedding")
        embeddings = embed_texts_fn(chunks, instruction=PASSAGE_INSTRUCTION)
        if len(embeddings) != len(chunks):
            raise IngestionJobError("Embedding count does not match chunk count")

        # --- indexing (idempotent: wipe + rewrite this version's chunks) --
        _advance_stage(db, job, "indexing")
        _clear_existing_chunks_for_version(db, version.id)
        profile = _get_or_create_active_embedding_profile(db)

        for index, (content, embedding) in enumerate(zip(chunks, embeddings)):
            chunk = Chunk(
                id=uuid.uuid4(),
                document_id=document.id,
                version_id=version.id,
                chunk_index=index,
                sequence_no=index,
                chunk_type="text",
                content=content,
                embedding=embedding,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                created_at=datetime.utcnow(),
            )
            db.add(chunk)
            db.flush()
            db.add(
                ChunkEmbedding(
                    chunk_id=chunk.id,
                    embedding_profile_id=profile.id,
                    embedding=embedding,
                    created_at=datetime.utcnow(),
                )
            )
        db.commit()

        # --- activating ------------------------------------------------
        _advance_stage(db, job, "activating")
        version.status = "ready"
        version.activated_at = datetime.utcnow()
        document.active_version_id = version.id
        document.updated_at = datetime.utcnow()
        job.status = "completed"
        job.finished_at = datetime.utcnow()
        _emit_event(db, job, stage="activating", status="completed")
        db.commit()

        return {
            "job_id": str(job_id),
            "status": "completed",
            "version_id": str(version.id),
            "chunks": len(chunks),
        }

    except Exception as exc:
        db.rollback()
        job = db.get(IngestionJob, job_id)  # re-fetch: rollback expired instances
        if job is not None:
            job.status = "failed"
            job.error_code = type(exc).__name__
            job.error_message = str(exc)
            job.finished_at = datetime.utcnow()
            _emit_event(db, job, stage=job.stage or "unknown", status="failed", message=str(exc))
            db.commit()
        raise


@celery_app.task(name="ingestion.process_ingestion_job")
def process_ingestion_job(job_id: str) -> dict:
    """Celery task entrypoint: builds the real DB session and MinIO storage
    adapter, then runs the job to completion. Kept as a thin wrapper around
    ``run_ingestion_job`` (the actual state machine) so the logic is
    unit-testable without Celery, a real Postgres, or a real MinIO.
    """
    db = SessionLocal()
    try:
        storage = _build_storage()
        return run_ingestion_job(db=db, job_id=job_id, storage=storage)
    finally:
        db.close()
