"""Strict A9 offline infrastructure smoke; missing dependencies are failures."""

from __future__ import annotations

import os
import uuid

import pytest
import redis
from sqlalchemy import create_engine, text

from src.infrastructure.storage.minio_storage import MinioObjectStorage
from src.migration_settings import EXPECTED_ALEMBIC_HEAD


@pytest.mark.integration
@pytest.mark.evals
def test_fresh_postgres_redis_minio_are_real_and_writable():
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == EXPECTED_ALEMBIC_HEAD
        )
        assert (
            connection.execute(
                text("SELECT extname FROM pg_extension WHERE extname='vector'")
            ).scalar_one()
            == "vector"
        )
    engine.dispose()

    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    key = f"cv3:a9:{uuid.uuid4()}"
    assert client.ping()
    assert client.set(key, "ok", ex=30)
    assert client.get(key) == "ok"
    client.delete(key)

    storage = MinioObjectStorage(
        endpoint=os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        bucket=os.environ["MINIO_BUCKET"],
        encryption_key=os.environ["OBJECT_STORAGE_ENCRYPTION_KEY"],
    )
    object_key = f"offline-e2e/{uuid.uuid4()}"
    storage.put(object_key, b"offline-e2e", "application/octet-stream")
    assert storage.get(object_key) == b"offline-e2e"
    assert storage.is_encrypted(object_key)
    storage.delete(object_key)
