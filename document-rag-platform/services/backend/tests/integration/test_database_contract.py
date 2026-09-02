from __future__ import annotations

import os

import psycopg2
import pytest

from src.migration_settings import EXPECTED_ALEMBIC_HEAD


@pytest.mark.integration
def test_database_is_at_expected_v3_contract() -> None:
    database_url = os.environ["DATABASE_URL"]
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            assert cursor.fetchone() == (EXPECTED_ALEMBIC_HEAD,)

            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            assert cursor.fetchone() == (True,)

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN ('projects', 'documents', 'document_versions', 'chunks')
                """
            )
            assert cursor.fetchone() == (4,)
