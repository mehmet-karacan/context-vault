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
                  AND table_name IN (
                    'projects', 'documents', 'document_versions', 'chunks',
                    'principals', 'workspaces', 'workspace_memberships', 'api_keys'
                  )
                """
            )
            assert cursor.fetchone() == (8,)

            cursor.execute(
                """
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'projects'
                  AND column_name = 'workspace_id'
                """
            )
            assert cursor.fetchone() == ("NO",)

            cursor.execute(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'api_keys'
                  AND column_name IN ('created_at', 'expires_at', 'revoked_at', 'last_used_at')
                GROUP BY data_type
                """
            )
            assert cursor.fetchall() == [("timestamp with time zone",)]
