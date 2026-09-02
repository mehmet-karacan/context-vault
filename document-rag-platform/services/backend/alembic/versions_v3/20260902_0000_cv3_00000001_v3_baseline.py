"""Context Vault V3 pre-1.0 lineage reset baseline.

Revision ID: cv3_00000001
Revises:
Create Date: 2026-09-02

This static migration is the only operational V3 base. The b2f1c0a10001-
b2f1c0a10003 files remain in ``alembic/versions`` solely as incident-history
evidence and are excluded by ``alembic.ini``. Exact historical 00004/00005
revisions were unavailable, so this baseline is intended only for a new,
empty database. Existing databases must never be stamped to this revision.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "cv3_00000001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIMENSION = 1024
DEFAULT_PROFILE_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_PROFILE_CONFIG_HASH = (
    "5aee3eae0b5ee98b6a48990544a7e6e0b151c95077d90fe07c1eb978f2545127"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
    )

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="uploaded"),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("source_type", sa.String(), nullable=True),
        sa.Column("origin_uri", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("checksum", sa.String(), nullable=True),
        sa.Column("active_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("parser_profile", sa.String(), nullable=True),
        sa.Column("chunker_profile", sa.String(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column(
            "normalized_artifact_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "document_id",
            "version_no",
            name="uq_document_versions_document_id_version_no",
        ),
    )
    op.create_index(
        "ix_document_versions_document_id", "document_versions", ["document_id"]
    )
    op.create_foreign_key(
        "fk_documents_active_version_id_document_versions",
        "documents",
        "document_versions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documents_active_version_id", "documents", ["active_version_id"]
    )

    op.create_table(
        "source_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("is_binary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "is_generated", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "is_ignored", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_source_files_version_id", "source_files", ["version_id"])

    op.create_table(
        "document_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.String(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index(
        "ix_document_artifacts_version_id", "document_artifacts", ["version_id"]
    )
    op.create_foreign_key(
        "fk_document_versions_normalized_artifact_id_document_artifacts",
        "document_versions",
        "document_artifacts",
        ["normalized_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index("ix_ingestion_jobs_version_id", "ingestion_jobs", ["version_id"])

    op.create_table(
        "ingestion_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index("ix_ingestion_events_job_id", "ingestion_events", ["job_id"])

    op.create_table(
        "embedding_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("distance_metric", sa.String(), nullable=False),
        sa.Column("query_prefix", sa.Text(), nullable=True),
        sa.Column("passage_prefix", sa.Text(), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config_hash", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index(
        "uq_embedding_profiles_one_active",
        "embedding_profiles",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION), nullable=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=True),
        sa.Column("chunk_type", sa.String(), nullable=True),
        sa.Column("heading_path", postgresql.JSONB(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(), nullable=True),
        sa.Column("symbol_name", sa.String(), nullable=True),
        sa.Column("symbol_type", sa.String(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column(
            "parent_chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("identifiers", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_chunks_version_id", "chunks", ["version_id"])
    op.create_index("ix_chunks_source_file_id", "chunks", ["source_file_id"])
    op.create_index("ix_chunks_parent_chunk_id", "chunks", ["parent_chunk_id"])
    op.create_index(
        "chunks_embedding_idx",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "chunk_embeddings",
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "embedding_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("embedding_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint(
            "chunk_id", "embedding_profile_id", name="pk_chunk_embeddings"
        ),
    )
    op.create_index(
        "ix_chunk_embeddings_embedding_profile_id",
        "chunk_embeddings",
        ["embedding_profile_id"],
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("answerable", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "message_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("retrieval_score", sa.Float(), nullable=True),
        sa.Column("reranker_score", sa.Float(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("citation_label", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_message_citations_message_id", "message_citations", ["message_id"]
    )

    op.execute(
        sa.text(
            """
            INSERT INTO embedding_profiles (
                id, provider, model, dimension, distance_metric,
                query_prefix, passage_prefix, profile_version,
                config_hash, is_active, created_at
            ) VALUES (
                CAST(:profile_id AS uuid), 'openai_compatible',
                'openai/BAAI/bge-m3', 1024, 'cosine',
                'Bu soruyu ilgili belge parçalarını bulmak için temsil et: ',
                'Bu metni bir belge arama sisteminde bulunmak üzere temsil et: ',
                1, :config_hash, TRUE, now()
            )
            """
        ).bindparams(
            profile_id=DEFAULT_PROFILE_ID, config_hash=DEFAULT_PROFILE_CONFIG_HASH
        )
    )


def downgrade() -> None:
    op.drop_index("ix_message_citations_message_id", table_name="message_citations")
    op.drop_table("message_citations")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_project_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index(
        "ix_chunk_embeddings_embedding_profile_id", table_name="chunk_embeddings"
    )
    op.drop_table("chunk_embeddings")
    op.drop_index("chunks_embedding_idx", table_name="chunks")
    op.drop_index("ix_chunks_parent_chunk_id", table_name="chunks")
    op.drop_index("ix_chunks_source_file_id", table_name="chunks")
    op.drop_index("ix_chunks_version_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("uq_embedding_profiles_one_active", table_name="embedding_profiles")
    op.drop_table("embedding_profiles")
    op.drop_index("ix_ingestion_events_job_id", table_name="ingestion_events")
    op.drop_table("ingestion_events")
    op.drop_index("ix_ingestion_jobs_version_id", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_constraint(
        "fk_document_versions_normalized_artifact_id_document_artifacts",
        "document_versions",
        type_="foreignkey",
    )
    op.drop_index("ix_document_artifacts_version_id", table_name="document_artifacts")
    op.drop_table("document_artifacts")
    op.drop_index("ix_source_files_version_id", table_name="source_files")
    op.drop_table("source_files")
    op.drop_index("ix_documents_active_version_id", table_name="documents")
    op.drop_constraint(
        "fk_documents_active_version_id_document_versions",
        "documents",
        type_="foreignkey",
    )
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("projects")
