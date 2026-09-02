"""Lease reconciliation, orphan sweeping, and retention-gated storage GC."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from ..domain.clock import Clock, SYSTEM_CLOCK, ensure_utc
from ..models import (
    Chunk,
    Document,
    DocumentArtifact,
    DocumentVersion,
    IngestionAttempt,
    IngestionJob,
    IngestionReceipt,
    MessageCitation,
    StorageGcReceipt,
    StorageObject,
)


@dataclass(frozen=True)
class StorageReconciliationReport:
    missing_keys: tuple[str, ...]
    orphan_keys: tuple[str, ...]


def reconcile_stale_leases(db: Session, *, clock: Clock = SYSTEM_CLOCK) -> int:
    now = clock.now()
    attempts = (
        db.query(IngestionAttempt)
        .filter(
            IngestionAttempt.status.in_(("claimed", "running")),
            IngestionAttempt.lease_expires_at < now,
        )
        .all()
    )
    for attempt in attempts:
        attempt.status = "stale"
        attempt.finished_at = now
        job = db.get(IngestionJob, attempt.job_id)
        if job is None or job.status != "running":
            continue
        job.status = "retrying"
        job.error_code = "stale_lease"
        job.error_message = "worker lease expired before terminal receipt"
        job.lease_owner = None
        job.lease_expires_at = None
        db.add(
            IngestionReceipt(
                id=uuid.uuid4(),
                job_id=job.id,
                attempt_id=attempt.id,
                stage=job.stage or "validating",
                status="retrying",
                error_code="stale_lease",
                metadata_json={},
                created_at=now,
            )
        )
    db.commit()
    return len(attempts)


def storage_reconciliation_report(db: Session, storage) -> StorageReconciliationReport:
    actual = set(storage.list_keys())
    registered = {
        row.storage_key
        for row in db.query(StorageObject)
        .filter(StorageObject.status != "deleted")
        .all()
    }
    return StorageReconciliationReport(
        missing_keys=tuple(sorted(registered - actual)),
        orphan_keys=tuple(sorted(actual - registered)),
    )


def _list_staging_entries(storage) -> Iterable[tuple[str, object]]:
    if hasattr(storage, "list_entries"):
        return storage.list_entries(prefix="staging/")
    return ((key, None) for key in storage.list_keys(prefix="staging/"))


def sweep_orphan_staging(
    db: Session,
    storage,
    *,
    clock: Clock = SYSTEM_CLOCK,
    grace_seconds: int = 3600,
    dry_run: bool = True,
) -> list[str]:
    cutoff = clock.now() - timedelta(seconds=grace_seconds)
    registered = {row.storage_key: row for row in db.query(StorageObject).all()}
    candidates: list[str] = []
    for key, last_modified in _list_staging_entries(storage):
        if last_modified is None or ensure_utc(last_modified) > cutoff:
            continue
        registered_object = registered.get(key)
        reason = "unregistered_staging_object_past_grace"
        if registered_object is not None:
            if registered_object.status != "staged":
                continue
            version = (
                db.get(DocumentVersion, registered_object.version_id)
                if registered_object.version_id is not None
                else None
            )
            terminal_job = (
                db.query(IngestionJob)
                .filter(
                    IngestionJob.version_id == registered_object.version_id,
                    IngestionJob.status.in_(("failed", "cancelled")),
                )
                .first()
                if registered_object.version_id is not None
                else None
            )
            if (
                version is not None
                and version.status != "failed"
                and terminal_job is None
            ):
                continue
            reason = "abandoned_registered_staging_object_past_grace"
        candidates.append(key)
        db.add(
            StorageGcReceipt(
                id=uuid.uuid4(),
                storage_object_id=(
                    registered_object.id if registered_object is not None else None
                ),
                key_hash=hashlib.sha256(key.encode("utf-8")).hexdigest(),
                action="orphan_sweep",
                status="planned" if dry_run else "deleted",
                reason=reason,
                dry_run=dry_run,
                created_at=clock.now(),
            )
        )
        if not dry_run:
            storage.delete(key)
            if registered_object is not None:
                registered_object.status = "deleted"
                registered_object.deleted_at = clock.now()
    db.commit()
    return candidates


def run_storage_gc(
    db: Session,
    storage,
    *,
    clock: Clock = SYSTEM_CLOCK,
    dry_run: bool = True,
) -> list[str]:
    now = clock.now()
    objects = (
        db.query(StorageObject)
        .filter(
            StorageObject.status == "referenced",
            StorageObject.legal_hold.is_(False),
            StorageObject.retention_until.is_not(None),
            StorageObject.retention_until <= now,
        )
        .all()
    )
    selected: list[str] = []
    for stored in objects:
        version = (
            db.get(DocumentVersion, stored.version_id) if stored.version_id else None
        )
        document = (
            db.get(Document, version.document_id) if version is not None else None
        )
        citation_count = (
            db.query(MessageCitation)
            .filter(MessageCitation.version_id == stored.version_id)
            .count()
            if stored.version_id is not None
            else 0
        )
        inactive = (
            document is None
            or document.deleted_at is not None
            or document.active_version_id != stored.version_id
        )
        eligible = (
            inactive
            and (
                document is None
                or document.deleted_at is not None
                or (version is not None and version.status == "superseded")
            )
            and citation_count == 0
        )
        if not eligible:
            continue
        selected.append(stored.storage_key)
        receipt = StorageGcReceipt(
            id=uuid.uuid4(),
            storage_object_id=stored.id,
            key_hash=hashlib.sha256(stored.storage_key.encode("utf-8")).hexdigest(),
            action="retention_gc",
            status="planned",
            reason="inactive_version_retention_elapsed",
            dry_run=dry_run,
            created_at=now,
        )
        db.add(receipt)
        if not dry_run:
            try:
                storage.delete(stored.storage_key)
                stored.status = "deleted"
                stored.deleted_at = now
                receipt.status = "deleted"
            except Exception:
                receipt.status = "failed"
                db.commit()
                raise
    if not dry_run:
        affected_version_ids = {row.version_id for row in objects if row.version_id}
        for version_id in affected_version_ids:
            remaining = (
                db.query(StorageObject)
                .filter(
                    StorageObject.version_id == version_id,
                    StorageObject.status == "referenced",
                )
                .count()
            )
            citations = (
                db.query(MessageCitation)
                .filter(MessageCitation.version_id == version_id)
                .count()
            )
            if remaining == 0 and citations == 0:
                version = db.get(DocumentVersion, version_id)
                if version is not None:
                    version.normalized_artifact_id = None
                    db.flush([version])
                db.query(DocumentArtifact).filter(
                    DocumentArtifact.version_id == version_id
                ).delete(synchronize_session=False)
                db.query(Chunk).filter(Chunk.version_id == version_id).delete(
                    synchronize_session=False
                )
    db.commit()
    return selected
