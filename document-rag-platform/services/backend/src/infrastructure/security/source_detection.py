"""Multi-signal source detection used before any ingestion side effect."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Optional

from ...domain.ingestion import SourceDescriptor
from .file_validation import UploadValidationResult, validate_upload


class SourcePolicyRejected(ValueError):
    def __init__(self, message: str, *, result: UploadValidationResult):
        super().__init__(message)
        self.result = result


def _detected_mime(data: bytes, filename: str, result: UploadValidationResult) -> str:
    category = result.detected_magic or result.detected_mime
    if category == "pdf":
        return "application/pdf"
    if category == "image":
        guessed = mimetypes.guess_type(filename)[0]
        return guessed if guessed and guessed.startswith("image/") else "image/unknown"
    if category == "office":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if b"\x00" not in data[:8192]:
        try:
            data[:8192].decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            pass
    return "application/octet-stream"


def describe_upload(
    data: bytes,
    *,
    filename: str,
    declared_mime: Optional[str],
    classification: str,
    source_type: str = "document",
    origin: Optional[str] = None,
    revision: Optional[str] = None,
    config=None,
) -> SourceDescriptor:
    result = validate_upload(data, filename, declared_mime, config=config)
    if not result.ok:
        raise SourcePolicyRejected(result.error or "source rejected", result=result)
    detected = _detected_mime(data, filename, result)
    declared = declared_mime.split(";", 1)[0].strip().lower() if declared_mime else None
    events: tuple[str, ...] = ()
    if declared and declared != detected:
        # Category-compatible labels (for example text/markdown detected as
        # text/plain) are still recorded for audit, even though safe policy
        # permits the source.
        events = ("declared_detected_mime_difference",)
    return SourceDescriptor(
        source_type=source_type,
        origin=origin or Path(filename).name,
        revision=revision,
        content_length=len(data),
        content_hash=hashlib.sha256(data).hexdigest(),
        detected_mime=detected,
        declared_mime=declared,
        data_classification_hint=classification,
        policy_events=events,
    )
