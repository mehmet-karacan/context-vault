"""Deterministic multi-file source bundle for the canonical ingestion path."""

from __future__ import annotations

import hashlib
import json
import uuid

from ..domain.normalized_content import (
    ContentUnit,
    NormalizedSource,
    SourceLocator,
    UnitType,
)
from ..infrastructure.repositories.scan_result import ScanResult


BUNDLE_MIME = "application/vnd.context-vault.source-bundle+json"
BUNDLE_FORMAT = "context-vault-source-bundle-v1"


def build_scan_bundle(scan: ScanResult) -> bytes:
    """Serialize accepted text files without paths escaping the scanner root."""
    files: list[dict] = []
    for source in sorted(scan.files, key=lambda item: item.relative_path):
        if source.is_ignored or source.is_binary or source.is_generated:
            continue
        with open(source.abs_path, "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8", errors="replace")
        files.append(
            {
                "relative_path": source.relative_path,
                "size_bytes": source.size_bytes or len(raw),
                "content_hash": source.content_hash or hashlib.sha256(raw).hexdigest(),
                "language": source.language,
                "mime_type": source.mime_type,
                "is_generated": source.is_generated,
                "metadata": source.metadata_json or {},
                "content": text,
            }
        )
    payload = {
        "format": BUNDLE_FORMAT,
        "source_type": scan.source_type,
        "source_revision": scan.source_revision,
        "files": files,
        "warnings": list(scan.warnings),
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def parse_scan_bundle(data: bytes) -> NormalizedSource:
    """Parse a validated bundle into typed units with per-file locators."""
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("source bundle is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("format") != BUNDLE_FORMAT:
        raise ValueError("unsupported source bundle format")
    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError("source bundle files must be a list")

    units: list[ContentUnit] = []
    manifest: list[dict] = []
    order = 0
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("source bundle file entry must be an object")
        path = item.get("relative_path")
        content = item.get("content")
        if not isinstance(path, str) or not path or not isinstance(content, str):
            raise ValueError("source bundle file entry is incomplete")
        manifest.append({key: value for key, value in item.items() if key != "content"})
        units.append(
            ContentUnit(
                unit_id=str(uuid.uuid4()),
                unit_type=UnitType.FILE_HEADER,
                text=path,
                markdown=f"# File: {path}",
                order=order,
                locator=SourceLocator(file_path=path, line_start=1, line_end=1),
                metadata={"source_file": path},
            )
        )
        order += 1
        if content.strip():
            units.append(
                ContentUnit(
                    unit_id=str(uuid.uuid4()),
                    unit_type=(
                        UnitType.CODE if item.get("language") else UnitType.PARAGRAPH
                    ),
                    text=content,
                    markdown=content,
                    order=order,
                    locator=SourceLocator(
                        file_path=path,
                        line_start=1,
                        line_end=max(1, len(content.splitlines())),
                    ),
                    metadata={
                        "source_file": path,
                        "language": item.get("language"),
                    },
                )
            )
            order += 1
    return NormalizedSource(
        source_id=str(uuid.uuid4()),
        source_type=str(payload.get("source_type") or "directory"),
        metadata={
            "bundle_format": BUNDLE_FORMAT,
            "source_revision": payload.get("source_revision"),
            "source_files": manifest,
            "warnings": payload.get("warnings") or [],
        },
        units=units,
    )
