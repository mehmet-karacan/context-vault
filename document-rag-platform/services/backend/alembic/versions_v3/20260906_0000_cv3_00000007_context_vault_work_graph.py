"""Add the authoritative Context Vault Work Graph and registries.

Revision ID: cv3_00000007
Revises: cv3_00000006

The upgrade is additive and does not rewrite or delete existing user rows.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "cv3_00000007"
down_revision: str | None = "cv3_00000006"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def upgrade() -> None:
    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    op.execute(sa.text("SET LOCAL statement_timeout = '10min'"))

    op.create_table(
        "work_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "workspace_id",
            UUID,
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column(
            "scope_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "exclusions_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_class", sa.String(), nullable=False, server_default="low"),
        sa.Column(
            "required_approvals",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("expected_revision", sa.String(), nullable=False),
        sa.Column("expected_baseline_hash", sa.String(64)),
        sa.Column("status", sa.String(), nullable=False, server_default="DRAFT"),
        sa.Column(
            "created_by_principal_id",
            UUID,
            sa.ForeignKey("principals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "owner_principal_id",
            UUID,
            sa.ForeignKey("principals.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "parent_id", UUID, sa.ForeignKey("work_items.id", ondelete="RESTRICT")
        ),
        sa.Column(
            "acceptance_criteria",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "evidence_requirements",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "next_fencing_token", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("active_fencing_token", sa.BigInteger()),
        sa.Column("active_attempt_id", UUID),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('DRAFT','READY','CLAIMED','RUNNING','BLOCKED','VERIFYING',"
            "'COMPLETED','FAILED','CANCELLED','RECOVERY_REQUIRED')",
            name="ck_work_items_status",
        ),
        sa.CheckConstraint(
            "risk_class IN ('low','medium','high','critical')",
            name="ck_work_items_risk_class",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_work_items_revision"),
        sa.CheckConstraint(
            "next_fencing_token >= 0", name="ck_work_items_next_fencing_token"
        ),
    )
    op.create_index("ix_work_items_workspace_id", "work_items", ["workspace_id"])
    op.create_index("ix_work_items_project_id", "work_items", ["project_id"])
    op.create_index(
        "ix_work_items_project_status", "work_items", ["project_id", "status"]
    )

    op.create_table(
        "work_item_dependencies",
        sa.Column(
            "work_item_id",
            UUID,
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "depends_on_work_item_id",
            UUID,
            sa.ForeignKey("work_items.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "dependency_type", sa.String(), nullable=False, server_default="blocks"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "work_item_id <> depends_on_work_item_id",
            name="ck_work_item_dependencies_not_self",
        ),
    )

    op.create_table(
        "work_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "work_item_id",
            UUID,
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("executor_id", sa.String(), nullable=False),
        sa.Column("adapter_id", sa.String()),
        sa.Column("model_id", sa.String()),
        sa.Column("provider_id", sa.String()),
        sa.Column("status", sa.String(), nullable=False, server_default="PREPARED"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("input_context_manifest_hash", sa.String(64)),
        sa.Column("expected_revision", sa.String(), nullable=False),
        sa.Column("drift_token", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("claim_request_hash", sa.String(64), nullable=False),
        sa.Column("effect_idempotency_key", sa.String()),
        sa.Column("effect_started_at", sa.DateTime(timezone=True)),
        sa.Column("rollback_idempotency_key", sa.String()),
        sa.Column("rollback_request_hash", sa.String(64)),
        sa.Column("rollback_started_at", sa.DateTime(timezone=True)),
        sa.Column(
            "outcome_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("terminal_reason", sa.String()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "work_item_id", "attempt_no", name="uq_work_attempt_item_number"
        ),
        sa.UniqueConstraint(
            "work_item_id", "idempotency_key", name="uq_work_attempt_item_idempotency"
        ),
        sa.UniqueConstraint(
            "work_item_id",
            "effect_idempotency_key",
            name="uq_work_attempt_effect_idempotency",
        ),
        sa.CheckConstraint("attempt_no > 0", name="ck_work_attempt_number_positive"),
        sa.CheckConstraint(
            "status IN ('PREPARED','CLAIMED','RUNNING','BLOCKED','VERIFYING',"
            "'COMPLETED','FAILED','CANCELLED','RECOVERY_REQUIRED')",
            name="ck_work_attempts_status",
        ),
        sa.CheckConstraint(
            "effect_started_at IS NULL OR effect_idempotency_key IS NOT NULL",
            name="ck_work_attempt_effect_intent",
        ),
        sa.CheckConstraint(
            "rollback_started_at IS NULL OR "
            "(rollback_idempotency_key IS NOT NULL AND rollback_request_hash IS NOT NULL)",
            name="ck_work_attempt_rollback_intent",
        ),
        sa.UniqueConstraint(
            "work_item_id",
            "rollback_idempotency_key",
            name="uq_work_attempt_rollback_idempotency",
        ),
    )
    op.create_index("ix_work_attempts_work_item_id", "work_attempts", ["work_item_id"])
    op.create_index(
        "ix_work_attempts_item_status", "work_attempts", ["work_item_id", "status"]
    )
    op.create_foreign_key(
        "fk_work_items_active_attempt",
        "work_items",
        "work_attempts",
        ["active_attempt_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "work_claims",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "work_item_id",
            UUID,
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            UUID,
            sa.ForeignKey("work_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_scope", JSONB, nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("claimant_id", sa.String(), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("released_reason", sa.String()),
        sa.Column("reconciled_reason", sa.String()),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "work_item_id", "fencing_token", name="uq_work_claim_item_fencing"
        ),
        sa.UniqueConstraint("attempt_id", name="uq_work_claim_attempt"),
        sa.CheckConstraint("fencing_token > 0", name="ck_work_claim_fencing_positive"),
        sa.CheckConstraint(
            "status IN ('active','released','reconciled')",
            name="ck_work_claims_status",
        ),
        sa.CheckConstraint(
            "expires_at > acquired_at", name="ck_work_claim_expiry_after_acquired"
        ),
        sa.CheckConstraint(
            "heartbeat_at >= acquired_at AND heartbeat_at <= expires_at",
            name="ck_work_claim_heartbeat_window",
        ),
    )
    op.create_index("ix_work_claims_work_item_id", "work_claims", ["work_item_id"])
    op.create_index("ix_work_claims_attempt_id", "work_claims", ["attempt_id"])
    op.create_index("ix_work_claims_expiry", "work_claims", ["status", "expires_at"])
    op.create_index(
        "uq_work_claim_one_active",
        "work_claims",
        ["work_item_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "work_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "work_item_id",
            UUID,
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id", UUID, sa.ForeignKey("work_attempts.id", ondelete="SET NULL")
        ),
        sa.Column("event_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("correlation_id", UUID, nullable=False),
        sa.Column("causation_id", UUID),
        sa.Column("previous_state", sa.String()),
        sa.Column("new_state", sa.String()),
        sa.Column("payload_schema_version", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "payload_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "work_item_id", "event_sequence", name="uq_work_event_item_sequence"
        ),
        sa.CheckConstraint(
            "event_sequence > 0", name="ck_work_event_sequence_positive"
        ),
        sa.CheckConstraint(
            "actor_type IN ('human','policy','service','cli','model')",
            name="ck_work_event_actor_type",
        ),
    )
    op.create_index(
        "ix_work_events_item_created", "work_events", ["work_item_id", "created_at"]
    )

    op.create_table(
        "work_approvals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "work_item_id",
            UUID,
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id", UUID, sa.ForeignKey("work_attempts.id", ondelete="SET NULL")
        ),
        sa.Column("approval_type", sa.String(), nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "evidence_refs",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "work_item_id",
            "approval_type",
            "scope_hash",
            "actor_id",
            name="uq_work_approval_actor_scope",
        ),
        sa.CheckConstraint(
            "actor_type IN ('human','policy')", name="ck_work_approval_actor_type"
        ),
        sa.CheckConstraint(
            "decision IN ('approved','rejected','revoked')",
            name="ck_work_approval_decision",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > granted_at",
            name="ck_work_approval_expiry",
        ),
    )
    op.create_index(
        "ix_work_approvals_work_item_id", "work_approvals", ["work_item_id"]
    )

    op.create_table(
        "work_receipts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "work_item_id",
            UUID,
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            UUID,
            sa.ForeignKey("work_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "claim_id", UUID, sa.ForeignKey("work_claims.id", ondelete="SET NULL")
        ),
        sa.Column(
            "parent_receipt_id",
            UUID,
            sa.ForeignKey("work_receipts.id", ondelete="RESTRICT"),
        ),
        sa.Column("receipt_type", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_status", sa.Integer(), nullable=False),
        sa.Column("input_artifact_hash", sa.String(64)),
        sa.Column("output_artifact_hash", sa.String(64)),
        sa.Column("before_revision", sa.String(), nullable=False),
        sa.Column("after_revision", sa.String(), nullable=False),
        sa.Column(
            "test_evidence_refs",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "acceptance_evidence",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("rollback_result", JSONB),
        sa.Column("signer_type", sa.String(), nullable=False),
        sa.Column("signer_id", sa.String(), nullable=False),
        sa.Column(
            "attestation", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "is_terminal", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "work_item_id",
            "receipt_type",
            "idempotency_key",
            name="uq_work_receipt_item_type_idempotency",
        ),
        sa.CheckConstraint(
            "receipt_type IN ('prepare','apply','verify','close','rollback')",
            name="ck_work_receipts_type",
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64", name="ck_work_receipt_request_hash"
        ),
        sa.CheckConstraint(
            "(receipt_type = 'apply' AND parent_receipt_id IS NULL) OR "
            "(receipt_type IN ('verify','close','rollback') AND parent_receipt_id IS NOT NULL) OR "
            "receipt_type = 'prepare'",
            name="ck_work_receipt_parent_chain",
        ),
        sa.CheckConstraint(
            "is_terminal = (receipt_type = 'close')",
            name="ck_work_receipt_terminal_close",
        ),
        sa.CheckConstraint(
            "parent_receipt_id IS NULL OR parent_receipt_id <> id",
            name="ck_work_receipt_parent_not_self",
        ),
        sa.CheckConstraint("ended_at >= started_at", name="ck_work_receipt_time_order"),
        sa.CheckConstraint(
            "signer_type IN ('human','policy','service','cli')",
            name="ck_work_receipt_signer_type",
        ),
    )
    op.create_index(
        "uq_work_receipt_one_terminal",
        "work_receipts",
        ["work_item_id"],
        unique=True,
        postgresql_where=sa.text("is_terminal"),
    )
    op.create_index(
        "ix_work_receipts_attempt", "work_receipts", ["attempt_id", "created_at"]
    )

    op.create_table(
        "artifact_refs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "work_item_id",
            UUID,
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id", UUID, sa.ForeignKey("work_attempts.id", ondelete="SET NULL")
        ),
        sa.Column(
            "receipt_id", UUID, sa.ForeignKey("work_receipts.id", ondelete="SET NULL")
        ),
        sa.Column("artifact_type", sa.String(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column(
            "classification", sa.String(), nullable=False, server_default="internal"
        ),
        sa.Column(
            "metadata_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("uri", "sha256", name="uq_artifact_ref_uri_hash"),
        sa.CheckConstraint(
            "classification IN ('public','internal','confidential','restricted')",
            name="ck_artifact_ref_classification",
        ),
    )
    op.create_index("ix_artifact_refs_work_item_id", "artifact_refs", ["work_item_id"])

    _create_knowledge_context_registry_tables()
    _create_knowledge_lifecycle_triggers()
    _create_work_graph_triggers()


def _create_knowledge_context_registry_tables() -> None:
    op.create_table(
        "decision_records",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "workspace_id",
            UUID,
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT")
        ),
        sa.Column("decision_key", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="PROPOSED"),
        sa.Column(
            "source_artifact_id",
            UUID,
            sa.ForeignKey("artifact_refs.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "supersedes_decision_id",
            UUID,
            sa.ForeignKey("decision_records.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "owner_principal_id",
            UUID,
            sa.ForeignKey("principals.id", ondelete="RESTRICT"),
        ),
        sa.Column("approved_by_type", sa.String()),
        sa.Column("approved_by_id", sa.String()),
        *_timestamps(),
        sa.UniqueConstraint(
            "workspace_id", "decision_key", name="uq_decision_record_workspace_key"
        ),
        sa.CheckConstraint(
            "status IN ('PROPOSED','APPROVED','SUPERSEDED','REVOKED')",
            name="ck_decision_record_status",
        ),
        sa.CheckConstraint(
            "approved_by_type IS NULL OR approved_by_type IN ('human','policy')",
            name="ck_decision_record_approver",
        ),
        sa.CheckConstraint(
            "status <> 'APPROVED' OR approved_by_type IN ('human','policy')",
            name="ck_decision_record_approved_actor",
        ),
    )
    op.create_index(
        "ix_decision_records_workspace_id", "decision_records", ["workspace_id"]
    )
    op.create_index(
        "ix_decision_records_project_id", "decision_records", ["project_id"]
    )

    op.create_table(
        "knowledge_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "workspace_id",
            UUID,
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT")
        ),
        sa.Column("stable_key", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("classification", sa.String(), nullable=False),
        sa.Column(
            "owner_id", UUID, sa.ForeignKey("principals.id", ondelete="RESTRICT")
        ),
        sa.Column("active_revision_id", UUID),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_knowledge_items_workspace_id", "knowledge_items", ["workspace_id"]
    )
    op.create_index("ix_knowledge_items_project_id", "knowledge_items", ["project_id"])
    op.create_index(
        "uq_knowledge_item_scope_key",
        "knowledge_items",
        [
            "workspace_id",
            sa.text(
                "coalesce(project_id, '00000000-0000-0000-0000-000000000000'::uuid)"
            ),
            "stable_key",
        ],
        unique=True,
    )

    op.create_table(
        "knowledge_revisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "knowledge_item_id",
            UUID,
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="PROPOSED"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "source_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "evidence_refs",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "owner_id", UUID, sa.ForeignKey("principals.id", ondelete="RESTRICT")
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("classification", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column(
            "supersedes_revision_id",
            UUID,
            sa.ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"),
        ),
        sa.Column("proposed_by_type", sa.String(), nullable=False),
        sa.Column("proposed_by_id", sa.String(), nullable=False),
        sa.Column("reviewed_by_type", sa.String()),
        sa.Column("reviewed_by_id", sa.String()),
        sa.Column("approved_by_type", sa.String()),
        sa.Column("approved_by_id", sa.String()),
        sa.Column("terminal_by_type", sa.String()),
        sa.Column("terminal_by_id", sa.String()),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "knowledge_item_id", "version", name="uq_knowledge_revision_version"
        ),
        sa.UniqueConstraint(
            "knowledge_item_id", "content_hash", name="uq_knowledge_revision_hash"
        ),
        sa.CheckConstraint("version > 0", name="ck_knowledge_revision_version"),
        sa.CheckConstraint(
            "status IN ('PROPOSED','REVIEWED','APPROVED','SUPERSEDED','REVOKED','EXPIRED')",
            name="ck_knowledge_revision_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_knowledge_revision_confidence",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_knowledge_revision_validity",
        ),
        sa.CheckConstraint(
            "proposed_by_type IN ('human','policy','model','service')",
            name="ck_knowledge_revision_proposer",
        ),
        sa.CheckConstraint(
            "reviewed_by_type IS NULL OR reviewed_by_type IN ('human','policy')",
            name="ck_knowledge_revision_reviewer",
        ),
        sa.CheckConstraint(
            "approved_by_type IS NULL OR approved_by_type IN ('human','policy')",
            name="ck_knowledge_revision_approver",
        ),
        sa.CheckConstraint(
            "(reviewed_by_type IS NULL) = (reviewed_by_id IS NULL)",
            name="ck_knowledge_revision_reviewer_pair",
        ),
        sa.CheckConstraint(
            "(approved_by_type IS NULL) = (approved_by_id IS NULL)",
            name="ck_knowledge_revision_approver_pair",
        ),
        sa.CheckConstraint(
            "(terminal_by_type IS NULL) = (terminal_by_id IS NULL)",
            name="ck_knowledge_revision_terminal_pair",
        ),
        sa.CheckConstraint(
            "CASE status "
            "WHEN 'PROPOSED' THEN reviewed_by_type IS NULL AND approved_by_type IS NULL "
            "WHEN 'REVIEWED' THEN reviewed_by_type IN ('human','policy') "
            "AND approved_by_type IS NULL "
            "ELSE reviewed_by_type IN ('human','policy') "
            "AND approved_by_type IN ('human','policy') END",
            name="ck_knowledge_revision_authority_audit",
        ),
        sa.CheckConstraint(
            "terminal_by_type IS NULL OR terminal_by_type IN ('human','policy')",
            name="ck_knowledge_revision_terminal_actor",
        ),
        sa.CheckConstraint(
            "(status IN ('SUPERSEDED','REVOKED','EXPIRED')) = "
            "(terminal_by_type IS NOT NULL AND terminal_by_id IS NOT NULL "
            "AND terminal_at IS NOT NULL)",
            name="ck_knowledge_revision_terminal_audit",
        ),
    )
    op.create_index(
        "ix_knowledge_revision_item_status_validity",
        "knowledge_revisions",
        ["knowledge_item_id", "status", "valid_from", "valid_to"],
    )
    op.create_index(
        "ix_knowledge_revisions_knowledge_item_id",
        "knowledge_revisions",
        ["knowledge_item_id"],
    )
    op.create_foreign_key(
        "fk_knowledge_items_active_revision",
        "knowledge_items",
        "knowledge_revisions",
        ["active_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "knowledge_conflicts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "knowledge_item_id",
            UUID,
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "left_revision_id",
            UUID,
            sa.ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "right_revision_id",
            UUID,
            sa.ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="OPEN"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "resolution_revision_id",
            UUID,
            sa.ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"),
        ),
        sa.Column("resolved_by_type", sa.String()),
        sa.Column("resolved_by_id", sa.String()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('OPEN','RESOLVED')", name="ck_knowledge_conflict_status"
        ),
        sa.CheckConstraint(
            "left_revision_id <> right_revision_id",
            name="ck_knowledge_conflict_not_self",
        ),
        sa.CheckConstraint(
            "resolved_by_type IS NULL OR resolved_by_type IN ('human','policy')",
            name="ck_knowledge_conflict_resolver",
        ),
        sa.CheckConstraint(
            "(status = 'RESOLVED') = "
            "(resolution_revision_id IS NOT NULL AND resolved_by_type IS NOT NULL "
            "AND resolved_by_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_knowledge_conflict_resolution_audit",
        ),
    )
    op.create_index(
        "uq_knowledge_conflict_pair",
        "knowledge_conflicts",
        [
            "knowledge_item_id",
            sa.text("least(left_revision_id, right_revision_id)"),
            sa.text("greatest(left_revision_id, right_revision_id)"),
        ],
        unique=True,
    )

    op.create_table(
        "context_sources",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("load_tier", sa.String(), nullable=False),
        sa.Column("classification", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "supersedes_source_id",
            UUID,
            sa.ForeignKey("context_sources.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "provider_policy",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("token_cost", sa.Integer(), nullable=False),
        sa.Column(
            "metadata_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "project_id", "source_id", "version", name="uq_context_source_version"
        ),
        sa.CheckConstraint("version > 0", name="ck_context_source_version"),
        sa.CheckConstraint("token_cost >= 0", name="ck_context_source_token_cost"),
        sa.CheckConstraint(
            "load_tier IN ('MUST_LOAD','SHOULD_LOAD_IF_RELEVANT',"
            "'RETRIEVE_ON_DEMAND','NEVER_AUTO_LOAD')",
            name="ck_context_source_load_tier",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','SUPERSEDED','STALE')",
            name="ck_context_source_status",
        ),
    )
    op.create_index("ix_context_sources_project_id", "context_sources", ["project_id"])
    op.create_index(
        "ix_context_source_project_status", "context_sources", ["project_id", "status"]
    )

    op.create_table(
        "provider_registry",
        sa.Column("provider_id", sa.String(), primary_key=True),
        sa.Column("adapter_id", sa.String(), nullable=False),
        sa.Column("locality", sa.String(), nullable=False),
        sa.Column(
            "network_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("secret_ref", sa.Text()),
        sa.Column("health_state", sa.String(), nullable=False),
        sa.Column(
            "circuit_open", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column(
            "metadata_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "locality IN ('local','remote')", name="ck_provider_registry_locality"
        ),
        sa.CheckConstraint(
            "health_state IN ('healthy','degraded','unhealthy','disabled')",
            name="ck_provider_registry_health",
        ),
        sa.CheckConstraint("length(config_hash) = 64", name="ck_provider_config_hash"),
    )

    op.create_table(
        "model_registry",
        sa.Column("model_id", sa.String(), primary_key=True),
        sa.Column(
            "provider_id",
            sa.String(),
            sa.ForeignKey("provider_registry.provider_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "capabilities", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("context_limit", sa.Integer(), nullable=False),
        sa.Column("output_limit", sa.Integer(), nullable=False),
        sa.Column(
            "data_classifications",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("embedding_profile", JSONB),
        sa.Column(
            "benchmark", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "health_state", sa.String(), nullable=False, server_default="healthy"
        ),
        sa.Column(
            "circuit_open", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("accessible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "fallback_model_ids",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("context_limit > 0", name="ck_model_context_limit"),
        sa.CheckConstraint("output_limit > 0", name="ck_model_output_limit"),
        sa.CheckConstraint(
            "health_state IN ('healthy','degraded','unhealthy','disabled')",
            name="ck_model_registry_health",
        ),
        sa.CheckConstraint("length(config_hash) = 64", name="ck_model_config_hash"),
    )
    op.create_index("ix_model_registry_provider_id", "model_registry", ["provider_id"])

    op.create_table(
        "skill_registry",
        sa.Column("skill_id", sa.String(), primary_key=True),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("package_hash", sa.String(64), nullable=False),
        sa.Column("publisher", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("trust_level", sa.String(), nullable=False),
        sa.Column(
            "required_tools",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "required_permissions",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "network_scope",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "filesystem_scope",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "supported_adapters",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "receipt_refs", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("license_id", sa.String()),
        sa.Column("security_scan_receipt", sa.Text(), nullable=False),
        sa.Column("enabled_scope_type", sa.String(), nullable=False),
        sa.Column("enabled_scope_id", sa.String()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("length(commit_sha) = 40", name="ck_skill_commit_sha"),
        sa.CheckConstraint("length(package_hash) = 64", name="ck_skill_package_hash"),
        sa.CheckConstraint(
            "lower(version) NOT IN ('main','master','latest','head')",
            name="ck_skill_immutable_version",
        ),
        sa.CheckConstraint(
            "trust_level IN ('untrusted','restricted','verified')",
            name="ck_skill_trust_level",
        ),
    )

    op.create_table(
        "context_manifests",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "attempt_id", UUID, sa.ForeignKey("work_attempts.id", ondelete="RESTRICT")
        ),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column(
            "provider_id",
            sa.String(),
            sa.ForeignKey("provider_registry.provider_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "model_id",
            sa.String(),
            sa.ForeignKey("model_registry.model_id", ondelete="RESTRICT"),
        ),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("reserved_output", sa.Integer(), nullable=False),
        sa.Column("safety_margin", sa.Integer(), nullable=False),
        sa.Column("core_json", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("manifest_hash", name="uq_context_manifest_hash"),
        sa.UniqueConstraint("attempt_id", name="uq_context_manifest_attempt"),
        sa.CheckConstraint(
            "token_budget > 0 AND reserved_output >= 0 AND safety_margin >= 0",
            name="ck_context_manifest_budget",
        ),
        sa.CheckConstraint(
            "token_budget > reserved_output + safety_margin",
            name="ck_context_manifest_available_budget",
        ),
    )

    op.create_table(
        "compiled_contexts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "context_manifest_id",
            UUID,
            sa.ForeignKey("context_manifests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("adapter_id", sa.String(), nullable=False),
        sa.Column("rendered_hash", sa.String(64), nullable=False),
        sa.Column(
            "rendered_artifact_ref",
            UUID,
            sa.ForeignKey("artifact_refs.id", ondelete="SET NULL"),
        ),
        sa.Column("token_cost", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "context_manifest_id", "adapter_id", name="uq_compiled_context_adapter"
        ),
        sa.CheckConstraint("token_cost >= 0", name="ck_compiled_context_token_cost"),
    )
    op.create_index(
        "ix_compiled_contexts_context_manifest_id",
        "compiled_contexts",
        ["context_manifest_id"],
    )


def _create_knowledge_lifecycle_triggers() -> None:
    op.execute(
        sa.text(
            """
            CREATE FUNCTION cv3_enforce_knowledge_revision_lifecycle()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'knowledge revisions are immutable and cannot be deleted';
              END IF;

              IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'PROPOSED' THEN
                  RAISE EXCEPTION 'knowledge revision status must start at PROPOSED';
                END IF;
                RETURN NEW;
              END IF;

              IF ROW(
                OLD.knowledge_item_id, OLD.version, OLD.content, OLD.content_hash,
                OLD.source_refs, OLD.evidence_refs, OLD.owner_id, OLD.scope,
                OLD.classification, OLD.confidence, OLD.valid_from, OLD.valid_to,
                OLD.supersedes_revision_id, OLD.proposed_by_type,
                OLD.proposed_by_id, OLD.created_at
              ) IS DISTINCT FROM ROW(
                NEW.knowledge_item_id, NEW.version, NEW.content, NEW.content_hash,
                NEW.source_refs, NEW.evidence_refs, NEW.owner_id, NEW.scope,
                NEW.classification, NEW.confidence, NEW.valid_from, NEW.valid_to,
                NEW.supersedes_revision_id, NEW.proposed_by_type,
                NEW.proposed_by_id, NEW.created_at
              ) THEN
                RAISE EXCEPTION 'knowledge revision provenance and content are immutable';
              END IF;

              IF OLD.status = NEW.status THEN
                IF ROW(
                  OLD.reviewed_by_type, OLD.reviewed_by_id,
                  OLD.approved_by_type, OLD.approved_by_id,
                  OLD.terminal_by_type, OLD.terminal_by_id, OLD.terminal_at
                ) IS DISTINCT FROM ROW(
                  NEW.reviewed_by_type, NEW.reviewed_by_id,
                  NEW.approved_by_type, NEW.approved_by_id,
                  NEW.terminal_by_type, NEW.terminal_by_id, NEW.terminal_at
                ) THEN
                  RAISE EXCEPTION 'knowledge authority audit can change only with status';
                END IF;
                RETURN NEW;
              END IF;

              IF OLD.status = 'PROPOSED' AND NEW.status = 'REVIEWED' THEN
                IF NEW.reviewed_by_type NOT IN ('human','policy')
                   OR NEW.reviewed_by_id IS NULL
                   OR NEW.approved_by_type IS NOT NULL
                   OR NEW.approved_by_id IS NOT NULL
                   OR NEW.terminal_by_type IS NOT NULL
                   OR NEW.terminal_by_id IS NOT NULL
                   OR NEW.terminal_at IS NOT NULL THEN
                  RAISE EXCEPTION 'REVIEWED requires exact reviewer audit only';
                END IF;
                RETURN NEW;
              END IF;

              IF OLD.status = 'REVIEWED' AND NEW.status = 'APPROVED' THEN
                IF ROW(OLD.reviewed_by_type, OLD.reviewed_by_id)
                     IS DISTINCT FROM
                   ROW(NEW.reviewed_by_type, NEW.reviewed_by_id)
                   OR NEW.approved_by_type NOT IN ('human','policy')
                   OR NEW.approved_by_id IS NULL
                   OR NEW.terminal_by_type IS NOT NULL
                   OR NEW.terminal_by_id IS NOT NULL
                   OR NEW.terminal_at IS NOT NULL THEN
                  RAISE EXCEPTION 'APPROVED requires preserved review and exact approver audit';
                END IF;
                RETURN NEW;
              END IF;

              IF OLD.status = 'APPROVED'
                 AND NEW.status IN ('SUPERSEDED','REVOKED','EXPIRED') THEN
                IF ROW(
                     OLD.reviewed_by_type, OLD.reviewed_by_id,
                     OLD.approved_by_type, OLD.approved_by_id
                   ) IS DISTINCT FROM ROW(
                     NEW.reviewed_by_type, NEW.reviewed_by_id,
                     NEW.approved_by_type, NEW.approved_by_id
                   )
                   OR NEW.terminal_by_type NOT IN ('human','policy')
                   OR NEW.terminal_by_id IS NULL
                   OR NEW.terminal_at IS NULL THEN
                  RAISE EXCEPTION 'terminal knowledge requires preserved authority and exact terminal audit';
                END IF;
                RETURN NEW;
              END IF;

              RAISE EXCEPTION 'invalid knowledge transition: % -> %', OLD.status, NEW.status;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_knowledge_revisions_lifecycle
            BEFORE INSERT OR UPDATE OR DELETE ON knowledge_revisions
            FOR EACH ROW EXECUTE FUNCTION cv3_enforce_knowledge_revision_lifecycle()
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION cv3_enforce_knowledge_active_head()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF EXISTS (
                SELECT 1
                  FROM knowledge_items item
                  LEFT JOIN knowledge_revisions active
                    ON active.id = item.active_revision_id
                 WHERE item.active_revision_id IS NOT NULL
                   AND (
                     active.id IS NULL
                     OR active.knowledge_item_id <> item.id
                     OR active.status <> 'APPROVED'
                   )
              ) THEN
                RAISE EXCEPTION 'active knowledge revision must belong to its item and be APPROVED';
              END IF;

              IF EXISTS (
                SELECT 1
                  FROM knowledge_revisions revision
                  LEFT JOIN knowledge_items item
                    ON item.id = revision.knowledge_item_id
                   AND item.active_revision_id = revision.id
                 WHERE revision.status = 'APPROVED'
                   AND item.id IS NULL
              ) THEN
                RAISE EXCEPTION 'every APPROVED knowledge revision must be the active head';
              END IF;
              RETURN NULL;
            END;
            $$
            """
        )
    )
    for table in ("knowledge_items", "knowledge_revisions"):
        op.execute(
            sa.text(
                f"""
                CREATE CONSTRAINT TRIGGER trg_{table}_active_head
                AFTER INSERT OR UPDATE OR DELETE ON {table}
                DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION cv3_enforce_knowledge_active_head()
                """
            )
        )


def _create_work_graph_triggers() -> None:
    op.execute(
        sa.text(
            """
            CREATE FUNCTION cv3_reject_append_only_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$
            """
        )
    )
    for table in ("work_events", "work_receipts"):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table}_append_only
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION cv3_reject_append_only_mutation()
                """
            )
        )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION cv3_enforce_work_item_transition()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
              transition_allowed boolean := false;
            BEGIN
              IF OLD.status = NEW.status THEN
                RETURN NEW;
              END IF;
              transition_allowed := CASE OLD.status
                WHEN 'DRAFT' THEN NEW.status IN ('READY','CANCELLED')
                WHEN 'READY' THEN NEW.status IN ('CLAIMED','CANCELLED')
                WHEN 'CLAIMED' THEN NEW.status IN ('RUNNING','CANCELLED','RECOVERY_REQUIRED')
                WHEN 'RUNNING' THEN NEW.status IN ('BLOCKED','VERIFYING','FAILED','CANCELLED','RECOVERY_REQUIRED')
                WHEN 'BLOCKED' THEN NEW.status IN ('READY','FAILED','CANCELLED','RECOVERY_REQUIRED')
                WHEN 'VERIFYING' THEN NEW.status IN ('COMPLETED','FAILED','RECOVERY_REQUIRED')
                ELSE false
              END;
              IF NOT transition_allowed THEN
                RAISE EXCEPTION 'invalid work transition: % -> %', OLD.status, NEW.status;
              END IF;
              IF NOT EXISTS (
                SELECT 1 FROM work_events e
                 WHERE e.work_item_id = NEW.id
                   AND e.event_sequence = OLD.revision + 1
                   AND e.event_type = 'work.transition'
                   AND e.previous_state = OLD.status
                   AND e.new_state = NEW.status
                   AND e.attempt_id IS NOT DISTINCT FROM NEW.active_attempt_id
              ) THEN
                RAISE EXCEPTION 'state transition requires exact append-only event binding';
              END IF;
              IF NEW.status = 'RUNNING' AND NOT EXISTS (
                SELECT 1 FROM work_claims c
                 WHERE c.work_item_id = NEW.id
                   AND c.attempt_id = NEW.active_attempt_id
                   AND c.status = 'active'
                   AND c.expires_at > CURRENT_TIMESTAMP
                   AND c.fencing_token = NEW.active_fencing_token
              ) THEN
                RAISE EXCEPTION 'RUNNING requires an active claim and current fencing token';
              END IF;
              IF NEW.status = 'COMPLETED' AND NOT EXISTS (
                SELECT 1
                  FROM work_receipts close_receipt
                  JOIN work_receipts verify_receipt
                    ON close_receipt.parent_receipt_id = verify_receipt.id
                  JOIN work_receipts apply_receipt
                    ON verify_receipt.parent_receipt_id = apply_receipt.id
                 WHERE close_receipt.work_item_id = NEW.id
                   AND close_receipt.attempt_id = NEW.active_attempt_id
                   AND close_receipt.receipt_type = 'close'
                   AND close_receipt.is_terminal
                   AND close_receipt.exit_status = 0
                   AND verify_receipt.work_item_id = NEW.id
                   AND verify_receipt.attempt_id = NEW.active_attempt_id
                   AND verify_receipt.receipt_type = 'verify'
                   AND NOT verify_receipt.is_terminal
                   AND verify_receipt.exit_status = 0
                   AND apply_receipt.work_item_id = NEW.id
                   AND apply_receipt.attempt_id = NEW.active_attempt_id
                   AND apply_receipt.receipt_type = 'apply'
                   AND NOT apply_receipt.is_terminal
                   AND apply_receipt.exit_status = 0
                   AND apply_receipt.claim_id IS NOT NULL
                   AND close_receipt.after_revision = verify_receipt.after_revision
                   AND verify_receipt.after_revision = apply_receipt.after_revision
                   AND close_receipt.output_artifact_hash = verify_receipt.output_artifact_hash
                   AND close_receipt.acceptance_evidence = verify_receipt.acceptance_evidence
                   AND close_receipt.test_evidence_refs = verify_receipt.test_evidence_refs
                   AND jsonb_typeof(close_receipt.acceptance_evidence) = 'array'
                   AND jsonb_array_length(close_receipt.acceptance_evidence) > 0
              ) THEN
                RAISE EXCEPTION 'COMPLETED requires exact apply/verify/close receipt chain';
              END IF;
              NEW.revision := OLD.revision + 1;
              NEW.updated_at := CURRENT_TIMESTAMP;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_work_items_state_machine
            BEFORE UPDATE OF status ON work_items
            FOR EACH ROW EXECUTE FUNCTION cv3_enforce_work_item_transition()
            """
        )
    )


def downgrade() -> None:
    for table in ("knowledge_revisions", "knowledge_items"):
        op.execute(
            sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_active_head ON {table}")
        )
    op.execute(sa.text("DROP FUNCTION IF EXISTS cv3_enforce_knowledge_active_head()"))
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_knowledge_revisions_lifecycle "
            "ON knowledge_revisions"
        )
    )
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS cv3_enforce_knowledge_revision_lifecycle()")
    )
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_work_items_state_machine ON work_items")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS cv3_enforce_work_item_transition()"))
    for table in ("work_receipts", "work_events"):
        op.execute(
            sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
        )
    op.execute(sa.text("DROP FUNCTION IF EXISTS cv3_reject_append_only_mutation()"))
    op.drop_table("compiled_contexts")
    op.drop_table("context_manifests")
    op.drop_table("skill_registry")
    op.drop_table("model_registry")
    op.drop_table("provider_registry")
    op.drop_table("context_sources")
    op.drop_table("knowledge_conflicts")
    op.drop_constraint(
        "fk_knowledge_items_active_revision", "knowledge_items", type_="foreignkey"
    )
    op.drop_table("knowledge_revisions")
    op.drop_table("knowledge_items")
    op.drop_table("decision_records")
    op.drop_table("artifact_refs")
    op.drop_table("work_receipts")
    op.drop_table("work_approvals")
    op.drop_table("work_events")
    op.drop_table("work_claims")
    op.drop_constraint("fk_work_items_active_attempt", "work_items", type_="foreignkey")
    op.drop_table("work_attempts")
    op.drop_table("work_item_dependencies")
    op.drop_table("work_items")
