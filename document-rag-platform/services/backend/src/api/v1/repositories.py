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
incremental re-index) is delegated to the source scanners and the canonical
``IngestionOrchestrator`` through module-level factory functions so tests can
stub them without a real clone, archive, DB or object store.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...application.ingestion_bundle import BUNDLE_MIME, build_scan_bundle
from ...application.ingestion_orchestrator import (
    AcceptSourceCommand,
    IngestionOrchestrator,
)
from ...config import settings
from ...db import get_db
from ...infrastructure.repositories.archive_source import ArchiveSourceScanner
from ...infrastructure.repositories.git_source import (
    GitRepositorySource,
    RepositoryUrlRejected,
)
from ...infrastructure.repositories.scan_result import ScanResult
from ...models import Document, DocumentArtifact, DocumentVersion, Project, SourceFile
from src.domain.identity import PrincipalContext
from src.domain.ingestion import SourceDescriptor
from src.infrastructure.rate_limiter import rate_limiter
from src.infrastructure.security.auth import (
    get_principal_context,
    require_project_access,
)

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


def _build_ingestion_orchestrator(db: Session) -> IngestionOrchestrator:
    from ...infrastructure.storage.minio_storage import MinioObjectStorage

    storage = MinioObjectStorage(
        endpoint=settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        bucket=settings.MINIO_BUCKET,
        encryption_key=settings.OBJECT_STORAGE_ENCRYPTION_KEY,
        allow_legacy_plaintext_reads=settings.OBJECT_STORAGE_ALLOW_LEGACY_PLAINTEXT_READS,
    )
    return IngestionOrchestrator(db, storage)


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
        "\n".join(sorted(f"{f.relative_path}:{f.content_hash}" for f in files)).encode(
            "utf-8"
        )
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
            roots[os.path.basename(entry.rstrip("/\\")).lower()] = os.path.realpath(
                entry
            )
    return roots


def resolve_allowed_scan_path(alias: str, relative_path: str) -> str:
    """Resolves ``alias + relative_path`` to a canonical absolute path (or
    raises an HTTPException). Rejects absolute/escaping paths (§7.2)."""
    if not relative_path or not relative_path.strip():
        raise HTTPException(status_code=400, detail="relative_path is required")
    rp = relative_path.strip()
    if os.path.isabs(rp):
        raise HTTPException(
            status_code=400, detail="Absolute paths are not allowed for directory scan"
        )
    if re.match(r"^[a-zA-Z]:[\\/]", rp):
        raise HTTPException(
            status_code=400, detail="Absolute paths are not allowed for directory scan"
        )

    roots = _parse_allowed_roots()
    key = (alias or "").strip().lower()
    if key not in roots:
        raise HTTPException(
            status_code=403, detail=f"Unknown allowed root alias: {alias!r}"
        )

    root = os.path.realpath(roots[key])
    rel = rp.replace("\\", "/").lstrip("/")
    target = os.path.realpath(os.path.join(root, rel))

    # Bind the request to the selected alias, not merely to any configured root.
    # Keeping normalization and the boundary comparison adjacent also makes the
    # path-injection barrier explicit to static analysis.
    try:
        common_root = os.path.commonpath((root, target))
    except ValueError:
        common_root = ""
    if common_root != root:
        raise HTTPException(status_code=403, detail="Path escapes the allowed root")

    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Path does not exist")
    return target


# ---------------------------------------------------------------------------
# Document helpers
# ---------------------------------------------------------------------------


def _find_source_document(
    db: Session, project: Project, source_type: str, origin_uri: str
) -> Optional[Document]:
    return (
        db.query(Document)
        .filter(
            Document.project_id == project.id,
            Document.source_type == source_type,
            Document.origin_uri == origin_uri,
            Document.deleted_at.is_(None),
        )
        .first()
    )


def _read_scan_config(db: Session, document: Document) -> dict:
    active = (
        db.get(DocumentVersion, document.active_version_id)
        if document.active_version_id
        else None
    )
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


def _reindex(
    db: Session,
    project: Project,
    document: Optional[Document],
    scan: ScanResult,
    scan_config: dict,
    principal: PrincipalContext,
    *,
    filename: str,
    origin: str,
    classification: str,
) -> dict:
    try:
        bundle = build_scan_bundle(scan)
    finally:
        if scan.cleanup_root:
            shutil.rmtree(scan.cleanup_root, ignore_errors=True)
    checksum = hashlib.sha256(bundle).hexdigest()
    descriptor = SourceDescriptor(
        source_type=scan.source_type,
        origin=origin,
        revision=scan.source_revision,
        content_length=len(bundle),
        content_hash=checksum,
        detected_mime=BUNDLE_MIME,
        declared_mime=BUNDLE_MIME,
        data_classification_hint=classification,
    )
    accepted = _build_ingestion_orchestrator(db).accept_source(
        AcceptSourceCommand(
            project=project,
            filename=filename,
            content=bundle,
            descriptor=descriptor,
            idempotency_key=(
                f"scan:{project.id}:{scan.source_type}:{origin}:"
                f"{scan.source_revision}:{checksum}"
            ),
            actor_principal_id=principal.principal_id,
            workspace_id=principal.workspace_id,
            existing_document=document,
            scan_config=scan_config,
        )
    )
    version = db.get(DocumentVersion, accepted.version_id)
    return {
        "document_id": str(accepted.document_id),
        "version_id": str(accepted.version_id),
        "version_no": version.version_no if version is not None else None,
        "job_id": str(accepted.job_id),
        "status": accepted.status,
        "replayed": accepted.replayed,
        "quarantine_reason": accepted.quarantine_reason,
        "source_revision": scan.source_revision,
        "files_count": len(scan.files),
    }


# ---------------------------------------------------------------------------
# Request bodies (§12.2 / §12.3)
# ---------------------------------------------------------------------------


class RepoIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    repository_url: str
    ref: Optional[str] = None
    credential_ref: Optional[str] = None
    include_patterns: List[str] = Field(default_factory=list)
    exclude_patterns: List[str] = Field(default_factory=list)
    data_classification: Literal["public", "internal", "confidential", "restricted"] = (
        "internal"
    )


class DirectoryScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    allowed_root_alias: str
    relative_path: str
    include_patterns: List[str] = Field(default_factory=list)
    exclude_patterns: List[str] = Field(default_factory=list)
    data_classification: Literal["public", "internal", "confidential", "restricted"] = (
        "internal"
    )


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
def ingest_repository(
    payload: RepoIngestRequest,
    _: None = Depends(rate_limiter),
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    _feature_gate()
    project = require_project_access(db, principal, payload.project_id)

    source = _build_repository_source()
    try:
        scan = source.scan(
            payload.repository_url,
            ref=payload.ref,
            credential_ref=payload.credential_ref,
            include_patterns=payload.include_patterns,
            exclude_patterns=payload.exclude_patterns,
        )
    except RepositoryUrlRejected as exc:
        raise HTTPException(status_code=400, detail="Repository URL rejected") from exc
    repo_name = (
        os.path.basename(payload.repository_url.rstrip("/")).removesuffix(".git")
        or "repository"
    )
    document = _find_source_document(db, project, "repository", payload.repository_url)
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
        principal=principal,
        filename=repo_name,
        origin=payload.repository_url,
        classification=payload.data_classification,
    )
    return {"source_type": "repository", **result}


@router.post("/archives/upload")
def upload_archive(
    file: UploadFile = File(...),
    project_id: UUID = File(...),
    data_classification: Literal[
        "public", "internal", "confidential", "restricted"
    ] = File("internal"),
    _: None = Depends(rate_limiter),
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    _feature_gate()
    project = require_project_access(db, principal, project_id)

    data = file.file.read()
    checksum = hashlib.sha256(data).hexdigest()
    scanner = _build_archive_scanner()
    scan = scanner.scan(data, file.filename or "archive.zip")
    origin = f"archive:{checksum}"
    document = _find_source_document(db, project, "archive", origin)

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
        principal=principal,
        filename=file.filename or "archive.zip",
        origin=origin,
        classification=data_classification,
    )
    return {"source_type": "archive", **result}


@router.post("/directories/scan")
def scan_directory(
    payload: DirectoryScanRequest,
    _: None = Depends(rate_limiter),
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    _feature_gate()
    project = require_project_access(db, principal, payload.project_id)

    target = resolve_allowed_scan_path(
        payload.allowed_root_alias, payload.relative_path
    )
    scan = _discover_directory(
        target,
        include_patterns=payload.include_patterns,
        exclude_patterns=payload.exclude_patterns,
    )
    origin = f"{payload.allowed_root_alias}:{payload.relative_path}"
    document = _find_source_document(db, project, "directory", origin)
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
        principal=principal,
        filename=payload.relative_path,
        origin=origin,
        classification=payload.data_classification,
    )
    return {"source_type": "directory", **result}


def _scoped_document(
    db: Session, principal: PrincipalContext, project_id: UUID, document_id: UUID
) -> Document:
    require_project_access(db, principal, project_id)
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.project_id == project_id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.post("/documents/{document_id}/refresh")
def refresh_document(
    document_id: UUID,
    project_id: UUID,
    _: None = Depends(rate_limiter),
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    _feature_gate()
    document = _scoped_document(db, principal, project_id, document_id)
    if document.active_version_id is None:
        raise HTTPException(
            status_code=409, detail="Document has no active version to refresh"
        )

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
            raise HTTPException(
                status_code=409, detail="Archive original artifact missing"
            )
        from ...infrastructure.storage.minio_storage import MinioObjectStorage

        storage = MinioObjectStorage(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            bucket=settings.MINIO_BUCKET,
            encryption_key=settings.OBJECT_STORAGE_ENCRYPTION_KEY,
            allow_legacy_plaintext_reads=settings.OBJECT_STORAGE_ALLOW_LEGACY_PLAINTEXT_READS,
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

    project = require_project_access(db, principal, project_id)
    result = _reindex(
        db,
        project,
        document,
        scan,
        scan_config=dict(cfg),
        principal=principal,
        filename=document.name,
        origin=document.origin_uri or document.name,
        classification=document.data_classification,
    )
    return {"source_type": source_type, **result}


@router.get("/documents/{document_id}/files")
def list_document_files(
    document_id: UUID,
    project_id: UUID,
    version_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    _feature_gate()
    document = _scoped_document(db, principal, project_id, document_id)
    target_version = version_id or document.active_version_id
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
def list_document_versions(
    document_id: UUID,
    project_id: UUID,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    _feature_gate()
    document = _scoped_document(db, principal, project_id, document_id)
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
