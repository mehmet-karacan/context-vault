"""Incremental re-index service (Aşama 7.6).

``ReindexService`` turns a scanned ``ScanResult`` (repository / archive /
directory) into a brand-new ``DocumentVersion`` for an existing ``Document``,
re-processing **only the files whose ``content_hash`` changed** and copying the
chunks/embeddings of unchanged files from the previous active version.

Contract (AKTIF_GOREV.md §7.6 / §13 / Aşama 2 kabul kriteri):

1. Snapshot the source set keyed by ``source_revision`` (commit SHA / checksum).
2. For each discovered, non-ignored file compute its ``content_hash``; if it is
   unchanged since the previous active version of that document, **skip**
   re-parse/re-embed (copy the previous version's chunks instead).
3. Changed/new files are parsed, chunked and embedded into the new version.
4. Deleted files (present in the old version, absent from the scan) simply never
   appear in the new version.
5. ``documents.active_version_id`` is **not** mutated until the entire new
   version is fully built; only then is it swapped atomically (nothing reads a
   half-built version).

Like ``retrieval_service`` / ``answer_service``, every external dependency
(DB session, object storage, parser, chunker, embedder) is constructor-injected
so the service is deterministic and unit-testable without a real DB, MinIO or
LLM gateway (see ``tests/test_reindex_service.py``).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional

from ..infrastructure.storage import object_keys
from ..models import (
    EMBEDDING_DIMENSION,
    Chunk,
    ChunkEmbedding,
    Document,
    DocumentArtifact,
    DocumentVersion,
    EmbeddingProfile,
    SourceFile,
)
from ..infrastructure.repositories.scan_result import ScanResult, ScannedFile


class ReindexError(RuntimeError):
    """Raised for permanent, validation-level failures during re-index."""


# ---------------------------------------------------------------------------
# Default collaborators (lazily imported so importing this module never pulls
# network/LLM dependencies). Tests inject fakes instead.
# ---------------------------------------------------------------------------


def _default_parse(file_path: str, filename: str) -> str:
    """Default parser: routes code files through the real Aşama 7
    ``CodeParser``, JSON-serializing the produced ``NormalizedSource`` so the
    paired default chunker can re-route it through the registry; every other
    file is read as UTF-8 text. Binary files yield an empty string and
    therefore no chunks."""
    from ..infrastructure.parsers.code_parser import CodeParser

    try:
        parsed = CodeParser().parse(file_path, filename)
    except Exception:
        parsed = None
    if parsed is not None and parsed.source_type == "code" and any(
        u.text.strip() for u in parsed.units
    ):
        return json.dumps(parsed.to_dict())
    with open(file_path, "rb") as fh:
        data = fh.read()
    if b"\x00" in data[:8192]:
        return ""
    return data.decode("utf-8", errors="replace")


def _default_chunk(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """Default chunker: a serialized code ``NormalizedSource`` is routed through
    the real ``ChunkerRegistry`` — which sends PL/SQL (``.pks``/``.pkb``/``.pls``/
    ``.sql``) to ``PlSqlChunker`` and other code to the generic ``CodeChunker``.
    Any other input falls back to a naive size-based split."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict) and payload.get("source_type") == "code":
        from ..domain.normalized_content import NormalizedSource
        from ..infrastructure.chunkers.registry import ChunkerRegistry

        try:
            source = NormalizedSource.from_dict(payload)
            return [c.content for c in ChunkerRegistry().chunk(source) if c.content]
        except Exception:
            pass
    chunks: List[str] = []
    lines = text.splitlines()
    current: List[str] = []
    size = 0
    for line in lines:
        current.append(line)
        size += len(line) + 1
        if size >= chunk_size:
            joined = "\n".join(current).strip()
            if joined:
                chunks.append(joined)
            current = []
            size = 0
    joined = "\n".join(current).strip()
    if joined:
        chunks.append(joined)
    return chunks


def _default_embed(texts: List[str], instruction: str = "") -> List[List[float]]:
    from ..llm import embed_texts  # type: ignore
    return embed_texts(texts, instruction=instruction)


def _get_or_create_active_embedding_profile(db) -> EmbeddingProfile:
    """Returns the single active embedding profile, creating it from settings
    if none exists (mirrors ``workers.ingestion_tasks``)."""
    profile = (
        db.query(EmbeddingProfile)
        .filter(EmbeddingProfile.is_active.is_(True))
        .order_by(EmbeddingProfile.created_at.desc())
        .first()
    )
    if profile is not None:
        return profile
    from ..config import settings

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


class ReindexService:
    """Incremental, atomically-activated version producer (Aşama 7.6)."""

    def __init__(
        self,
        *,
        storage: Optional[object] = None,
        parse_fn: Optional[Callable[[str, str], str]] = None,
        chunk_fn: Optional[Callable[..., List[str]]] = None,
        embed_fn: Optional[Callable[..., List[List[float]]]] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        embed_instruction: str = "",
    ):
        self.storage = storage
        self.parse_fn = parse_fn or _default_parse
        self.chunk_fn = chunk_fn or _default_chunk
        self.embed_fn = embed_fn or _default_embed
        self.chunk_size = chunk_size or 500
        self.chunk_overlap = chunk_overlap if chunk_overlap is not None else 50
        self.embed_instruction = embed_instruction

    # --- internals -----------------------------------------------------------

    def _previous_version(self, db, document: Document) -> Optional[DocumentVersion]:
        if not document.active_version_id:
            return None
        return db.get(DocumentVersion, document.active_version_id)

    def _store_original_artifact(
        self, db, document: Document, version: DocumentVersion, sf: SourceFile, data: bytes
    ) -> None:
        key = object_keys.artifact_key(
            str(document.project_id), str(document.id), str(version.id), sf.relative_path
        )
        if self.storage is not None:
            self.storage.put(key, data, content_type="application/octet-stream")
        db.add(
            DocumentArtifact(
                id=uuid.uuid4(),
                version_id=version.id,
                artifact_type="original",
                storage_key=key,
                checksum=sf.content_hash,
                size_bytes=sf.size_bytes,
                metadata_json={"relative_path": sf.relative_path},
                created_at=datetime.utcnow(),
            )
        )

    def _process_changed_file(
        self,
        db,
        document: Document,
        version: DocumentVersion,
        sf: SourceFile,
        file: ScannedFile,
    ) -> int:
        """Full parse -> chunk -> embed pipeline for a changed/new file.
        Returns the number of chunks produced."""
        with open(file.abs_path, "rb") as fh:
            data = fh.read()
        self._store_original_artifact(db, document, version, sf, data)

        text = self.parse_fn(file.abs_path, file.relative_path)
        if not text:
            return 0
        chunks = self.chunk_fn(text, chunk_size=self.chunk_size, overlap=self.chunk_overlap)
        if not chunks:
            return 0
        embeddings = self.embed_fn(chunks, instruction=self.embed_instruction)
        if len(embeddings) != len(chunks):
            raise ReindexError("Embedding count does not match chunk count")

        profile = _get_or_create_active_embedding_profile(db)
        for idx, (content, embedding) in enumerate(zip(chunks, embeddings)):
            chunk = Chunk(
                id=uuid.uuid4(),
                document_id=document.id,
                version_id=version.id,
                source_file_id=sf.id,
                chunk_index=idx,
                sequence_no=idx,
                chunk_type="code" if (file.language or "").isalpha() else "text",
                content=content,
                embedding=embedding,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                line_start=None,
                line_end=None,
                metadata_json={"source_file": sf.relative_path, "language": file.language},
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
        return len(chunks)

    def _copy_unchanged_file(
        self,
        db,
        document: Document,
        version: DocumentVersion,
        new_sf: SourceFile,
        prev_sf: SourceFile,
        prev_chunks_by_file: Dict[str, List[Chunk]],
    ) -> int:
        """Copies a previous version's chunks (re-linked to the new version and
        the new SourceFile) without re-parsing or re-embedding. Returns count."""
        profile = _get_or_create_active_embedding_profile(db)
        count = 0
        for pc in prev_chunks_by_file.get(str(prev_sf.id), []):
            chunk = Chunk(
                id=uuid.uuid4(),
                document_id=document.id,
                version_id=version.id,
                source_file_id=new_sf.id,
                chunk_index=pc.chunk_index,
                sequence_no=pc.sequence_no,
                chunk_type=pc.chunk_type,
                content=pc.content,
                embedding=pc.embedding,
                content_hash=pc.content_hash,
                line_start=pc.line_start,
                line_end=pc.line_end,
                metadata_json=pc.metadata_json,
                created_at=datetime.utcnow(),
            )
            db.add(chunk)
            db.flush()
            db.add(
                ChunkEmbedding(
                    chunk_id=chunk.id,
                    embedding_profile_id=profile.id,
                    embedding=pc.embedding,
                    created_at=datetime.utcnow(),
                )
            )
            count += 1
        return count

    # --- public --------------------------------------------------------------

    def run(self, db, document: Document, scan: ScanResult) -> dict:
        """Builds a new ``DocumentVersion`` for ``scan`` under ``document`` and
        activates it atomically only once fully ready. Returns a summary dict."""
        now = datetime.utcnow()

        prev_version = self._previous_version(db, document)
        prev_by_path: Dict[str, SourceFile] = {}
        prev_chunks_by_file: Dict[str, List[Chunk]] = {}
        if prev_version is not None:
            prev_by_path = {
                sf.relative_path: sf
                for sf in db.query(SourceFile).filter(SourceFile.version_id == prev_version.id).all()
            }
            for c in db.query(Chunk).filter(Chunk.version_id == prev_version.id).all():
                prev_chunks_by_file.setdefault(str(c.source_file_id), []).append(c)

        existing_versions = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == document.id)
            .all()
        )
        version_no = max((int(v.version_no) for v in existing_versions), default=0) + 1

        from ..config import settings

        new_version = DocumentVersion(
            id=uuid.uuid4(),
            document_id=document.id,
            version_no=version_no,
            source_revision=scan.source_revision,
            status="pending",
            parser_profile=getattr(settings, "CODE_PARSER_PROFILE", None) or "code-default",
            chunker_profile=getattr(settings, "CODE_CHUNKER_PROFILE", None) or "code-default",
            created_at=now,
        )
        db.add(new_version)
        db.flush()

        processed = 0
        copied = 0
        ignored = 0
        deleted = set(prev_by_path.keys()) - {f.relative_path for f in scan.files}

        for file in scan.files:
            if file.is_ignored:
                ignored += 1
                continue

            sf = SourceFile(
                id=uuid.uuid4(),
                version_id=new_version.id,
                relative_path=file.relative_path,
                language=file.language,
                mime_type=file.mime_type,
                size_bytes=file.size_bytes,
                content_hash=file.content_hash,
                is_binary=file.is_binary,
                is_generated=file.is_generated,
                is_ignored=False,
                metadata_json=file.metadata_json or {},
            )
            db.add(sf)
            db.flush()

            prev_sf = prev_by_path.get(file.relative_path)
            if prev_sf is not None and prev_sf.content_hash == file.content_hash:
                copied += self._copy_unchanged_file(
                    db, document, new_version, sf, prev_sf, prev_chunks_by_file
                )
            else:
                processed += self._process_changed_file(db, document, new_version, sf, file)

        # ---- atomic activation: only after the whole version is ready ----
        new_version.status = "ready"
        new_version.activated_at = now
        document.active_version_id = new_version.id
        document.status = "indexed"
        document.updated_at = now
        db.commit()

        return {
            "document_id": str(document.id),
            "version_id": str(new_version.id),
            "version_no": version_no,
            "source_revision": scan.source_revision,
            "files_count": len(scan.files),
            "files_processed": processed,
            "files_copied": copied,
            "ignored": ignored,
            "deleted_files": sorted(deleted),
            "chunks": processed + copied,
        }
