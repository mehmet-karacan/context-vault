from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.application.ingestion_orchestrator import (
    AcceptSourceCommand,
    IngestionOrchestrator,
)
from src.config import settings
from src.domain.clock import FixedClock
from src.domain.ingestion import SourceDescriptor
from src.infrastructure.chunkers.base import ChunkCandidate
from src.infrastructure.storage.minio_storage import MinioObjectStorage
from src.models import Principal, Project, Workspace


@pytest.mark.integration
def test_real_postgres_and_minio_encrypted_ingestion_roundtrip() -> None:
    storage = MinioObjectStorage(
        endpoint=settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        bucket=settings.MINIO_BUCKET,
        encryption_key=settings.OBJECT_STORAGE_ENCRYPTION_KEY,
        allow_legacy_plaintext_reads=settings.OBJECT_STORAGE_ALLOW_LEGACY_PLAINTEXT_READS,
    )
    try:
        before = set(storage.list_keys())
    except Exception as exc:  # pragma: no cover - environment admission
        pytest.skip(f"MinIO integration environment unavailable: {exc}")

    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        connection = engine.connect()
    except Exception as exc:  # pragma: no cover - environment admission
        pytest.skip(f"PostgreSQL integration environment unavailable: {exc}")
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    now = datetime(2026, 9, 2, 23, 0, tzinfo=timezone.utc)
    principal_id, workspace_id, project_id = (uuid.uuid4() for _ in range(3))

    try:
        db.add_all(
            [
                Principal(id=principal_id, subject=f"a6-minio:{principal_id}"),
                Workspace(id=workspace_id, name=f"A6 MinIO {workspace_id}"),
            ]
        )
        db.flush()
        project = Project(
            id=project_id, workspace_id=workspace_id, name=f"A6 MinIO {project_id}"
        )
        db.add(project)
        db.commit()

        content = b"real postgres and encrypted minio integration payload"
        import hashlib

        descriptor = SourceDescriptor(
            source_type="document",
            origin="minio-e2e.txt",
            revision=None,
            content_length=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            detected_mime="text/plain",
            declared_mime="text/plain",
            data_classification_hint="internal",
        )
        accepted = IngestionOrchestrator(
            db, storage, clock=FixedClock(now)
        ).accept_source(
            AcceptSourceCommand(
                project=project,
                filename="minio-e2e.txt",
                content=content,
                descriptor=descriptor,
                idempotency_key=f"minio-e2e:{uuid.uuid4()}",
                actor_principal_id=principal_id,
                workspace_id=workspace_id,
            )
        )
        result = IngestionOrchestrator(db, storage, clock=FixedClock(now)).process_job(
            accepted.job_id,
            extract_text_fn=lambda _path, _name: "real storage normalized text",
            chunk_text_fn=lambda _source, **_kwargs: [
                ChunkCandidate(
                    chunk_id=str(uuid.uuid4()),
                    source_id="minio-e2e",
                    chunk_type="document",
                    content="real storage chunk",
                    embedding_text="real storage chunk",
                )
            ],
            embed_texts_fn=lambda texts, instruction="": [[0.02] * 1024 for _ in texts],
            embedding_is_remote=False,
            worker_id="minio-e2e-worker",
        )
        assert result["status"] == "completed"
        created = set(storage.list_keys()) - before
        assert len(created) == 3
        assert all(storage.is_encrypted(key) for key in created)
        assert not any(key.startswith("staging/") for key in created)
    finally:
        try:
            for key in set(storage.list_keys()) - before:
                storage.delete(key)
        finally:
            db.close()
            outer.rollback()
            connection.close()
            engine.dispose()
