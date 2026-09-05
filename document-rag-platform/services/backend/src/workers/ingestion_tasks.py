"""Worker adapter for the canonical ingestion orchestrator."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import uuid
from datetime import timedelta
from typing import Callable, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..application.ingestion_pipeline import chunk_source, parse_source
from ..config import settings
from ..db import SessionLocal
from ..domain.clock import Clock, SYSTEM_CLOCK
from ..domain.ingestion_state import JobStatus, transition_job
from ..domain.normalized_content import ContentUnit, NormalizedSource, UnitType
from ..domain.version_activation import activate_document_version
from ..infrastructure.embeddings.cache import profile_config_hash
from ..infrastructure.observability import continue_trace, metrics, traced
from ..infrastructure.security import redact_secrets
from ..infrastructure.retrieval.indexing import (
    build_search_vector_stmt,
    chunk_identifiers,
)
from ..infrastructure.storage import object_keys
from ..infrastructure.storage.minio_storage import MinioObjectStorage
from ..llm import PASSAGE_INSTRUCTION, embed_texts
from ..models import (
    EMBEDDING_DIMENSION,
    AuditEvent,
    Chunk,
    ChunkEmbedding,
    ContentPolicyDecisionRecord,
    Document,
    DocumentArtifact,
    DocumentVersion,
    EmbeddingProfile,
    IngestionEvent,
    IngestionAttempt,
    IngestionJob,
    IngestionReceipt,
    InboxReceipt,
    SourceFile,
    StorageObject,
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
DEFAULT_LEASE_SECONDS = settings.INGESTION_LEASE_SECONDS


def _legacy_text_as_source(text_value: str, version_id) -> NormalizedSource:
    """Compatibility bridge for injected unit-test parsers, never an API path."""
    return NormalizedSource(
        source_id=str(uuid.uuid4()),
        version_id=str(version_id),
        source_type="plain_text",
        units=[
            ContentUnit(
                unit_id="legacy-test:1",
                unit_type=UnitType.PARAGRAPH,
                text=text_value,
                markdown=text_value,
                order=1,
            )
        ],
    )


class IngestionJobError(RuntimeError):
    """Raised for permanent validation-level failures (missing
    job/version/document, unreadable source content, zero chunks, embedding
    count mismatch, etc.). A permanent failure is never retried: the job ends
    ``failed`` and no automatic redelivery happens (Aşama 2.5)."""


class StageTransitionError(IngestionJobError):
    """Raised when an ingestion job tries to move to a stage that is not the
    documented successor of its current stage. This indicates a code-level
    state bug rather than a transient environment hiccup, so it is treated as
    permanent (see ``_validate_stage_transition``)."""


class JobCancelled(IngestionJobError):
    """Raised only at a stage boundary after a durable cancel request."""


class RetryableIngestionError(RuntimeError):
    """Raised for transient, environment-dependent failures (embedding gateway
    timeouts, MinIO connection blips, DB connection drops, etc.) where a
    later retry has a real chance of succeeding. With Celery ``autoretry``
    (see ``process_ingestion_job``) this exception triggers an automatic retry
    with exponential backoff up to ``settings.INGESTION_MAX_RETRIES``. The
    job is still marked ``failed`` on this attempt (so the durable status and
    events are accurate), but a fresh attempt rewinds its stage to
    ``validating`` and re-runs the idempotent pipeline."""


def _build_storage() -> MinioObjectStorage:
    return MinioObjectStorage(
        endpoint=settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        bucket=settings.MINIO_BUCKET,
        encryption_key=settings.OBJECT_STORAGE_ENCRYPTION_KEY,
        allow_legacy_plaintext_reads=settings.OBJECT_STORAGE_ALLOW_LEGACY_PLAINTEXT_READS,
    )


def _emit_event(
    db: Session,
    job: IngestionJob,
    stage: str,
    status: str,
    message: Optional[str] = None,
    clock: Clock = SYSTEM_CLOCK,
) -> None:
    if getattr(job, "cancel_requested_at", None) is not None:
        raise JobCancelled("ingestion cancelled at a safe stage boundary")
    db.add(
        IngestionEvent(
            id=uuid.uuid4(),
            job_id=job.id,
            stage=stage,
            status=status,
            message=message,
            created_at=clock.now(),
        )
    )


def _emit_receipt(
    db: Session,
    job: IngestionJob,
    attempt: Optional[IngestionAttempt],
    *,
    stage: str,
    status: str,
    error_code: Optional[str] = None,
    evidence_hash: Optional[str] = None,
    clock: Clock = SYSTEM_CLOCK,
) -> None:
    if attempt is None:
        return
    db.add(
        IngestionReceipt(
            id=uuid.uuid4(),
            job_id=job.id,
            attempt_id=attempt.id,
            stage=stage,
            status=status,
            error_code=error_code,
            evidence_hash=evidence_hash,
            metadata_json={},
            created_at=clock.now(),
        )
    )


def _claim_job(
    db: Session,
    job: IngestionJob,
    *,
    worker_id: str,
    celery_task_id: Optional[str],
    clock: Clock,
    lease_seconds: int,
) -> tuple[IngestionJob, Optional[IngestionAttempt]]:
    """Atomically claim before effects; expired leases are safely reclaimable."""
    now = clock.now()
    expires = now + timedelta(seconds=lease_seconds)
    if not hasattr(db, "execute"):
        transition_job(job, JobStatus.RUNNING)
        job.attempt = (job.attempt or 0) + 1
        job.lease_owner = worker_id
        job.lease_expires_at = expires
        job.heartbeat_at = now
        return job, None

    claimed = db.execute(
        text(
            """
            UPDATE ingestion_jobs
            SET status='running', attempt=attempt+1, lease_owner=:owner,
                lease_expires_at=:expires, heartbeat_at=:now,
                started_at=COALESCE(started_at,:now),
                error_code=NULL, error_message=NULL
            WHERE id=:job_id
              AND status IN ('queued','retrying','running')
              AND (status <> 'running' OR lease_expires_at IS NULL OR lease_expires_at < :now)
            RETURNING attempt
            """
        ),
        {"owner": worker_id, "expires": expires, "now": now, "job_id": job.id},
    ).first()
    if claimed is None:
        db.rollback()
        current = db.get(IngestionJob, job.id)
        if current is not None and current.status == "completed":
            return current, None
        raise RetryableIngestionError(
            "ingestion job is already leased by another worker"
        )
    attempt = IngestionAttempt(
        id=uuid.uuid4(),
        job_id=job.id,
        attempt_no=int(claimed[0]),
        worker_id=worker_id,
        celery_task_id=celery_task_id,
        status="running",
        claimed_at=now,
        lease_expires_at=expires,
        heartbeat_at=now,
    )
    db.add(attempt)
    db.commit()
    db.expire_all()
    return db.get(IngestionJob, job.id), attempt


def _validate_stage_transition(current, new) -> None:
    """Enforces the documented stage machine on ``STAGES``.

    A job may only ever transition to the *immediate successor* of its current
    stage, with one deliberate exception: rewinding to ``STAGES[0]``
    (``validating``) is always allowed, because a retried job restarts its
    pipeline from the top (see ``RetryableIngestionError``). Everything else —
    skipping a stage, jumping backwards, starting at a non-first stage, or
    naming a stage that is not in ``STAGES`` — is a state-machine bug and
    raises ``StageTransitionError`` (permanent; never retried).
    """
    if new not in STAGES:
        raise StageTransitionError(f"Unknown ingestion stage: {new!r}")
    if new == STAGES[0]:
        # Fresh start, or a retried job rewinding to the beginning. Both are
        # valid; the pipeline always runs forward from here.
        return
    if current is None or current not in STAGES:
        raise StageTransitionError(
            f"Cannot start a job at stage {new!r} with current stage {current!r}"
        )
    current_idx = STAGES.index(current)
    expected = STAGES[current_idx + 1] if current_idx + 1 < len(STAGES) else None
    if new != expected:
        raise StageTransitionError(
            f"Invalid stage transition {current!r} -> {new!r}; expected "
            f"{expected!r} (next stage) or {STAGES[0]!r} (restart)"
        )


def _advance_stage(
    db: Session,
    job: IngestionJob,
    stage: str,
    clock: Clock = SYSTEM_CLOCK,
    attempt: Optional[IngestionAttempt] = None,
) -> None:
    _validate_stage_transition(job.stage, stage)
    job.stage = stage
    job.heartbeat_at = clock.now()
    if job.lease_owner:
        job.lease_expires_at = clock.now() + timedelta(seconds=DEFAULT_LEASE_SECONDS)
    if attempt is not None:
        attempt.heartbeat_at = job.heartbeat_at
        attempt.lease_expires_at = job.lease_expires_at
    _emit_event(db, job, stage=stage, status="started", clock=clock)
    _emit_receipt(db, job, attempt, stage=stage, status="started", clock=clock)
    db.commit()


def _get_or_create_active_embedding_profile(
    db: Session, clock: Clock = SYSTEM_CLOCK
) -> EmbeddingProfile:
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
        created_at=clock.now(),
    )
    profile.config_hash = profile_config_hash(profile)
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
    db.query(Chunk).filter(Chunk.version_id == version_id).delete(
        synchronize_session=False
    )
    db.commit()


def _register_referenced_object(
    db: Session,
    *,
    key: str,
    checksum: str,
    size_bytes: int,
    version_id,
    artifact_id,
    clock: Clock,
) -> None:
    """Idempotently bind an immutable artifact to the storage registry."""
    existing = db.query(StorageObject).filter(StorageObject.storage_key == key).first()
    if existing is not None:
        return
    db.add(
        StorageObject(
            id=uuid.uuid4(),
            storage_key=key,
            checksum=checksum,
            size_bytes=size_bytes,
            status="referenced",
            version_id=version_id,
            artifact_id=artifact_id,
            created_at=clock.now(),
            referenced_at=clock.now(),
        )
    )


@traced("ingestion.process")
def run_ingestion_job(
    db: Session,
    job_id,
    storage,
    extract_text_fn: Callable = parse_source,
    chunk_text_fn: Callable = chunk_source,
    embed_texts_fn: Callable[..., List[List[float]]] = embed_texts,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    clock: Clock = SYSTEM_CLOCK,
    worker_id: Optional[str] = None,
    celery_task_id: Optional[str] = None,
    inbox_idempotency_key: Optional[str] = None,
    embedding_is_remote: Optional[bool] = None,
    checkpoint_hook: Optional[Callable[[str], None]] = None,
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
    if job.status in {"failed", "cancelled"}:
        raise IngestionJobError(f"IngestionJob {job_id} is terminal ({job.status})")

    worker_identity = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    job, attempt = _claim_job(
        db,
        job,
        worker_id=worker_identity,
        celery_task_id=celery_task_id,
        clock=clock,
        lease_seconds=DEFAULT_LEASE_SECONDS,
    )
    if job.status == "completed":
        return {"job_id": str(job_id), "status": "completed", "skipped": True}
    if inbox_idempotency_key and hasattr(db, "execute"):
        existing_receipt = (
            db.query(InboxReceipt)
            .filter(
                InboxReceipt.consumer == "ingestion-worker",
                InboxReceipt.idempotency_key == inbox_idempotency_key,
            )
            .first()
        )
        if existing_receipt is not None and existing_receipt.status == "completed":
            return {"job_id": str(job_id), "status": "completed", "skipped": True}
        if existing_receipt is None:
            db.add(
                InboxReceipt(
                    id=uuid.uuid4(),
                    consumer="ingestion-worker",
                    idempotency_key=inbox_idempotency_key,
                    job_id=job.id,
                    status="received",
                    received_at=clock.now(),
                )
            )
            db.commit()

    version = db.get(DocumentVersion, job.version_id)
    if version is None:
        transition_job(job, JobStatus.FAILED)
        job.error_code = "version_not_found"
        job.error_message = f"DocumentVersion {job.version_id} not found"
        job.finished_at = clock.now()
        _emit_event(
            db,
            job,
            stage="validating",
            status="failed",
            message=job.error_message,
            clock=clock,
        )
        db.commit()
        raise IngestionJobError(job.error_message)

    document = db.get(Document, version.document_id)
    if document is None:
        transition_job(job, JobStatus.FAILED)
        job.error_code = "document_not_found"
        job.error_message = f"Document {version.document_id} not found"
        job.finished_at = clock.now()
        _emit_event(
            db,
            job,
            stage="validating",
            status="failed",
            message=job.error_message,
            clock=clock,
        )
        db.commit()
        raise IngestionJobError(job.error_message)

    expected_active_version_id = document.active_version_id
    job.started_at = job.started_at or clock.now()
    # Mirror job progress onto documents.status using the same vocabulary the
    # legacy synchronous upload path already writes ("uploaded" / "processing"
    # / "indexed" / "error") so existing frontend status labels keep working
    # unchanged for async-ingested documents too (Aşama 2.4).
    document.status = "processing"
    document.updated_at = clock.now()
    db.commit()

    try:
        # --- validating -----------------------------------------------
        _advance_stage(db, job, "validating", clock, attempt)
        if not version.storage_key:
            raise IngestionJobError(
                "DocumentVersion.storage_key is empty; original was never stored"
            )

        # --- storing (fetch + register the original artifact) ----------
        _advance_stage(db, job, "storing", clock, attempt)
        staging_storage_key = version.storage_key
        original_bytes = storage.get(staging_storage_key)
        original_checksum = hashlib.sha256(original_bytes).hexdigest()
        original_artifact = (
            db.query(DocumentArtifact)
            .filter(
                DocumentArtifact.version_id == version.id,
                DocumentArtifact.artifact_type == "original",
            )
            .first()
        )
        expected_checksum = (
            original_artifact.checksum
            if original_artifact is not None and original_artifact.checksum
            else document.checksum
        )
        if expected_checksum and original_checksum != expected_checksum:
            raise IngestionJobError("original artifact checksum mismatch")
        if original_artifact is None:
            original_artifact = DocumentArtifact(
                id=uuid.uuid4(),
                version_id=version.id,
                artifact_type="original",
                storage_key=staging_storage_key,
                checksum=original_checksum,
                size_bytes=len(original_bytes),
                created_at=clock.now(),
            )
            db.add(original_artifact)
            db.flush()
        if staging_storage_key.startswith("staging/"):
            final_key = object_keys.immutable_original_key(
                str(document.project_id),
                str(document.id),
                str(version.id),
                str(original_artifact.id),
                original_checksum,
                document.name,
            )
            storage.put(final_key, original_bytes, content_type=document.mime_type)
            if hashlib.sha256(storage.get(final_key)).hexdigest() != original_checksum:
                raise IngestionJobError("final original artifact checksum mismatch")
            original_artifact.storage_key = final_key
            original_artifact.checksum = original_checksum
            original_artifact.metadata_json = {"state": "immutable"}
            version.storage_key = final_key
            staging_record = (
                db.query(StorageObject)
                .filter(StorageObject.storage_key == staging_storage_key)
                .first()
            )
            if staging_record is not None:
                staging_record.status = "deleted"
                staging_record.deleted_at = clock.now()
            if (
                db.query(StorageObject)
                .filter(StorageObject.storage_key == final_key)
                .first()
                is None
            ):
                db.add(
                    StorageObject(
                        id=uuid.uuid4(),
                        storage_key=final_key,
                        checksum=original_checksum,
                        size_bytes=len(original_bytes),
                        status="referenced",
                        version_id=version.id,
                        artifact_id=original_artifact.id,
                        created_at=clock.now(),
                        referenced_at=clock.now(),
                    )
                )
            db.commit()
            storage.delete(staging_storage_key)
        else:
            original_artifact.checksum = original_checksum
            db.commit()
        if checkpoint_hook is not None:
            checkpoint_hook("after_storing")

        # --- parsing -----------------------------------------------------
        _advance_stage(db, job, "parsing", clock, attempt)
        suffix = os.path.splitext(document.name or "")[1]
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                tmp_file.write(original_bytes)
                tmp_path = tmp_file.name
            parsed = (
                extract_text_fn(tmp_path, document.name, document.mime_type)
                if extract_text_fn is parse_source
                else extract_text_fn(tmp_path, document.name)
            )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        normalized = (
            parsed
            if isinstance(parsed, NormalizedSource)
            else _legacy_text_as_source(str(parsed), version.id)
        )
        normalized.version_id = str(version.id)
        source_file_by_path: dict[str, SourceFile] = {}
        source_manifest = normalized.metadata.get("source_files") or []
        if isinstance(source_manifest, list):
            for item in source_manifest:
                if not isinstance(item, dict):
                    continue
                relative_path = item.get("relative_path")
                if not isinstance(relative_path, str) or not relative_path:
                    continue
                source_file = (
                    db.query(SourceFile)
                    .filter(
                        SourceFile.version_id == version.id,
                        SourceFile.relative_path == relative_path,
                    )
                    .first()
                )
                if source_file is None:
                    source_file = SourceFile(
                        id=uuid.uuid4(),
                        version_id=version.id,
                        relative_path=relative_path,
                        language=item.get("language"),
                        mime_type=item.get("mime_type"),
                        size_bytes=item.get("size_bytes"),
                        content_hash=item.get("content_hash"),
                        is_binary=False,
                        is_generated=bool(item.get("is_generated")),
                        is_ignored=False,
                        metadata_json=item.get("metadata") or {},
                    )
                    db.add(source_file)
                    db.flush([source_file])
                source_file_by_path[relative_path] = source_file
        rendered_text = "\n\n".join(
            (unit.markdown or unit.text).strip()
            for unit in normalized.units
            if (unit.markdown or unit.text).strip()
        )
        if not rendered_text.strip():
            raise IngestionJobError("Belgenin okunabilir metin içeriği bulunamadı.")

        policy = None
        policy_id = getattr(version, "content_policy_decision_id", None)
        if policy_id:
            policy = db.get(ContentPolicyDecisionRecord, policy_id)
        if policy is not None and not policy.permit_normalized_storage:
            raise IngestionJobError("content policy forbids normalized storage")

        normalized_json = json.dumps(
            normalized.to_dict(), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        normalized_json_storage_key = object_keys.normalized_json_key(
            str(document.project_id), str(document.id), str(version.id)
        )
        normalized_key = object_keys.normalized_markdown_key(
            str(document.project_id), str(document.id), str(version.id)
        )
        storage.put(
            normalized_json_storage_key,
            normalized_json,
            content_type="application/json",
        )
        storage.put(
            normalized_key,
            rendered_text.encode("utf-8"),
            content_type="text/markdown",
        )

        normalized_artifact = (
            db.query(DocumentArtifact)
            .filter(
                DocumentArtifact.version_id == version.id,
                DocumentArtifact.artifact_type == "normalized_json",
            )
            .first()
        )
        if normalized_artifact is None:
            normalized_artifact = DocumentArtifact(
                id=uuid.uuid4(),
                version_id=version.id,
                artifact_type="normalized_json",
                storage_key=normalized_json_storage_key,
                checksum=hashlib.sha256(normalized_json).hexdigest(),
                size_bytes=len(normalized_json),
                created_at=clock.now(),
            )
            db.add(normalized_artifact)
            db.flush()
        _register_referenced_object(
            db,
            key=normalized_json_storage_key,
            checksum=hashlib.sha256(normalized_json).hexdigest(),
            size_bytes=len(normalized_json),
            version_id=version.id,
            artifact_id=normalized_artifact.id,
            clock=clock,
        )
        normalized_markdown = (
            db.query(DocumentArtifact)
            .filter(
                DocumentArtifact.version_id == version.id,
                DocumentArtifact.artifact_type == "normalized_md",
            )
            .first()
        )
        if normalized_markdown is None:
            markdown_bytes = rendered_text.encode("utf-8")
            normalized_markdown = DocumentArtifact(
                id=uuid.uuid4(),
                version_id=version.id,
                artifact_type="normalized_md",
                storage_key=normalized_key,
                checksum=hashlib.sha256(markdown_bytes).hexdigest(),
                size_bytes=len(markdown_bytes),
                created_at=clock.now(),
            )
            db.add(normalized_markdown)
            db.flush()
        markdown_bytes = rendered_text.encode("utf-8")
        _register_referenced_object(
            db,
            key=normalized_key,
            checksum=hashlib.sha256(markdown_bytes).hexdigest(),
            size_bytes=len(markdown_bytes),
            version_id=version.id,
            artifact_id=normalized_markdown.id,
            clock=clock,
        )
        version.normalized_artifact_id = normalized_artifact.id
        db.commit()
        if checkpoint_hook is not None:
            checkpoint_hook("after_parsing")

        # --- chunking ------------------------------------------------------
        _advance_stage(db, job, "chunking", clock, attempt)
        chunk_candidates = chunk_text_fn(
            normalized, chunk_size=chunk_size, overlap=chunk_overlap
        )
        if not chunk_candidates:
            raise IngestionJobError("Chunking produced zero chunks")
        chunk_contents = [
            redact_secrets(
                candidate.content if hasattr(candidate, "content") else str(candidate)
            )
            for candidate in chunk_candidates
        ]
        embedding_texts = [
            redact_secrets(
                candidate.embedding_text
                if hasattr(candidate, "embedding_text")
                else str(candidate)
            )
            for candidate in chunk_candidates
        ]

        # --- embedding -----------------------------------------------------
        _advance_stage(db, job, "embedding", clock, attempt)
        remote = (
            embed_texts_fn is embed_texts
            if embedding_is_remote is None
            else embedding_is_remote
        )
        if policy is not None and remote and not policy.permit_remote_embedding:
            raise IngestionJobError("content policy forbids remote embedding")
        if remote:
            if job.actor_principal_id is None or job.workspace_id is None:
                raise IngestionJobError(
                    "remote embedding requires an attributable ingestion actor"
                )
            db.add(
                AuditEvent(
                    id=uuid.uuid4(),
                    actor_principal_id=job.actor_principal_id,
                    workspace_id=job.workspace_id,
                    project_id=document.project_id,
                    event_type="ingestion.remote_embedding_authorized",
                    metadata_json={
                        "document_id": str(document.id),
                        "version_id": str(version.id),
                        "content_policy_decision_id": str(policy.id)
                        if policy is not None
                        else None,
                        "classification": policy.classification
                        if policy is not None
                        else document.data_classification,
                        "policy_version": policy.policy_version
                        if policy is not None
                        else None,
                        "provider": "openai-compatible",
                        "model": settings.EMBEDDING_MODEL,
                    },
                    created_at=clock.now(),
                )
            )
            db.commit()
        embeddings = embed_texts_fn(embedding_texts, instruction=PASSAGE_INSTRUCTION)
        if len(embeddings) != len(chunk_candidates):
            raise IngestionJobError("Embedding count does not match chunk count")
        if any(len(embedding) != EMBEDDING_DIMENSION for embedding in embeddings):
            raise IngestionJobError(
                f"Embedding dimension must be {EMBEDDING_DIMENSION}"
            )
        if checkpoint_hook is not None:
            checkpoint_hook("after_embedding")

        # --- indexing (idempotent: wipe + rewrite this version's chunks) --
        _advance_stage(db, job, "indexing", clock, attempt)
        _clear_existing_chunks_for_version(db, version.id)
        profile_id = getattr(version, "embedding_profile_id", None)
        profile = db.get(EmbeddingProfile, profile_id) if profile_id else None
        if profile is None:
            profile = _get_or_create_active_embedding_profile(db, clock)

        for index, (candidate, content, embedding) in enumerate(
            zip(chunk_candidates, chunk_contents, embeddings)
        ):
            locator = getattr(candidate, "locator", {}) or {}
            source_file = source_file_by_path.get(locator.get("file_path"))
            chunk = Chunk(
                id=uuid.uuid4(),
                document_id=document.id,
                version_id=version.id,
                source_file_id=source_file.id if source_file is not None else None,
                chunk_index=index,
                sequence_no=index,
                chunk_type=getattr(candidate, "chunk_type", "text"),
                content=content,
                identifiers=chunk_identifiers(content),
                content_hash=getattr(candidate, "content_hash", None)
                or hashlib.sha256(content.encode("utf-8")).hexdigest(),
                heading_path=getattr(candidate, "heading_path", None),
                page_start=locator.get("page_start"),
                page_end=locator.get("page_end"),
                line_start=locator.get("line_start"),
                line_end=locator.get("line_end"),
                bbox=locator.get("bbox"),
                symbol_name=locator.get("symbol_name"),
                symbol_type=locator.get("symbol_type"),
                symbol_qualified_name=locator.get("symbol_qualified_name"),
                package_name=locator.get("package_name"),
                schema_name=locator.get("schema_name"),
                table_name=locator.get("table_name"),
                column_name=locator.get("column_name"),
                token_count=getattr(candidate, "token_count", None),
                metadata_json=getattr(candidate, "metadata", None),
                search_profile="simple-websearch-v1",
                created_at=clock.now(),
            )
            db.add(chunk)
            db.flush()
            db.add(
                ChunkEmbedding(
                    chunk_id=chunk.id,
                    embedding_profile_id=profile.id,
                    embedding=embedding,
                    created_at=clock.now(),
                )
            )
        # Build the lexical (tsvector) index so LexicalRetriever can find these
        # chunks; identifiers were set per-chunk above (Aşama 5.2). Guarded for
        # the DB-free test doubles (which have no ``execute``): the real
        # SQLAlchemy session always supports it.
        if hasattr(db, "execute"):
            db.execute(
                build_search_vector_stmt(document.id), {"document_id": document.id}
            )
        db.commit()
        if checkpoint_hook is not None:
            checkpoint_hook("after_indexing")

        if hasattr(db, "execute"):
            smoke = db.execute(
                text(
                    """
                    SELECT c.id
                    FROM chunks c
                    JOIN chunk_embeddings ce ON ce.chunk_id = c.id
                    WHERE c.version_id = :version_id
                      AND ce.embedding_profile_id = :profile_id
                      AND c.search_vector IS NOT NULL
                    LIMIT 1
                    """
                ),
                {"version_id": version.id, "profile_id": profile.id},
            ).first()
            if smoke is None:
                raise IngestionJobError("candidate smoke retrieval failed")

        # --- activating ------------------------------------------------
        _advance_stage(db, job, "activating", clock, attempt)
        version.status = "ready"
        db.flush()
        if hasattr(db, "execute"):
            activate_document_version(
                db,
                document_id=document.id,
                version_id=version.id,
                expected_current_version_id=expected_active_version_id,
                clock=clock,
            )
        else:
            version.activated_at = clock.now()
            document.active_version_id = version.id
            document.status = "indexed"
            document.updated_at = clock.now()
        if expected_active_version_id and expected_active_version_id != version.id:
            previous = db.get(DocumentVersion, expected_active_version_id)
            if previous is not None:
                previous.status = "superseded"
                retirement_at = clock.now() + timedelta(
                    days=settings.STORAGE_RETENTION_DAYS
                )
                (
                    db.query(StorageObject)
                    .filter(
                        StorageObject.version_id == previous.id,
                        StorageObject.status == "referenced",
                        StorageObject.retention_until.is_(None),
                    )
                    .update(
                        {StorageObject.retention_until: retirement_at},
                        synchronize_session=False,
                    )
                )
        document.checksum = original_checksum
        document.size = len(original_bytes)
        document.mime_type = document.mime_type or "application/octet-stream"
        transition_job(job, JobStatus.COMPLETED)
        job.finished_at = clock.now()
        job.lease_owner = None
        job.lease_expires_at = None
        _emit_event(db, job, stage="activating", status="completed", clock=clock)
        _emit_receipt(
            db,
            job,
            attempt,
            stage="activating",
            status="completed",
            evidence_hash=hashlib.sha256(
                f"{version.id}:{len(chunk_candidates)}".encode("utf-8")
            ).hexdigest(),
            clock=clock,
        )
        if attempt is not None:
            attempt.status = "completed"
            attempt.finished_at = clock.now()
        if inbox_idempotency_key and hasattr(db, "execute"):
            inbox = (
                db.query(InboxReceipt)
                .filter(
                    InboxReceipt.consumer == "ingestion-worker",
                    InboxReceipt.idempotency_key == inbox_idempotency_key,
                )
                .first()
            )
            if inbox is not None:
                inbox.status = "completed"
                inbox.completed_at = clock.now()
        if checkpoint_hook is not None:
            checkpoint_hook("after_activation")
        db.commit()

        return {
            "job_id": str(job_id),
            "status": "completed",
            "version_id": str(version.id),
            "chunks": len(chunk_candidates),
        }

    except Exception as exc:
        db.rollback()
        job = db.get(IngestionJob, job_id)  # re-fetch: rollback expired instances
        if job is not None:
            if attempt is not None:
                attempt = db.get(IngestionAttempt, attempt.id)
            cancelled = isinstance(exc, JobCancelled)
            permanent = isinstance(exc, IngestionJobError)
            transition_job(
                job,
                JobStatus.CANCELLED
                if cancelled
                else JobStatus.FAILED
                if permanent
                else JobStatus.RETRYING,
            )
            job.error_code = type(exc).__name__
            job.error_message = str(exc)
            job.finished_at = clock.now() if permanent else None
            job.lease_owner = None
            job.lease_expires_at = None
            _emit_event(
                db,
                job,
                stage=job.stage or "validating",
                status="cancelled"
                if cancelled
                else "failed"
                if permanent
                else "retrying",
                message=str(exc),
                clock=clock,
            )
            _emit_receipt(
                db,
                job,
                attempt,
                stage=job.stage or "validating",
                status="cancelled"
                if cancelled
                else "failed"
                if permanent
                else "retrying",
                error_code=type(exc).__name__,
                clock=clock,
            )
            if attempt is not None:
                attempt.status = "cancelled" if cancelled else "failed"
                attempt.error_code = type(exc).__name__
                attempt.error_message = str(exc)
                attempt.finished_at = clock.now()
            # Best-effort: also surface the failure on documents.status (same
            # re-fetch-after-rollback pattern as ``job`` above). Never lets a
            # failure to resolve version/document mask the real job failure.
            failed_document = None
            failed_version = None
            try:
                failed_version = db.get(DocumentVersion, job.version_id)
                if failed_version is not None:
                    failed_document = db.get(Document, failed_version.document_id)
            except Exception:
                failed_document = None
            if failed_document is not None:
                if failed_document.active_version_id is None:
                    failed_document.status = "error"
                    failed_document.error_code = type(exc).__name__
                    failed_document.error_message = str(exc)
                else:
                    failed_document.status = "indexed"
                failed_document.updated_at = clock.now()
            if permanent and failed_version is not None:
                failed_version.status = "failed"
                failed_version.error_code = type(exc).__name__
                failed_version.error_message = str(exc)
            db.commit()

        # Permanent, code-level failures (validation, stage machine) propagate
        # unchanged so the Celery task does not retry them. Anything else is a
        # transient environment failure (gateway/MINIO/DB blip) and is wrapped
        # in RetryableIngestionError so autoretry picks it up.
        if isinstance(exc, IngestionJobError):
            raise
        raise RetryableIngestionError(str(exc)) from exc


@celery_app.task(
    name="ingestion.process_ingestion_job",
    autoretry_for=(RetryableIngestionError,),
    retry_kwargs={"max_retries": settings.INGESTION_MAX_RETRIES},
    retry_backoff=settings.INGESTION_RETRY_BACKOFF_SECONDS,
    retry_backoff_max=int(settings.INGESTION_RETRY_BACKOFF_MAX_SECONDS),
    retry_jitter=True,
)
def process_ingestion_job(
    job_id: str,
    inbox_idempotency_key: Optional[str] = None,
    traceparent: Optional[str] = None,
) -> dict:
    """Celery task entrypoint: builds the real DB session and MinIO storage
    adapter, then runs the job to completion. Kept as a thin wrapper around
    ``run_ingestion_job`` (the actual state machine) so the logic is
    unit-testable without Celery, a real Postgres, or a real MinIO.
    """
    db = SessionLocal()
    try:
        from ..application.ingestion_orchestrator import IngestionOrchestrator

        storage = _build_storage()
        task_id = getattr(getattr(process_ingestion_job, "request", None), "id", None)
        with continue_trace(traceparent):
            return IngestionOrchestrator(db, storage).process_job(
                job_id,
                celery_task_id=task_id,
                inbox_idempotency_key=inbox_idempotency_key,
            )
    finally:
        db.close()


@celery_app.task(name="ingestion.dispatch_outbox")
def dispatch_ingestion_outbox(limit: int = 100) -> dict:
    """Retry durable queue intents; publish errors remain in PostgreSQL."""
    from ..application.ingestion_orchestrator import OutboxDispatcher

    db = SessionLocal()
    try:
        dispatcher = OutboxDispatcher(
            db,
            lambda job_id, key, traceparent: process_ingestion_job.delay(
                job_id, key, traceparent
            ),
            SYSTEM_CLOCK,
        )
        return {"published": dispatcher.dispatch_pending(limit=limit)}
    finally:
        db.close()


@celery_app.task(name="ingestion.reconcile_stale_leases")
def reconcile_ingestion_leases() -> dict:
    from ..application.ingestion_maintenance import reconcile_stale_leases

    db = SessionLocal()
    try:
        reconciled = reconcile_stale_leases(db)
        metrics.set_gauge("lease.stale", reconciled)
        return {"reconciled": reconciled}
    finally:
        db.close()


@celery_app.task(name="ingestion.sweep_orphan_staging")
def sweep_ingestion_staging() -> dict:
    from ..application.ingestion_maintenance import sweep_orphan_staging

    db = SessionLocal()
    try:
        keys = sweep_orphan_staging(
            db,
            _build_storage(),
            grace_seconds=settings.STAGING_ORPHAN_GRACE_SECONDS,
            dry_run=False,
        )
        metrics.set_gauge("orphan.objects", len(keys))
        return {"deleted": len(keys)}
    finally:
        db.close()
