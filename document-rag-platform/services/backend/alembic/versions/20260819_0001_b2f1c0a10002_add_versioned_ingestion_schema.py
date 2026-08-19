"""add versioned ingestion, artifact, job, lexical and citation schema

Revision ID: b2f1c0a10002
Revises: b2f1c0a10001
Create Date: 2026-08-19

Additive schema migration for Aşama 2 (AKTIF_GOREV.md Bölüm 8). Adds the
tables and columns needed for versioned ingestion, object-storage artifacts,
async ingestion jobs, lexical/identifier search, multi-profile embeddings
and chat citations, without touching any existing table's existing columns,
data, or the current ``chunks.embedding Vector(1024)`` column / HNSW index.

New tables (Bölüm 8.2-8.10):
    document_versions, source_files, document_artifacts, ingestion_jobs,
    ingestion_events, embedding_profiles, chunk_embeddings, conversations,
    messages, message_citations

New nullable columns on existing tables:
    documents: source_type, origin_uri, mime_type, checksum,
               active_version_id, created_at, updated_at, deleted_at
    chunks:    version_id, source_file_id, sequence_no, chunk_type,
               heading_path, page_start, page_end, line_start, line_end,
               bbox, symbol_name, symbol_type, token_count, content_hash,
               parent_chunk_id, metadata_json, search_vector, identifiers,
               created_at

``downgrade()`` removes everything this migration adds, in dependency-safe
reverse order, and touches nothing else.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2f1c0a10002"
down_revision: Union[str, None] = "b2f1c0a10001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Matches src/models.py::EMBEDDING_DIMENSION -- the current single active
# dense profile. Hardcoded (not imported from src.models) because migration
# files must stay stable even as the application's models evolve.
_EMBEDDING_DIMENSION = 1024


def upgrade() -> None:
    # pgcrypto provides gen_random_uuid(), used by the next migration
    # (backfill) for set-based INSERT ... SELECT statements instead of a
    # per-row Python loop. Mirrors the existing precedent in src/db.py's
    # init_db(), which does `CREATE EXTENSION IF NOT EXISTS vector`.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # --- documents: additive nullable columns ---------------------------
    op.add_column("documents", sa.Column("source_type", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("origin_uri", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("mime_type", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("checksum", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.add_column("documents", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(), nullable=True))

    # --- document_versions (Bölüm 8.2) -----------------------------------
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
        # FK to document_artifacts.id added below, once that table exists
        # (document_artifacts.version_id references this table -> circular).
        sa.Column("normalized_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "document_id", "version_no", name="uq_document_versions_document_id_version_no"
        ),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])

    # documents.active_version_id -> document_versions.id
    op.add_column(
        "documents", sa.Column("active_version_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_documents_active_version_id_document_versions",
        "documents",
        "document_versions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_documents_active_version_id", "documents", ["active_version_id"])

    # --- source_files (Bölüm 8.3) -----------------------------------------
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
        sa.Column("is_generated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_ignored", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_source_files_version_id", "source_files", ["version_id"])

    # --- document_artifacts (Bölüm 8.4) ------------------------------------
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
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_document_artifacts_version_id", "document_artifacts", ["version_id"])

    # Now that document_artifacts exists, close the circular FK.
    op.create_foreign_key(
        "fk_document_versions_normalized_artifact_id_document_artifacts",
        "document_versions",
        "document_artifacts",
        ["normalized_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- ingestion_jobs (Bölüm 8.5) -----------------------------------------
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
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ingestion_jobs_version_id", "ingestion_jobs", ["version_id"])

    # --- ingestion_events (Bölüm 8.6) ---------------------------------------
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
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ingestion_events_job_id", "ingestion_events", ["job_id"])

    # --- embedding_profiles (Bölüm 8.8) -------------------------------------
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
        sa.Column("config_hash", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    # --- chunks: additive nullable columns (Bölüm 8.7) ----------------------
    op.add_column("chunks", sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "chunks", sa.Column("source_file_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("chunks", sa.Column("sequence_no", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("chunk_type", sa.String(), nullable=True))
    op.add_column("chunks", sa.Column("heading_path", postgresql.JSONB(), nullable=True))
    op.add_column("chunks", sa.Column("page_start", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("page_end", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("line_start", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("line_end", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("bbox", postgresql.JSONB(), nullable=True))
    op.add_column("chunks", sa.Column("symbol_name", sa.String(), nullable=True))
    op.add_column("chunks", sa.Column("symbol_type", sa.String(), nullable=True))
    op.add_column("chunks", sa.Column("token_count", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("content_hash", sa.String(), nullable=True))
    op.add_column(
        "chunks", sa.Column("parent_chunk_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("chunks", sa.Column("metadata_json", postgresql.JSONB(), nullable=True))
    op.add_column("chunks", sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True))
    op.add_column(
        "chunks", sa.Column("identifiers", postgresql.ARRAY(sa.Text()), nullable=True)
    )
    op.add_column("chunks", sa.Column("created_at", sa.DateTime(), nullable=True))

    op.create_foreign_key(
        "fk_chunks_version_id_document_versions",
        "chunks",
        "document_versions",
        ["version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_chunks_source_file_id_source_files",
        "chunks",
        "source_files",
        ["source_file_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_chunks_parent_chunk_id_chunks",
        "chunks",
        "chunks",
        ["parent_chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_chunks_version_id", "chunks", ["version_id"])
    op.create_index("ix_chunks_source_file_id", "chunks", ["source_file_id"])
    op.create_index("ix_chunks_parent_chunk_id", "chunks", ["parent_chunk_id"])

    # --- chunk_embeddings (Bölüm 8.9) ---------------------------------------
    # NOTE: this migration does not touch the existing chunks.embedding
    # Vector(1024) column or its HNSW index -- chunk_embeddings is an
    # additional, per-profile table, not a replacement.
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
        sa.Column("embedding", Vector(_EMBEDDING_DIMENSION), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("chunk_id", "embedding_profile_id", name="pk_chunk_embeddings"),
    )
    op.create_index(
        "ix_chunk_embeddings_embedding_profile_id", "chunk_embeddings", ["embedding_profile_id"]
    )

    # --- conversations / messages / message_citations (Bölüm 8.10) ---------
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
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
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
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
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
    op.create_index("ix_message_citations_message_id", "message_citations", ["message_id"])


def downgrade() -> None:
    # Reverse of upgrade(), in dependency-safe order. Does not drop the
    # `pgcrypto` extension (other data/sessions may depend on it, same as
    # `vector` is never dropped by src/db.py) and does not touch
    # chunks.embedding / its HNSW index.
    op.drop_index("ix_message_citations_message_id", table_name="message_citations")
    op.drop_table("message_citations")

    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_conversations_project_id", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("ix_chunk_embeddings_embedding_profile_id", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")

    op.drop_table("embedding_profiles")

    op.drop_constraint("fk_chunks_parent_chunk_id_chunks", "chunks", type_="foreignkey")
    op.drop_constraint("fk_chunks_source_file_id_source_files", "chunks", type_="foreignkey")
    op.drop_constraint("fk_chunks_version_id_document_versions", "chunks", type_="foreignkey")
    op.drop_index("ix_chunks_parent_chunk_id", table_name="chunks")
    op.drop_index("ix_chunks_source_file_id", table_name="chunks")
    op.drop_index("ix_chunks_version_id", table_name="chunks")
    for col in (
        "created_at",
        "identifiers",
        "search_vector",
        "metadata_json",
        "parent_chunk_id",
        "content_hash",
        "token_count",
        "symbol_type",
        "symbol_name",
        "bbox",
        "line_end",
        "line_start",
        "page_end",
        "page_start",
        "heading_path",
        "chunk_type",
        "sequence_no",
        "source_file_id",
        "version_id",
    ):
        op.drop_column("chunks", col)

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
        "fk_documents_active_version_id_document_versions", "documents", type_="foreignkey"
    )
    op.drop_column("documents", "active_version_id")

    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")

    for col in (
        "deleted_at",
        "updated_at",
        "created_at",
        "checksum",
        "mime_type",
        "origin_uri",
        "source_type",
    ):
        op.drop_column("documents", col)
