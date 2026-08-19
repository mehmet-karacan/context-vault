"""baseline: pre-Phase-2 schema (projects, documents, chunks)

Revision ID: b2f1c0a10001
Revises:
Create Date: 2026-08-19

Creates the three tables (``projects``, ``documents``, ``chunks``) exactly
as defined by ``services/backend/src/models.py`` prior to Aşama 2 (Context
Vault dönüşüm planı, AKTIF_GOREV.md Bölüm 3/8), including the
``chunks.embedding Vector(1024)`` column. This makes ``alembic upgrade
head`` work end-to-end from a genuinely empty database, not just from one
already bootstrapped by the old ``Base.metadata.create_all()`` path in
``src/db.py``.

For a database that was already created by that old create_all() path
(has ``projects``/``documents``/``chunks`` but no ``alembic_version``
table), stamp it to this revision instead of running upgrade() here, so
Alembic considers it "at baseline" without re-creating the already-existing
tables:

    alembic stamp b2f1c0a10001

Only after that should ``alembic upgrade head`` be run (see
MIGRATION_RUNBOOK.md for the full sequence).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "b2f1c0a10001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIMENSION = 1024  # BAAI/bge-m3 output size


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="uploaded"),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION), nullable=True),
    )

    # Same idempotent HNSW index src/db.py's init_db() creates on every
    # startup; harmless to also have it here so upgrade() alone produces a
    # fully working schema without depending on app-level bootstrap code.
    op.execute(
        "CREATE INDEX IF NOT EXISTS chunks_embedding_idx "
        "ON chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("projects")
