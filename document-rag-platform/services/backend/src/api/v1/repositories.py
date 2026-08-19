"""Repository / archive / directory source ingestion endpoints (Aşama 7.7).

Implements the Aşama 7.7 REST surface (AKTIF_GOREV.md §12.2 / §12.3):

    POST /repositories/ingest
    POST /archives/upload
    POST /directories/scan
    POST /documents/{document_id}/refresh
    GET  /documents/{document_id}/files
    GET  /documents/{document_id}/versions

Every route is additive (new router, no existing route touched) and gated
behind ``FEATURE_REPOSITORY_INGESTION`` (§11 / §16 rollback). Directory scans
are forced through the allowed-roots / canonical-path security check and
**reject absolute paths** (§7.2 / §12.3). The heavy lifting (clone / extract /
incremental re-index) is delegated to the source scanners and the
``ReindexService`` through module-level factory functions so tests can stub
them without a real clone, archive, DB or object store.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...application.reindex_service import ReindexService
from ...config import settings
from ...db import get_db
from ...infrastructure.repositories.archive_source import ArchiveSourceScanner
from ...infrastructure.repositories.git_source import GitRepositorySource
from ...infrastructure.repositories.scan_result import ScanResult
from ...models import Document, DocumentArtifact, DocumentVersion, Project, SourceFile

router = APIRouter(tags=["repositories"])


# ---------------------------------------------------------------------------
# Injectable builders (overridable in tests)
# ---------------------------------------------------------------------------


def _feature_gate() -> None:
    if not settings.FEATURE_REPOSITORY_INGESTION:
        raise HTTPException(
            status_code=403,
            detail="Repository/directory/archive ingestion is disabled (FEATURE_REPOSITORY_INGESTION)",
        )


def _build_repository_source() -> GitRepositorySource:
    return GitRepositorySource()


def _build_archive_scanner() -> ArchiveSourceScanner:
    return ArchiveSourceScanner()


def _build_reindex_service() -> ReindexService:
    return ReindexService()


def _discover_directory(
    target_dir: str,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> ScanResult:
    from ...infrastructure.repositories.discovery_compat import discover_files

    files = discover_files(
        target_dir, include_patterns=include_patterns, exclude_patterns=exclude_patterns
    )
    manifest = hashlib.sha256(
        "\n".join(sorted(f"{f.relative_path}:{f.content_hash}" for f in files)).encode("utf-8")
    ).hexdigest()
    return ScanResult(
        source_type="directory",
        source_revision=manifest,
        root_dir=os.path.realpath(target_dir),
        files=files,
    )


# ---------------------------------------------------------------------------
# Path security (allowed-roots canonicalization; §7.2 / §12.3)
# ---------------------------------------------------------------------------


def _parse_allowed_roots() -> dict:
    """Parses ``CODE_ALLOWED_ROOTS`` into ``alias_lower -> canonical_root``.

    Supports both ``alias=path`` and ``/path`` forms (alias derived from the
    path's basename), matching the default ``/imports,/workspace``.
    """
    roots: dict = {}
    for entry in (settings.CODE_ALLOWED_ROOTS or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            alias, path = entry.split("=", 1)
            roots[alias.strip().lower()] = os.path.realpath(path.strip())
        else:
            roots[os.path.basename(entry.rstrip("/\\")).lower()] = os.path.realpath(entry)
    return roots


def resolve_allowed_scan_path(alias: str, relative_path: str) -> str:
    """Resolves ``alias + relative_path`` to a canonical absolute path (or
    raises an HTTPException). Rejects absolute/escaping paths (§7.2)."""
    if not relative_path or not relative_path.strip():
        raise HTTPException(status_code=400, detail="relative_path is required")
    rp = relative_path.strip()
    if os.path.isabs(rp):
        raise HTTPException(status_code=400, detail="Absolute paths are not allowed for directory scan")
    if re.match(r"^[a-zA-Z]:[\\/]", rp):
        raise HTTPException(status_code=400, detail="Absolute paths are not allowed for directory scan")

    roots = _parse_allowed_roots()
    key = (alias or "").strip().lower()
    if key not in roots:
        raise HTTPException(status_code=403, detail=f"Unknown allowed root alias: {alias!r}")

    root = roots[key]
    rel = rp.replace("\\", "/").lstrip("/")
    target = os.path.realpath(os.path.join(root, rel))

    # Route the under-root decision through the documented security gate
    # (AKTIF §7.2 / §12.3): canonicalize on both sides and refuse anything that
    # escapes every allowed root (incl. via symlink / traversal).
    try:
        from ...infrastructure.repositories.path_security import is_allowed_scan_path
        _is_allowed = is_allowed_scan_path(target, settings.CODE_ALLOWED_ROOTS)
    except Exception:  # concurrent module absent -> local canonical check
        _is_allowed = target == root or target.startswith(root + os.sep)
    if not _is_allowed:
        raise HTTPException(status_code=403, detail="Path escapes the allowed root")

    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail=f"Path does not exist: {target}")
    return target


# ---------------------------------------------------------------------------
# Document helpers
# ---------------------------------------------------------------------------


def _get_or_create_source_document(
    db: Session, project: Project, source_type: str, origin_uri: str, name: str
) -> Document:
    doc = (
        db.query(Document)
        .filter(
            Document.project_id == project.id,
            Document.source_type == source_type,
            Document.origin_uri == origin_uri,
        )
        .first()
    )
    if doc is not None:
        return doc
    now = datetime.utcnow()
    doc = Document(
        id=uuid.uuid4(),
        project_id=project.id,
        name=name,
        size=0,
        status="processing",
        uploaded_at=now,
        source_type=source_type,
        origin_uri=origin_uri,
        created_at=now,
        updated_at=now,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _store_scan_config(db: Session, version: DocumentVersion, metadata: dict) -> None:
    db.add(
        DocumentArtifact(
            id=uuid.uuid4(),
            version_id=version.id,
            artifact_type="scan_config",
            storage_key="inline:scan_config",
            size_bytes=0,
            metadata_json=metadata,
            created_at=datetime.utcnow(),
        )
    )


def _read_scan_config(db: Session, document: Document) -> dict:
    active = db.get(DocumentVersion, document.active_version_id) if document.active_version_id else None
    if active is None:
        return {}
    artifact = (
        db.query(DocumentArtifact)
        .filter(
            DocumentArtifact.version_id == active.id,
            DocumentArtifact.artifact_type == "scan_config",
        )
        .first()
    )
    return dict(artifact.metadata_json or {}) if artifact else {}


def _reindex(db: Session, project: Project, document: Document, scan: ScanResult, scan_config: dict) -> dict:
    service = _build_reindex_service()
    result = service.run(db, document, scan)
    new_version = db.get(DocumentVersion, result["version_id"])
    if new_version is not None:
        _store_scan_config(db, new_version, scan_config)
        db.commit()
    return result


# ---------------------------------------------------------------------------
# Request bodies (§12.2 / §12.3)
# ---------------------------------------------------------------------------


class RepoIngestRequest(BaseModel):
    project_id: str
    repository_url: str
    ref: Optional[str] = None
    credential_ref: Optional[str] = None
    include_patterns: List[str] = []
    exclude_patterns: List[str] = []


class DirectoryScanRequest(BaseModel):
    project_id: str
    allowed_root_alias: str
    relative_path: str
    include_patterns: List[str] = []
    exclude_patterns: List[str] = []


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _serialize_source_file(sf: SourceFile) -> dict:
    return {
        "id": str(sf.id),
        "relative_path": sf.relative_path,
        "language": sf.language,
        "mime_type": sf.mime_type,
        "size_bytes": sf.size_bytes,
        "content_hash": sf.content_hash,
        "is_binary": sf.is_binary,
        "is_generated": sf.is_generated,
        "metadata": sf.metadata_json,
    }


def _serialize_version(v: DocumentVersion) -> dict:
    return {
        "id": str(v.id),
        "version_no": v.version_no,
        "source_revision": v.source_revision,
        "status": v.status,
        "parser_profile": v.parser_profile,
        "chunker_profile": v.chunker_profile,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "activated_at": v.activated_at.isoformat() if v.activated_at else None,
        "is_active": v.document.active_version_id == v.id if v.document else False,
        "files_count": len(v.source_files) if v.source_files else 0,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/repositories/ingest")
def ingest_repository(payload: RepoIngestRequest, db: Session = Depends(get_db)):
    _feature_gate()
    project = db.get(Project, payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    source = _build_repository_source()
    scan = source.scan(
        payload.repository_url,
        ref=payload.ref,
        credential_ref=payload.credential_ref,
        include_patterns=payload.include_patterns,
        exclude_patterns=payload.exclude_patterns,
    )
    repo_name = os.path.basename(payload.repository_url.rstrip("/")).removesuffix(".git") or "repository"
    document = _get_or_create_source_document(
        db, project, "repository", payload.repository_url, repo_name
    )
    result = _reindex(
        db,
        project,
        document,
        scan,
        scan_config={
            "repository_url": payload.repository_url,
            "ref": payload.ref,
            "credential_ref": payload.credential_ref,
            "include_patterns": payload.include_patterns,
            "exclude_patterns": payload.exclude_patterns,
        },
    )
    return {"source_type": "repository", "document_id": str(document.id), **result}


@router.post("/archives/upload")
def upload_archive(file: UploadFile = File(...), project_id: str = File(...), db: Session = Depends(get_db)):
    _feature_gate()
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    data = file.file.read()
    checksum = hashlib.sha256(data).hexdigest()
    scanner = _build_archive_scanner()
    scan = scanner.scan(data, file.filename or "archive.zip")
    document = _get_or_create_source_document(
        db, project, "archive", f"archive:{checksum}", file.filename or "archive.zip"
    )
    from ...infrastructure.storage.minio_storage import MinioObjectStorage  # noqa: F401

    result = _reindex(
        db,
        project,
        document,
        scan,
        scan_config={
            "filename": file.filename,
            "checksum": checksum,
            "include_patterns": [],
            "exclude_patterns": [],
        },
    )
    return {"source_type": "archive", "document_id": str(document.id), **result}


@router.post("/directories/scan")
def scan_directory(payload: DirectoryScanRequest, db: Session = Depends(get_db)):
    _feature_gate()
    project = db.get(Project, payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    target = resolve_allowed_scan_path(payload.allowed_root_alias, payload.relative_path)
    scan = _discover_directory(
        target,
        include_patterns=payload.include_patterns,
        exclude_patterns=payload.exclude_patterns,
    )
    origin = f"{payload.allowed_root_alias}:{payload.relative_path}"
    document = _get_or_create_source_document(
        db, project, "directory", origin, payload.relative_path
    )
    result = _reindex(
        db,
        project,
        document,
        scan,
        scan_config={
            "allowed_root_alias": payload.allowed_root_alias,
            "relative_path": payload.relative_path,
            "include_patterns": payload.include_patterns,
            "exclude_patterns": payload.exclude_patterns,
        },
    )
    return {"source_type": "directory", "document_id": str(document.id), **result}


@router.post("/documents/{document_id}/refresh")
def refresh_document(document_id: str, db: Session = Depends(get_db)):
    _feature_gate()
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.active_version_id is None:
        raise HTTPException(status_code=409, detail="Document has no active version to refresh")

    cfg = _read_scan_config(db, document)
    source_type = document.source_type or "directory"

    if source_type == "repository":
        scan = _build_repository_source().scan(
            document.origin_uri,
            ref=cfg.get("ref"),
            credential_ref=cfg.get("credential_ref"),
            include_patterns=cfg.get("include_patterns"),
            exclude_patterns=cfg.get("exclude_patterns"),
        )
    elif source_type == "archive":
        import tempfile

        artifact = (
            db.query(DocumentArtifact)
            .filter(
                DocumentArtifact.version_id == document.active_version_id,
                DocumentArtifact.artifact_type == "original",
            )
            .first()
        )
        if artifact is None:
            raise HTTPException(status_code=409, detail="Archive original artifact missing")
        from ...infrastructure.storage.minio_storage import MinioObjectStorage

        storage = MinioObjectStorage(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            bucket=settings.MINIO_BUCKET,
        )
        data = storage.get(artifact.storage_key)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            tmp.write(data)
            tmp_name = tmp.name
        scan = _build_archive_scanner().scan(data, cfg.get("filename") or "archive.zip")
        os.unlink(tmp_name)
    else:  # directory
        target = resolve_allowed_scan_path(
            cfg.get("allowed_root_alias", ""), cfg.get("relative_path", "")
        )
        scan = _discover_directory(
            target,
            include_patterns=cfg.get("include_patterns"),
            exclude_patterns=cfg.get("exclude_patterns"),
        )

    project = db.get(Project, document.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    result = _reindex(
        db, project, document, scan, scan_config=dict(cfg)
    )
    return {"document_id": str(document.id), "source_type": source_type, **result}


@router.get("/documents/{document_id}/files")
def list_document_files(
    document_id: str, version_id: Optional[str] = None, db: Session = Depends(get_db)
):
    _feature_gate()
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    target_version = (
        version_id
        or document.active_version_id
    )
    if target_version is None:
        return {"document_id": str(document.id), "version_id": None, "files": []}
    files = (
        db.query(SourceFile)
        .filter(SourceFile.version_id == target_version)
        .order_by(SourceFile.relative_path.asc())
        .all()
    )
    return {
        "document_id": str(document.id),
        "version_id": str(target_version),
        "files": [_serialize_source_file(f) for f in files],
    }


@router.get("/documents/{document_id}/versions")
def list_document_versions(document_id: str, db: Session = Depends(get_db)):
    _feature_gate()
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    versions = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_no.asc())
        .all()
    )
    return {
        "document_id": str(document.id),
        "versions": [_serialize_version(v) for v in versions],
    }
