"""backfill document_versions, embedding_profiles and chunk_embeddings

Revision ID: b2f1c0a10003
Revises: b2f1c0a10002
Create Date: 2026-08-19

Data-only migration (no schema DDL). For every pre-existing ``documents``
row it creates a ``version_no = 1`` "completed" ``document_versions`` row
(``parser_profile`` / ``chunker_profile`` = ``legacy-v0``, since these rows
predate the parser/chunker versioning introduced in this phase) and points
``documents.active_version_id`` at it. It registers the embedding profile
that produced every existing ``chunks.embedding`` value, and copies each
chunk's existing embedding into ``chunk_embeddings`` under that profile.
``chunks.version_id`` is set to match its document's new version.

The embedding profile values (provider/model/dimension/distance_metric and
the query/passage prefixes) mirror the current production defaults as of
this migration: ``src/config.py``'s ``EMBEDDING_MODEL`` default
(``openai/BAAI/bge-m3``), ``src/models.py``'s ``EMBEDDING_DIMENSION`` (1024),
the cosine HNSW index src/db.py builds, and ``src/llm.py``'s
``PASSAGE_INSTRUCTION`` / ``QUERY_INSTRUCTION`` constants. If a given
deployment's gateway config genuinely differs from these, edit the literal
values below (or the resulting embedding_profiles row) before running
``alembic upgrade head`` -- do not change EMBEDDING_MODEL/prefixes in the
running app without also updating what this backfill records as the profile
that already-embedded chunks belong to.

Every statement below is idempotent (guarded with ``WHERE NOT EXISTS`` /
``IS NULL`` / ``ON CONFLICT DO NOTHING``), so re-running ``alembic upgrade
head`` after a partial failure is safe and will not create duplicates.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2f1c0a10003"
down_revision: Union[str, None] = "b2f1c0a10002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mirrors src/config.py Settings.EMBEDDING_MODEL default and
# src/models.py EMBEDDING_DIMENSION -- see module docstring.
_PROVIDER = "openai_compatible"
_MODEL = "openai/BAAI/bge-m3"
_DIMENSION = 1024
_DISTANCE_METRIC = "cosine"
# Mirrors src/llm.py's PASSAGE_INSTRUCTION / QUERY_INSTRUCTION constants.
_PASSAGE_PREFIX = "Bu metni bir belge arama sisteminde bulunmak üzere temsil et: "
_QUERY_PREFIX = "Bu soruyu ilgili belge parçalarını bulmak için temsil et: "
_PROFILE_VERSION = 1

# Tags used to identify exactly the rows this migration creates, so
# downgrade() can remove them without guessing.
_LEGACY_PARSER_PROFILE = "legacy-v0"
_LEGACY_CHUNKER_PROFILE = "legacy-v0"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Register the embedding profile that already-existing chunk
    #    embeddings were produced with, if not already present.
    bind.execute(
        sa.text(
            """
            INSERT INTO embedding_profiles
                (id, provider, model, dimension, distance_metric,
                 query_prefix, passage_prefix, profile_version, config_hash,
                 is_active, created_at)
            SELECT
                gen_random_uuid(), :provider, :model, :dimension, :distance_metric,
                :query_prefix, :passage_prefix, :profile_version, NULL,
                TRUE, now()
            WHERE NOT EXISTS (
                SELECT 1 FROM embedding_profiles
                WHERE provider = :provider
                  AND model = :model
                  AND profile_version = :profile_version
            )
            """
        ),
        {
            "provider": _PROVIDER,
            "model": _MODEL,
            "dimension": _DIMENSION,
            "distance_metric": _DISTANCE_METRIC,
            "query_prefix": _QUERY_PREFIX,
            "passage_prefix": _PASSAGE_PREFIX,
            "profile_version": _PROFILE_VERSION,
        },
    )

    profile_id = bind.execute(
        sa.text(
            """
            SELECT id FROM embedding_profiles
            WHERE provider = :provider AND model = :model AND profile_version = :profile_version
            """
        ),
        {"provider": _PROVIDER, "model": _MODEL, "profile_version": _PROFILE_VERSION},
    ).scalar_one()

    # 2. One version_no=1 "completed" document_versions row per existing
    #    document that doesn't already have one (idempotent on document_id).
    bind.execute(
        sa.text(
            """
            INSERT INTO document_versions
                (id, document_id, version_no, source_revision, status,
                 parser_profile, chunker_profile, storage_key,
                 normalized_artifact_id, created_at, activated_at, error_message)
            SELECT
                gen_random_uuid(), d.id, 1, NULL, 'completed',
                :parser_profile, :chunker_profile, NULL, NULL, now(), now(), NULL
            FROM documents d
            WHERE NOT EXISTS (
                SELECT 1 FROM document_versions dv WHERE dv.document_id = d.id
            )
            """
        ),
        {"parser_profile": _LEGACY_PARSER_PROFILE, "chunker_profile": _LEGACY_CHUNKER_PROFILE},
    )

    # 3. Point documents.active_version_id at the version just created.
    bind.execute(
        sa.text(
            """
            UPDATE documents d
            SET active_version_id = dv.id
            FROM document_versions dv
            WHERE dv.document_id = d.id
              AND dv.version_no = 1
              AND dv.parser_profile = :parser_profile
              AND d.active_version_id IS NULL
            """
        ),
        {"parser_profile": _LEGACY_PARSER_PROFILE},
    )

    # 4. Stamp every chunk with its document's new version_id.
    bind.execute(
        sa.text(
            """
            UPDATE chunks c
            SET version_id = dv.id
            FROM document_versions dv
            WHERE dv.document_id = c.document_id
              AND dv.version_no = 1
              AND dv.parser_profile = :parser_profile
              AND c.version_id IS NULL
            """
        ),
        {"parser_profile": _LEGACY_PARSER_PROFILE},
    )

    # 5. Copy each chunk's existing embedding into chunk_embeddings under
    #    the profile registered in step 1.
    bind.execute(
        sa.text(
            """
            INSERT INTO chunk_embeddings (chunk_id, embedding_profile_id, embedding, created_at)
            SELECT c.id, :profile_id, c.embedding, now()
            FROM chunks c
            WHERE c.embedding IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM chunk_embeddings ce
                  WHERE ce.chunk_id = c.id AND ce.embedding_profile_id = :profile_id
              )
            """
        ),
        {"profile_id": profile_id},
    )


def downgrade() -> None:
    # Reverses exactly what upgrade() creates, identified by the
    # legacy-v0 / (provider, model, profile_version) tags above -- not a
    # blanket wipe of document_versions / embedding_profiles, in case later
    # phases have since added real (non-legacy) rows of their own.
    #
    # NOTE: this is a clean, in-place reversal intended for testing the
    # upgrade/downgrade cycle right after applying it (see
    # MIGRATION_RUNBOOK.md). For a production rollback after real ingestion
    # activity has happened on top of this data, restore from the pg_dump
    # backup instead of relying on this downgrade.
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            DELETE FROM chunk_embeddings
            WHERE embedding_profile_id IN (
                SELECT id FROM embedding_profiles
                WHERE provider = :provider
                  AND model = :model
                  AND profile_version = :profile_version
            )
            """
        ),
        {"provider": _PROVIDER, "model": _MODEL, "profile_version": _PROFILE_VERSION},
    )

    bind.execute(
        sa.text(
            """
            UPDATE chunks
            SET version_id = NULL
            WHERE version_id IN (
                SELECT id FROM document_versions
                WHERE parser_profile = :parser_profile AND chunker_profile = :chunker_profile
            )
            """
        ),
        {"parser_profile": _LEGACY_PARSER_PROFILE, "chunker_profile": _LEGACY_CHUNKER_PROFILE},
    )

    bind.execute(
        sa.text(
            """
            UPDATE documents
            SET active_version_id = NULL
            WHERE active_version_id IN (
                SELECT id FROM document_versions
                WHERE parser_profile = :parser_profile AND chunker_profile = :chunker_profile
            )
            """
        ),
        {"parser_profile": _LEGACY_PARSER_PROFILE, "chunker_profile": _LEGACY_CHUNKER_PROFILE},
    )

    bind.execute(
        sa.text(
            """
            DELETE FROM document_versions
            WHERE parser_profile = :parser_profile AND chunker_profile = :chunker_profile
            """
        ),
        {"parser_profile": _LEGACY_PARSER_PROFILE, "chunker_profile": _LEGACY_CHUNKER_PROFILE},
    )

    bind.execute(
        sa.text(
            """
            DELETE FROM embedding_profiles
            WHERE provider = :provider AND model = :model AND profile_version = :profile_version
            """
        ),
        {"provider": _PROVIDER, "model": _MODEL, "profile_version": _PROFILE_VERSION},
    )
