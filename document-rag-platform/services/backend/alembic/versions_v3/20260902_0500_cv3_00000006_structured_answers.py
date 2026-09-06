"""Add structured-answer claims and immutable citation provenance.

Revision ID: cv3_00000006
Revises: cv3_00000005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "cv3_00000006"
down_revision: str | None = "cv3_00000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    op.execute(sa.text("SET LOCAL statement_timeout = '10min'"))

    op.add_column(
        "conversations",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column("title_status", sa.String(), nullable=False, server_default="unset"),
    )
    op.add_column("conversations", sa.Column("title_model", sa.String()))
    op.add_column("conversations", sa.Column("title_prompt_hash", sa.String(64)))
    op.execute(
        sa.text(
            "UPDATE conversations c SET workspace_id=p.workspace_id "
            "FROM projects p WHERE p.id=c.project_id AND c.workspace_id IS NULL"
        )
    )
    op.create_index("ix_conversations_workspace_id", "conversations", ["workspace_id"])
    op.create_index("ix_conversations_principal_id", "conversations", ["principal_id"])

    op.add_column("messages", sa.Column("no_answer_reason", sa.String()))
    op.add_column("messages", sa.Column("prompt_template_version", sa.String()))
    op.add_column("messages", sa.Column("prompt_hash", sa.String(64)))
    op.add_column(
        "messages",
        sa.Column(
            "generation_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("messages", sa.Column("deleted_at", sa.DateTime(timezone=True)))

    op.create_table(
        "message_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim_index", sa.Integer(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("claim_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("claim_index > 0", name="ck_message_claim_index_positive"),
        sa.UniqueConstraint("message_id", "claim_index", name="uq_message_claim_index"),
    )
    op.create_index("ix_message_claims_message_id", "message_claims", ["message_id"])

    op.add_column(
        "message_citations",
        sa.Column(
            "retrieval_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("retrieval_runs.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "message_citations",
        sa.Column(
            "embedding_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("embedding_profiles.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "message_citations",
        sa.Column("usage_order", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("message_citations", sa.Column("fusion_score", sa.Float()))
    op.add_column(
        "message_citations",
        sa.Column(
            "locator_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "message_citations", sa.Column("evidence_snapshot_encrypted", sa.LargeBinary())
    )
    op.add_column("message_citations", sa.Column("evidence_hash", sa.String(64)))
    op.add_column("message_citations", sa.Column("content_hash", sa.String(64)))
    op.add_column("message_citations", sa.Column("model", sa.String()))
    op.add_column(
        "message_citations", sa.Column("prompt_template_version", sa.String())
    )
    op.add_column("message_citations", sa.Column("prompt_hash", sa.String(64)))
    op.add_column(
        "message_citations",
        sa.Column(
            "generation_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "message_citations",
        sa.Column(
            "validation_result", sa.String(), nullable=False, server_default="legacy"
        ),
    )
    op.add_column(
        "message_citations",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "message_citations",
        sa.Column("evidence_expires_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "claim_citations",
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("message_claims.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "citation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("message_citations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_order", sa.Integer(), nullable=False),
        sa.CheckConstraint("source_order > 0", name="ck_claim_citation_order_positive"),
    )


def downgrade() -> None:
    op.drop_table("claim_citations")
    for column in (
        "evidence_expires_at",
        "created_at",
        "validation_result",
        "generation_config",
        "prompt_hash",
        "prompt_template_version",
        "model",
        "content_hash",
        "evidence_hash",
        "evidence_snapshot_encrypted",
        "locator_json",
        "fusion_score",
        "usage_order",
        "embedding_profile_id",
        "retrieval_run_id",
    ):
        op.drop_column("message_citations", column)
    op.drop_index("ix_message_claims_message_id", table_name="message_claims")
    op.drop_table("message_claims")
    for column in (
        "deleted_at",
        "generation_config",
        "prompt_hash",
        "prompt_template_version",
        "no_answer_reason",
    ):
        op.drop_column("messages", column)
    op.drop_index("ix_conversations_principal_id", table_name="conversations")
    op.drop_index("ix_conversations_workspace_id", table_name="conversations")
    for column in (
        "title_prompt_hash",
        "title_model",
        "title_status",
        "principal_id",
        "workspace_id",
    ):
        op.drop_column("conversations", column)
