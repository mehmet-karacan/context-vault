"""Add canonical retrieval indexes, metadata, and run provenance.

Revision ID: cv3_00000005
Revises: cv3_00000004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector


revision: str = "cv3_00000005"
down_revision: str | None = "cv3_00000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    op.execute(sa.text("SET LOCAL statement_timeout = '10min'"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    # The legacy single-profile vector may be removed only after every value
    # has a canonical profile-bound copy. A non-zero count aborts the whole
    # transactional migration without changing user data.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM chunks c
                WHERE c.embedding IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM chunk_embeddings ce WHERE ce.chunk_id = c.id
                  )
              ) THEN
                RAISE EXCEPTION 'legacy chunk embeddings lack canonical copies';
              END IF;
            END $$
            """
        )
    )
    op.drop_index("chunks_embedding_idx", table_name="chunks")
    op.drop_column("chunks", "embedding")

    for name in (
        "symbol_qualified_name",
        "package_name",
        "schema_name",
        "table_name",
        "column_name",
    ):
        op.add_column("chunks", sa.Column(name, sa.Text(), nullable=True))
    op.add_column("chunks", sa.Column("search_profile", sa.String(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE chunks SET search_profile='simple-websearch-v1' "
            "WHERE search_profile IS NULL"
        )
    )
    op.alter_column("chunks", "search_profile", nullable=False)

    op.create_index(
        "ix_chunk_embeddings_embedding_hnsw",
        "chunk_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_chunks_search_vector_gin",
        "chunks",
        ["search_vector"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_chunks_symbol_name_trgm",
        "chunks",
        [sa.text("lower(symbol_name)")],
        postgresql_using="gin",
        postgresql_ops={"lower(symbol_name)": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_source_files_relative_path_trgm",
        "source_files",
        [sa.text("lower(relative_path)")],
        postgresql_using="gin",
        postgresql_ops={"lower(relative_path)": "gin_trgm_ops"},
    )

    op.create_table(
        "retrieval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "embedding_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("embedding_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "retriever_versions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "config_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "stage_latency_ms",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("no_answer_reason", sa.String(), nullable=True),
        sa.Column("fallback_reason", sa.String(), nullable=True),
        sa.Column("bundle_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "candidate_count >= 0 AND selected_count >= 0",
            name="ck_retrieval_runs_counts",
        ),
    )
    op.create_index(
        "ix_retrieval_runs_project_created",
        "retrieval_runs",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_runs_project_created", table_name="retrieval_runs")
    op.drop_table("retrieval_runs")
    op.drop_index("ix_source_files_relative_path_trgm", table_name="source_files")
    op.drop_index("ix_chunks_symbol_name_trgm", table_name="chunks")
    op.drop_index("ix_chunks_search_vector_gin", table_name="chunks")
    op.drop_index("ix_chunk_embeddings_embedding_hnsw", table_name="chunk_embeddings")
    op.drop_column("chunks", "search_profile")
    for name in reversed(
        (
            "symbol_qualified_name",
            "package_name",
            "schema_name",
            "table_name",
            "column_name",
        )
    ):
        op.drop_column("chunks", name)
    op.add_column("chunks", sa.Column("embedding", Vector(1024), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE chunks c SET embedding = ce.embedding
            FROM document_versions v
            JOIN chunk_embeddings ce
              ON ce.embedding_profile_id = v.embedding_profile_id
            WHERE v.id = c.version_id AND ce.chunk_id = c.id
            """
        )
    )
    op.create_index(
        "chunks_embedding_idx",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
