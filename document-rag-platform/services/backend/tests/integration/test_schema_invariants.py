from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.domain.clock import FixedClock
from src.domain.version_activation import (
    ConcurrentActivationError,
    activate_document_version,
)

LEGACY_WORKSPACE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
psycopg2.extras.register_uuid()


def _expect_rejected(cursor, sql: str, params: tuple) -> None:
    cursor.execute("SAVEPOINT expected_rejection")
    try:
        cursor.execute(sql, params)
    except psycopg2.Error:
        cursor.execute("ROLLBACK TO SAVEPOINT expected_rejection")
        cursor.execute("RELEASE SAVEPOINT expected_rejection")
        return
    cursor.execute("ROLLBACK TO SAVEPOINT expected_rejection")
    cursor.execute("RELEASE SAVEPOINT expected_rejection")
    pytest.fail("database accepted an invariant violation")


@pytest.mark.integration
def test_database_rejects_cross_version_state_and_profile_violations() -> None:
    project_id, doc_a, doc_b, version_a, version_b, job_id = (
        uuid.uuid4() for _ in range(6)
    )
    now = datetime.now(timezone.utc)
    with psycopg2.connect(os.environ["DATABASE_URL"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (id, workspace_id, name, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (project_id, LEGACY_WORKSPACE_ID, f"invariant-{project_id}", now, now),
            )
            for document_id in (doc_a, doc_b):
                cursor.execute(
                    """
                    INSERT INTO documents
                      (id, project_id, name, size, status, uploaded_at, created_at, updated_at)
                    VALUES (%s,%s,%s,1,'uploaded',%s,%s,%s)
                    """,
                    (document_id, project_id, f"{document_id}.txt", now, now, now),
                )
            cursor.execute(
                """
                INSERT INTO document_versions
                  (id, document_id, version_no, status, created_at)
                VALUES (%s,%s,1,'ready',%s), (%s,%s,1,'pending',%s)
                """,
                (version_a, doc_a, now, version_b, doc_b, now),
            )

            _expect_rejected(
                cursor,
                """
                INSERT INTO chunks
                  (id, document_id, version_id, chunk_index, content, created_at)
                VALUES (%s,%s,%s,0,'cross scope',%s)
                """,
                (uuid.uuid4(), doc_a, version_b, now),
            )
            _expect_rejected(
                cursor,
                "UPDATE documents SET active_version_id=%s WHERE id=%s",
                (version_b, doc_b),
            )
            _expect_rejected(
                cursor,
                "UPDATE documents SET status='error' WHERE id=%s",
                (doc_a,),
            )
            _expect_rejected(
                cursor,
                "UPDATE document_versions SET status='failed' WHERE id=%s",
                (version_a,),
            )
            _expect_rejected(
                cursor,
                """
                INSERT INTO ingestion_jobs
                  (id, version_id, status, progress, attempt, created_at)
                VALUES (%s,%s,'queued',101,0,%s)
                """,
                (job_id, version_a, now),
            )
            cursor.execute(
                """
                INSERT INTO ingestion_jobs
                  (id, version_id, status, progress, attempt, created_at, finished_at)
                VALUES (%s,%s,'completed',100,1,%s,%s)
                """,
                (job_id, version_a, now, now),
            )
            _expect_rejected(
                cursor,
                "UPDATE ingestion_jobs SET status='running' WHERE id=%s",
                (job_id,),
            )
            _expect_rejected(
                cursor,
                """
                INSERT INTO ingestion_events (id,job_id,stage,status,created_at)
                VALUES (%s,%s,'unknown-stage','running',%s)
                """,
                (uuid.uuid4(), job_id, now),
            )

            cursor.execute("SELECT id FROM embedding_profiles WHERE is_active LIMIT 1")
            profile_id = cursor.fetchone()[0]
            _expect_rejected(
                cursor,
                "UPDATE embedding_profiles SET model=model || '-mutated' WHERE id=%s",
                (profile_id,),
            )
            _expect_rejected(
                cursor,
                """
                INSERT INTO embedding_profiles
                  (id,provider,model,dimension,distance_metric,profile_version,
                   config_hash,is_active,created_at)
                VALUES (%s,'test','test',1024,'cosine',1,%s,true,%s)
                """,
                (uuid.uuid4(), "a" * 64, now),
            )
        connection.rollback()


@pytest.mark.integration
def test_concurrent_compare_and_swap_allows_one_active_version() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    project_id, document_id, version_a, version_b = (uuid.uuid4() for _ in range(4))
    now = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (id,workspace_id,name,created_at,updated_at)
                VALUES (:id,:workspace,:name,:now,:now);
                INSERT INTO documents
                  (id,project_id,name,size,status,uploaded_at,created_at,updated_at)
                VALUES (:doc,:id,'concurrent.txt',1,'uploaded',:now,:now,:now);
                INSERT INTO document_versions
                  (id,document_id,version_no,status,created_at)
                VALUES (:a,:doc,1,'ready',:now),(:b,:doc,2,'ready',:now)
                """
            ),
            {
                "id": project_id,
                "workspace": LEGACY_WORKSPACE_ID,
                "name": f"concurrent-{project_id}",
                "doc": document_id,
                "a": version_a,
                "b": version_b,
                "now": now,
            },
        )

    first = Session(engine)
    started = threading.Event()
    outcomes: list[str] = []

    activate_document_version(
        first,
        document_id=document_id,
        version_id=version_a,
        expected_current_version_id=None,
        clock=FixedClock(now),
    )

    def competing_activation() -> None:
        with Session(engine) as second:
            started.set()
            try:
                activate_document_version(
                    second,
                    document_id=document_id,
                    version_id=version_b,
                    expected_current_version_id=None,
                    clock=FixedClock(now),
                )
                second.commit()
                outcomes.append("accepted")
            except ConcurrentActivationError:
                second.rollback()
                outcomes.append("rejected")

    competitor = threading.Thread(target=competing_activation)
    competitor.start()
    assert started.wait(timeout=2)
    first.commit()
    competitor.join(timeout=5)
    first.close()

    try:
        assert outcomes == ["rejected"]
        with engine.connect() as connection:
            active = connection.execute(
                text("SELECT active_version_id FROM documents WHERE id=:id"),
                {"id": document_id},
            ).scalar_one()
        assert active == version_a
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE documents SET active_version_id=NULL WHERE id=:id"),
                {"id": document_id},
            )
            connection.execute(
                text("DELETE FROM projects WHERE id=:id"), {"id": project_id}
            )
        engine.dispose()
