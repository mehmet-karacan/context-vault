"""Typed ingestion state vocabulary and guarded transitions."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    INDEXED = "indexed"
    ERROR = "error"
    DELETED = "deleted"


class VersionStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(StrEnum):
    VALIDATING = "validating"
    STORING = "storing"
    PARSING = "parsing"
    OCR = "ocr"
    NORMALIZING = "normalizing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    ACTIVATING = "activating"


_JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.RUNNING: {
        JobStatus.RETRYING,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.RETRYING: {JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}


class JobState(Protocol):
    status: str


def transition_job(job: JobState, target: JobStatus | str) -> None:
    current = JobStatus(job.status)
    target_status = JobStatus(target)
    if target_status == current:
        return
    if target_status not in _JOB_TRANSITIONS[current]:
        raise ValueError(
            f"invalid ingestion job transition: {current} -> {target_status}"
        )
    job.status = target_status.value
