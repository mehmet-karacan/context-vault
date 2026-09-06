"""Add the durable ingestion control plane without deleting existing data.

Revision ID: cv3_00000004
Revises: cv3_00000003
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "cv3_00000004"
down_revision: str | None = "cv3_00000003"
branch_labels = None
depends_on = None


def _execute(sql: str) -> None:
    op.execute(sa.text(sql))


def upgrade() -> None:
    _execute("SET LOCAL lock_timeout = '5s'")
    _execute("SET LOCAL statement_timeout = '10min'")

    op.drop_constraint("ck_ingestion_events_status", "ingestion_events", type_="check")
    _execute(
        """
        ALTER TABLE ingestion_events
        ADD CONSTRAINT ck_ingestion_events_status
        CHECK (status IS NULL OR status IN
          ('started','queued','running','retrying','completed','failed','cancelled'))
        NOT VALID;
        ALTER TABLE ingestion_events VALIDATE CONSTRAINT ck_ingestion_events_status
        """
    )
    op.drop_constraint(
        "ck_document_versions_activation", "document_versions", type_="check"
    )
    _execute(
        """
        ALTER TABLE document_versions
        ADD CONSTRAINT ck_document_versions_activation
        CHECK (activated_at IS NULL OR status IN
          ('completed','ready','superseded'))
        NOT VALID;
        ALTER TABLE document_versions
        VALIDATE CONSTRAINT ck_document_versions_activation
        """
    )

    op.create_table(
        "content_policy_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("classification", sa.String(), nullable=False),
        sa.Column("contains_credentials", sa.Boolean(), nullable=False),
        sa.Column("contains_private_key", sa.Boolean(), nullable=False),
        sa.Column("contains_pii", sa.Boolean(), nullable=False),
        sa.Column("permit_original_storage", sa.Boolean(), nullable=False),
        sa.Column("permit_normalized_storage", sa.Boolean(), nullable=False),
        sa.Column("permit_local_embedding", sa.Boolean(), nullable=False),
        sa.Column("permit_remote_embedding", sa.Boolean(), nullable=False),
        sa.Column("permit_local_generation", sa.Boolean(), nullable=False),
        sa.Column("permit_remote_generation", sa.Boolean(), nullable=False),
        sa.Column("redaction_required", sa.Boolean(), nullable=False),
        sa.Column("quarantine_reason", sa.String(), nullable=True),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "classification IN ('public','internal','confidential','restricted')",
            name="ck_content_policy_classification",
        ),
        sa.CheckConstraint(
            "NOT (contains_credentials OR contains_private_key) OR quarantine_reason IS NOT NULL",
            name="ck_content_policy_high_risk_quarantine",
        ),
    )
    op.create_index(
        "ix_content_policy_decisions_document_id",
        "content_policy_decisions",
        ["document_id"],
    )

    op.add_column(
        "document_versions",
        sa.Column("embedding_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column(
            "content_policy_decision_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    _execute(
        """
        UPDATE document_versions
        SET parser_profile = COALESCE(parser_profile, 'legacy-parser'),
            chunker_profile = COALESCE(chunker_profile, 'legacy-chunker'),
            embedding_profile_id = (
              SELECT id FROM embedding_profiles WHERE is_active LIMIT 1
            )
        """
    )
    _execute(
        """
        INSERT INTO content_policy_decisions (
          id, document_id, classification, contains_credentials,
          contains_private_key, contains_pii, permit_original_storage,
          permit_normalized_storage, permit_local_embedding,
          permit_remote_embedding, permit_local_generation,
          permit_remote_generation, redaction_required, quarantine_reason,
          policy_version, source_fingerprint, created_at
        )
        SELECT v.id, d.id, d.data_classification,
               false, false, false, true, true, true,
               d.data_classification NOT IN ('confidential','restricted'),
               true,
               d.data_classification NOT IN ('confidential','restricted'),
               false, NULL, 'legacy-v1',
               encode(digest(COALESCE(d.checksum, d.id::text), 'sha256'), 'hex'),
               COALESCE(v.created_at, CURRENT_TIMESTAMP)
        FROM document_versions v JOIN documents d ON d.id = v.document_id
        """
    )
    _execute(
        """
        UPDATE document_versions v
        SET content_policy_decision_id = p.id
        FROM content_policy_decisions p
        WHERE p.id = v.id
        """
    )
    _execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM document_versions
            WHERE parser_profile IS NULL OR chunker_profile IS NULL
               OR embedding_profile_id IS NULL
               OR content_policy_decision_id IS NULL
          ) THEN
            RAISE EXCEPTION 'cv3_00000004 audit: version profile/policy backfill incomplete';
          END IF;
          IF EXISTS (
            SELECT version_id, sequence_no FROM chunks
            WHERE sequence_no IS NOT NULL
            GROUP BY version_id, sequence_no HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION 'cv3_00000004 audit: duplicate chunk sequence';
          END IF;
          IF EXISTS (
            SELECT version_id, relative_path FROM source_files
            GROUP BY version_id, relative_path HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION 'cv3_00000004 audit: duplicate source file path';
          END IF;
          IF EXISTS (
            SELECT version_id, artifact_type, storage_key FROM document_artifacts
            GROUP BY version_id, artifact_type, storage_key HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION 'cv3_00000004 audit: duplicate artifact identity';
          END IF;
        END $$
        """
    )
    _execute("UPDATE chunks SET sequence_no = chunk_index WHERE sequence_no IS NULL")
    op.alter_column("document_versions", "parser_profile", nullable=False)
    op.alter_column("document_versions", "chunker_profile", nullable=False)
    op.alter_column("document_versions", "embedding_profile_id", nullable=False)
    op.alter_column("document_versions", "content_policy_decision_id", nullable=False)
    op.alter_column("chunks", "sequence_no", nullable=False)
    op.create_foreign_key(
        "fk_document_versions_embedding_profile",
        "document_versions",
        "embedding_profiles",
        ["embedding_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_document_versions_content_policy",
        "document_versions",
        "content_policy_decisions",
        ["content_policy_decision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_document_versions_embedding_profile_id",
        "document_versions",
        ["embedding_profile_id"],
    )
    op.create_index(
        "ix_document_versions_content_policy_decision_id",
        "document_versions",
        ["content_policy_decision_id"],
    )
    op.create_unique_constraint(
        "uq_chunks_version_sequence", "chunks", ["version_id", "sequence_no"]
    )
    op.create_unique_constraint(
        "uq_source_files_version_path", "source_files", ["version_id", "relative_path"]
    )
    op.create_unique_constraint(
        "uq_document_artifacts_version_type_key",
        "document_artifacts",
        ["version_id", "artifact_type", "storage_key"],
    )

    op.add_column(
        "ingestion_jobs", sa.Column("idempotency_key", sa.String(), nullable=True)
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("actor_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ingestion_jobs_actor_principal",
        "ingestion_jobs",
        "principals",
        ["actor_principal_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ingestion_jobs_workspace",
        "ingestion_jobs",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_ingestion_jobs_actor_principal_id",
        "ingestion_jobs",
        ["actor_principal_id"],
    )
    op.create_index(
        "ix_ingestion_jobs_workspace_id", "ingestion_jobs", ["workspace_id"]
    )
    op.add_column(
        "ingestion_jobs", sa.Column("lease_owner", sa.String(), nullable=True)
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    _execute("UPDATE ingestion_jobs SET idempotency_key = 'legacy:' || id::text")
    op.alter_column("ingestion_jobs", "idempotency_key", nullable=False)
    op.create_unique_constraint(
        "uq_ingestion_jobs_idempotency_key", "ingestion_jobs", ["idempotency_key"]
    )
    _execute(
        """
        ALTER TABLE ingestion_jobs
        ADD CONSTRAINT ck_ingestion_jobs_lease_owner
        CHECK (lease_expires_at IS NULL OR lease_owner IS NOT NULL) NOT VALID;
        ALTER TABLE ingestion_jobs VALIDATE CONSTRAINT ck_ingestion_jobs_lease_owner
        """
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_type", sa.String(), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column(
            "payload_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','dispatching','published','failed')",
            name="ck_outbox_events_status",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_outbox_events_failed_error",
        ),
    )
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index(
        "ix_outbox_events_pending",
        "outbox_events",
        ["available_at", "created_at"],
        postgresql_where=sa.text("status IN ('pending','failed')"),
    )

    op.create_table(
        "inbox_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("consumer", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "consumer", "idempotency_key", name="uq_inbox_consumer_key"
        ),
        sa.CheckConstraint(
            "status IN ('received','completed','failed')",
            name="ck_inbox_receipts_status",
        ),
    )
    op.create_index("ix_inbox_receipts_job_id", "inbox_receipts", ["job_id"])

    op.create_table(
        "ingestion_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(), nullable=False),
        sa.Column("celery_task_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="claimed"),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_ingestion_attempt_job_no"),
        sa.CheckConstraint(
            "status IN ('claimed','running','completed','failed','cancelled','stale')",
            name="ck_ingestion_attempts_status",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_ingestion_attempts_failed_error",
        ),
    )
    op.create_index("ix_ingestion_attempts_job_id", "ingestion_attempts", ["job_id"])
    op.create_index(
        "ix_ingestion_attempts_lease",
        "ingestion_attempts",
        ["status", "lease_expires_at"],
    )

    op.create_table(
        "ingestion_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
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
            "job_id", "attempt_id", "stage", "status", name="uq_ingestion_receipt_step"
        ),
        sa.CheckConstraint(
            "status IN ('started','completed','failed','retrying','cancelled')",
            name="ck_ingestion_receipts_status",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL",
            name="ck_ingestion_receipts_failed_error",
        ),
    )
    op.create_index("ix_ingestion_receipts_job_id", "ingestion_receipts", ["job_id"])
    op.create_index(
        "ix_ingestion_receipts_attempt_id", "ingestion_receipts", ["attempt_id"]
    )
    # Preserve pre-control-plane terminal outcomes as explicit legacy receipts.
    _execute(
        """
        INSERT INTO ingestion_attempts (
          id, job_id, attempt_no, worker_id, status, claimed_at,
          lease_expires_at, heartbeat_at, finished_at, error_code, error_message
        )
        SELECT j.id, j.id, j.attempt, 'legacy-worker',
               CASE j.status
                 WHEN 'completed' THEN 'completed'
                 WHEN 'cancelled' THEN 'cancelled'
                 ELSE 'failed'
               END,
               COALESCE(j.started_at, j.created_at),
               COALESCE(j.finished_at, j.created_at),
               COALESCE(j.finished_at, j.created_at),
               COALESCE(j.finished_at, j.created_at),
               CASE WHEN j.status = 'failed' THEN j.error_code ELSE NULL END,
               CASE WHEN j.status = 'failed' THEN j.error_message ELSE NULL END
        FROM ingestion_jobs j
        WHERE j.status IN ('completed','failed','cancelled')
        """
    )
    _execute(
        """
        INSERT INTO ingestion_receipts (
          id, job_id, attempt_id, stage, status, error_code,
          evidence_hash, metadata_json, created_at
        )
        SELECT gen_random_uuid(), j.id, j.id, COALESCE(j.stage, 'validating'),
               CASE j.status
                 WHEN 'completed' THEN 'completed'
                 WHEN 'cancelled' THEN 'cancelled'
                 ELSE 'failed'
               END,
               CASE WHEN j.status = 'failed' THEN j.error_code ELSE NULL END,
               encode(digest(j.id::text || ':' || j.status, 'sha256'), 'hex'),
               '{"provenance":"legacy-backfill"}'::jsonb,
               COALESCE(j.finished_at, j.created_at)
        FROM ingestion_jobs j
        WHERE j.status IN ('completed','failed','cancelled')
        """
    )

    op.create_table(
        "storage_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("storage_key", sa.Text(), nullable=False, unique=True),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "legal_hold",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("referenced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('staged','referenced','quarantined','deleted')",
            name="ck_storage_objects_status",
        ),
    )
    op.create_index("ix_storage_objects_version_id", "storage_objects", ["version_id"])
    op.create_index(
        "ix_storage_objects_artifact_id", "storage_objects", ["artifact_id"]
    )
    op.create_index(
        "ix_storage_objects_orphan_sweep",
        "storage_objects",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('staged','quarantined')"),
    )
    _execute(
        """
        INSERT INTO storage_objects (
          id, storage_key, checksum, size_bytes, status, version_id,
          artifact_id, created_at, referenced_at
        )
        SELECT gen_random_uuid(), a.storage_key,
               COALESCE(a.checksum, d.checksum), COALESCE(a.size_bytes, 0),
               'referenced', a.version_id, a.id,
               COALESCE(a.created_at, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP
        FROM document_artifacts a
        JOIN document_versions v ON v.id = a.version_id
        JOIN documents d ON d.id = v.document_id
        WHERE length(COALESCE(a.checksum, d.checksum)) = 64
        ON CONFLICT (storage_key) DO NOTHING
        """
    )

    op.create_table(
        "storage_gc_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "storage_object_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("storage_objects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('planned','deleted','skipped','failed')",
            name="ck_storage_gc_receipts_status",
        ),
    )
    op.create_index(
        "ix_storage_gc_receipts_storage_object_id",
        "storage_gc_receipts",
        ["storage_object_id"],
    )

    _execute(
        """
        CREATE FUNCTION cv3_guard_version_profiles_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF (OLD.parser_profile, OLD.chunker_profile, OLD.embedding_profile_id,
              OLD.content_policy_decision_id)
             IS DISTINCT FROM
             (NEW.parser_profile, NEW.chunker_profile, NEW.embedding_profile_id,
              NEW.content_policy_decision_id)
          THEN
            RAISE EXCEPTION 'document version profiles and policy are immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_cv3_guard_version_profiles_immutable
        BEFORE UPDATE ON document_versions
        FOR EACH ROW EXECUTE FUNCTION cv3_guard_version_profiles_immutable();
        """
    )


def downgrade() -> None:
    _execute(
        "DROP TRIGGER trg_cv3_guard_version_profiles_immutable ON document_versions"
    )
    _execute("DROP FUNCTION cv3_guard_version_profiles_immutable()")

    op.drop_index(
        "ix_storage_gc_receipts_storage_object_id", table_name="storage_gc_receipts"
    )
    op.drop_table("storage_gc_receipts")
    op.drop_index("ix_storage_objects_orphan_sweep", table_name="storage_objects")
    op.drop_index("ix_storage_objects_artifact_id", table_name="storage_objects")
    op.drop_index("ix_storage_objects_version_id", table_name="storage_objects")
    op.drop_table("storage_objects")
    op.drop_index("ix_ingestion_receipts_attempt_id", table_name="ingestion_receipts")
    op.drop_index("ix_ingestion_receipts_job_id", table_name="ingestion_receipts")
    op.drop_table("ingestion_receipts")
    op.drop_index("ix_ingestion_attempts_lease", table_name="ingestion_attempts")
    op.drop_index("ix_ingestion_attempts_job_id", table_name="ingestion_attempts")
    op.drop_table("ingestion_attempts")
    op.drop_index("ix_inbox_receipts_job_id", table_name="inbox_receipts")
    op.drop_table("inbox_receipts")
    op.drop_index("ix_outbox_events_pending", table_name="outbox_events")
    op.drop_index("ix_outbox_events_aggregate_id", table_name="outbox_events")
    op.drop_table("outbox_events")

    op.drop_constraint("ck_ingestion_jobs_lease_owner", "ingestion_jobs", type_="check")
    op.drop_constraint(
        "uq_ingestion_jobs_idempotency_key", "ingestion_jobs", type_="unique"
    )
    op.drop_index("ix_ingestion_jobs_workspace_id", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_actor_principal_id", table_name="ingestion_jobs")
    op.drop_constraint(
        "fk_ingestion_jobs_workspace", "ingestion_jobs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_ingestion_jobs_actor_principal", "ingestion_jobs", type_="foreignkey"
    )
    for column in (
        "cancel_requested_at",
        "heartbeat_at",
        "lease_expires_at",
        "lease_owner",
        "idempotency_key",
        "workspace_id",
        "actor_principal_id",
    ):
        op.drop_column("ingestion_jobs", column)

    op.drop_constraint(
        "uq_document_artifacts_version_type_key",
        "document_artifacts",
        type_="unique",
    )
    op.drop_constraint("uq_source_files_version_path", "source_files", type_="unique")
    op.drop_constraint("uq_chunks_version_sequence", "chunks", type_="unique")
    op.alter_column("chunks", "sequence_no", nullable=True)
    op.drop_index(
        "ix_document_versions_content_policy_decision_id",
        table_name="document_versions",
    )
    op.drop_index(
        "ix_document_versions_embedding_profile_id", table_name="document_versions"
    )
    op.drop_constraint(
        "fk_document_versions_content_policy", "document_versions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_document_versions_embedding_profile",
        "document_versions",
        type_="foreignkey",
    )
    op.alter_column("document_versions", "content_policy_decision_id", nullable=True)
    op.alter_column("document_versions", "embedding_profile_id", nullable=True)
    op.alter_column("document_versions", "chunker_profile", nullable=True)
    op.alter_column("document_versions", "parser_profile", nullable=True)
    op.drop_column("document_versions", "content_policy_decision_id")
    op.drop_column("document_versions", "embedding_profile_id")
    op.drop_index(
        "ix_content_policy_decisions_document_id",
        table_name="content_policy_decisions",
    )
    op.drop_table("content_policy_decisions")
    op.drop_constraint(
        "ck_document_versions_activation", "document_versions", type_="check"
    )
    _execute(
        """
        ALTER TABLE document_versions
        ADD CONSTRAINT ck_document_versions_activation
        CHECK (activated_at IS NULL OR status IN ('completed','ready'))
        NOT VALID;
        ALTER TABLE document_versions
        VALIDATE CONSTRAINT ck_document_versions_activation
        """
    )
    op.drop_constraint("ck_ingestion_events_status", "ingestion_events", type_="check")
    _execute(
        """
        ALTER TABLE ingestion_events
        ADD CONSTRAINT ck_ingestion_events_status
        CHECK (status IS NULL OR status IN
          ('queued','running','retrying','completed','failed','cancelled'))
        NOT VALID;
        ALTER TABLE ingestion_events VALIDATE CONSTRAINT ck_ingestion_events_status
        """
    )
