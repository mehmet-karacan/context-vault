"""OCR JSON artifact persistence (Aşama 8.4).

Stores the raw OCR result as an ``ocr_json`` artifact through a generic
``ObjectStorage`` adapter (e.g. ``MinioObjectStorage``) so the structured OCR
output (full text + per-block bounding boxes + confidence + orientation)
survives for UI citation rendering and debugging.

This is an *optional* integration: nothing in ``ImageParser`` requires it. Any
``ObjectStorage``-conforming object can be injected (including an in-memory
fake from tests), so the helper is DB-free and unit-testable without MinIO.
``ocr_json`` is one of the documented ``document_artifacts.artifact_type``
values (AKTIF_GOREV.md §8.4 / §16).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ...domain import ports
from ..storage import object_keys

# Relative path under artifacts/ where the OCR JSON is stored.
OCR_ARTIFACT_RELATIVE_PATH = "ocr/document.json"
OCR_ARTIFACT_TYPE = "ocr_json"


def ocr_artifact_key(
    project_id: str, document_id: str, version_id: str
) -> str:
    """Builds the object key for a version's OCR JSON artifact."""
    return object_keys.artifact_key(
        project_id, document_id, version_id, OCR_ARTIFACT_RELATIVE_PATH
    )


def ocr_result_to_dict(ocr_result: Any) -> Dict[str, Any]:
    """Serializes a documented ``OcrResult`` (or duck-typed result) to a dict.

    ``blocks`` each carry ``text``, ``confidence``, ``bbox`` and ``page`` so
    the bounding-box citation is storable and re-renderable in the UI.
    """
    blocks: list = []
    for b in getattr(ocr_result, "blocks", []) or []:
        blocks.append(
            {
                "text": getattr(b, "text", "") or "",
                "confidence": getattr(b, "confidence", None),
                "bbox": getattr(b, "bbox", None),
                "page": getattr(b, "page", None),
                "block_type": getattr(b, "block_type", None),
            }
        )
    return {
        "full_text": getattr(ocr_result, "full_text", "") or "",
        "blocks": blocks,
        "confidence": getattr(ocr_result, "confidence", None),
        "orientation": getattr(ocr_result, "orientation", None) or {},
        "engine": getattr(ocr_result, "engine", None),
        "preprocessing_steps": list(
            getattr(ocr_result, "preprocessing_steps", []) or []
        ),
    }


def persist_ocr_json(
    storage: ports.ObjectStorage,
    *,
    project_id: str,
    document_id: str,
    version_id: str,
    ocr_result: Any,
    content_type: str = "application/json",
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Serializes and stores the OCR result, returning the storage key.

    ``extra_metadata`` (e.g. ``source_type`` / ``needs_review``) is merged in
    so the artifact is self-describing for downstream consumers.
    """
    payload = ocr_result_to_dict(ocr_result)
    if extra_metadata:
        payload["metadata"] = dict(extra_metadata)
    key = ocr_artifact_key(project_id, document_id, version_id)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    storage.put(key, data, content_type=content_type)
    return key
