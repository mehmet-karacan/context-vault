"""Enforce version/profile/state/time invariants without deleting user data.

Revision ID: cv3_00000003
Revises: cv3_00000002

Naive timestamps are interpreted as UTC.  Check/FK constraints are added
NOT VALID after an explicit violation audit, then validated as a separate
observable step to reduce the initial lock window.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "cv3_00000003"
down_revision: str | None = "cv3_00000002"
branch_labels = None
depends_on = None

TIMESTAMP_COLUMNS = {
    "projects": ("created_at",),
    "documents": ("uploaded_at", "created_at", "updated_at", "deleted_at"),
    "document_versions": ("created_at", "activated_at"),
    "document_artifacts": ("created_at",),
    "ingestion_jobs": ("started_at", "finished_at", "created_at"),
    "ingestion_events": ("created_at",),
    "embedding_profiles": ("created_at",),
    "chunks": ("created_at",),
    "chunk_embeddings": ("created_at",),
    "conversations": ("created_at", "updated_at"),
    "messages": ("created_at",),
}


def _execute(sql: str) -> None:
    op.execute(sa.text(sql))


def _convert_timestamps(to_timezone: bool) -> None:
    for table, columns in TIMESTAMP_COLUMNS.items():
        for column in columns:
            if to_timezone:
                target = "TIMESTAMP WITH TIME ZONE"
                using = f"{column} AT TIME ZONE 'UTC'"
            else:
                target = "TIMESTAMP WITHOUT TIME ZONE"
                using = f"{column} AT TIME ZONE 'UTC'"
            _execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                f"TYPE {target} USING {using}"
            )


def _add_validated_check(table: str, name: str, expression: str) -> None:
    _execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM "{table}" WHERE NOT ({expression})) THEN
            RAISE EXCEPTION 'cv3_00000003 audit: existing rows violate {name}';
          END IF;
        END $$
        """
    )
    _execute(
        f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" CHECK ({expression}) NOT VALID'
    )
    _execute(f'ALTER TABLE "{table}" VALIDATE CONSTRAINT "{name}"')


def upgrade() -> None:
    _execute("SET LOCAL lock_timeout = '5s'")
    _execute("SET LOCAL statement_timeout = '10min'")

    # Semantics-preserving normalization observed in the recovered fixture.
    _execute("UPDATE documents SET status = 'indexed' WHERE status = 'completed'")
    # A legacy completed document with exactly one finished version has an
    # unambiguous activation candidate. Ambiguous histories abort in the audit
    # below instead of guessing which version is current.
    _execute(
        """
        WITH sole_ready_version AS (
          SELECT document_id, min(id::text)::uuid AS version_id
          FROM document_versions
          WHERE status IN ('ready', 'completed')
          GROUP BY document_id
          HAVING count(*) = 1
        )
        UPDATE documents d
        SET active_version_id = candidate.version_id
        FROM sole_ready_version candidate
        WHERE d.id = candidate.document_id
          AND d.status = 'indexed'
          AND d.active_version_id IS NULL
        """
    )
    _execute(
        """
        UPDATE document_versions v
        SET activated_at = COALESCE(v.activated_at, CURRENT_TIMESTAMP)
        FROM documents d
        WHERE d.active_version_id = v.id AND v.document_id = d.id
        """
    )
    _execute(
        "UPDATE documents SET created_at = COALESCE(created_at, uploaded_at, CURRENT_TIMESTAMP), "
        "updated_at = COALESCE(updated_at, created_at, uploaded_at, CURRENT_TIMESTAMP)"
    )
    _execute("UPDATE chunks SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)")
    _execute(
        "UPDATE conversations SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
    )
    # Preserve legacy vectors by copying them into the canonical profile table.
    # The legacy column remains intact for rollback, but runtime reads stop.
    _execute(
        """
        INSERT INTO chunk_embeddings
          (chunk_id, embedding_profile_id, embedding, created_at)
        SELECT c.id, ep.id, c.embedding, COALESCE(c.created_at, CURRENT_TIMESTAMP)
        FROM chunks c
        CROSS JOIN LATERAL (
          SELECT id FROM embedding_profiles WHERE is_active ORDER BY created_at DESC LIMIT 1
        ) ep
        WHERE c.embedding IS NOT NULL
        ON CONFLICT (chunk_id, embedding_profile_id) DO NOTHING
        """
    )

    # Abort before structural changes if provenance-safe backfill is impossible.
    _execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM chunks WHERE version_id IS NULL) THEN
            RAISE EXCEPTION 'cv3_00000003 audit: chunks.version_id contains NULL';
          END IF;
          IF EXISTS (
            SELECT 1 FROM chunks c JOIN document_versions v ON v.id = c.version_id
            WHERE c.document_id <> v.document_id
          ) THEN
            RAISE EXCEPTION 'cv3_00000003 audit: cross-document chunk/version';
          END IF;
          IF EXISTS (
            SELECT 1 FROM documents d JOIN document_versions v ON v.id = d.active_version_id
            WHERE v.document_id <> d.id OR v.status NOT IN ('ready', 'completed')
          ) THEN
            RAISE EXCEPTION 'cv3_00000003 audit: invalid active document version';
          END IF;
          IF EXISTS (
            SELECT 1 FROM documents
            WHERE status = 'indexed' AND active_version_id IS NULL
          ) THEN
            RAISE EXCEPTION 'cv3_00000003 audit: indexed document has no unambiguous active version';
          END IF;
          IF EXISTS (
            SELECT 1 FROM embedding_profiles
            WHERE dimension <> 1024 OR config_hash IS NULL OR length(config_hash) = 0
          ) THEN
            RAISE EXCEPTION 'cv3_00000003 audit: invalid embedding profile metadata';
          END IF;
          IF (SELECT count(*) FROM embedding_profiles WHERE is_active) <> 1 THEN
            RAISE EXCEPTION 'cv3_00000003 audit: expected exactly one active embedding profile';
          END IF;
        END $$;
        """
    )

    op.add_column(
        "projects",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "projects", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "conversations",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "data_classification",
            sa.String(),
            nullable=False,
            server_default="internal",
        ),
    )
    op.add_column("documents", sa.Column("error_code", sa.String(), nullable=True))
    op.add_column(
        "document_versions", sa.Column("error_code", sa.String(), nullable=True)
    )

    # Historical error rows did not have a structured code and could omit a
    # message. Preserve the row while making that missing provenance explicit.
    _execute(
        "UPDATE documents SET error_code = COALESCE(error_code, 'legacy_error'), "
        "error_message = COALESCE(error_message, 'Legacy error had no recorded message') "
        "WHERE status = 'error'"
    )
    _execute(
        "UPDATE document_versions SET error_code = COALESCE(error_code, 'legacy_error'), "
        "error_message = COALESCE(error_message, 'Legacy failure had no recorded message') "
        "WHERE status = 'failed'"
    )

    _convert_timestamps(to_timezone=True)
    op.alter_column("documents", "created_at", nullable=False)
    op.alter_column("documents", "updated_at", nullable=False)
    op.alter_column("chunks", "created_at", nullable=False)
    op.alter_column("conversations", "updated_at", nullable=False)
    op.alter_column("projects", "updated_at", nullable=False)

    op.create_unique_constraint(
        "uq_document_versions_document_id_id",
        "document_versions",
        ["document_id", "id"],
    )
    op.drop_constraint(
        "fk_documents_active_version_id_document_versions",
        "documents",
        type_="foreignkey",
    )
    _execute(
        """
        ALTER TABLE documents
        ADD CONSTRAINT fk_documents_active_version_same_document
        FOREIGN KEY (id, active_version_id)
        REFERENCES document_versions (document_id, id)
        NOT VALID
        """
    )
    _execute(
        "ALTER TABLE documents VALIDATE CONSTRAINT "
        "fk_documents_active_version_same_document"
    )
    op.drop_constraint("chunks_version_id_fkey", "chunks", type_="foreignkey")
    op.alter_column("chunks", "version_id", nullable=False)
    _execute(
        """
        ALTER TABLE chunks
        ADD CONSTRAINT fk_chunks_version_same_document
        FOREIGN KEY (document_id, version_id)
        REFERENCES document_versions (document_id, id)
        ON DELETE CASCADE
        NOT VALID
        """
    )
    _execute("ALTER TABLE chunks VALIDATE CONSTRAINT fk_chunks_version_same_document")

    _add_validated_check(
        "documents",
        "ck_documents_status",
        "status IN ('uploaded','processing','indexed','error','deleted')",
    )
    _add_validated_check(
        "documents",
        "ck_documents_source_type",
        "source_type IS NULL OR source_type IN "
        "('document','image','repository','directory','archive')",
    )
    _add_validated_check(
        "documents",
        "ck_documents_data_classification",
        "data_classification IN ('public','internal','confidential','restricted')",
    )
    _add_validated_check(
        "documents",
        "ck_documents_error_details",
        "status <> 'error' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
    )
    _add_validated_check(
        "document_versions",
        "ck_document_versions_status",
        "status IN ('pending','processing','completed','ready','failed','superseded')",
    )
    _add_validated_check(
        "document_versions",
        "ck_document_versions_activation",
        "activated_at IS NULL OR status IN ('completed','ready')",
    )
    _add_validated_check(
        "document_versions",
        "ck_document_versions_failed_error",
        "status <> 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
    )
    _add_validated_check(
        "ingestion_jobs",
        "ck_ingestion_jobs_status",
        "status IN ('queued','running','retrying','completed','failed','cancelled')",
    )
    _add_validated_check(
        "ingestion_jobs",
        "ck_ingestion_jobs_stage",
        "stage IS NULL OR stage IN "
        "('validating','storing','parsing','ocr','normalizing','chunking','embedding','indexing','activating')",
    )
    _add_validated_check(
        "ingestion_jobs",
        "ck_ingestion_jobs_progress",
        "progress IS NULL OR progress BETWEEN 0 AND 100",
    )
    _add_validated_check(
        "ingestion_jobs",
        "ck_ingestion_jobs_failed_error",
        "status <> 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
    )
    _add_validated_check(
        "ingestion_events",
        "ck_ingestion_events_stage",
        "stage IS NULL OR stage IN "
        "('validating','storing','parsing','ocr','normalizing','chunking','embedding','indexing','activating')",
    )
    _add_validated_check(
        "ingestion_events",
        "ck_ingestion_events_status",
        "status IS NULL OR status IN "
        "('queued','running','retrying','completed','failed','cancelled')",
    )
    _add_validated_check(
        "embedding_profiles",
        "ck_embedding_profiles_dimension",
        "dimension = 1024",
    )
    _add_validated_check(
        "embedding_profiles",
        "ck_embedding_profiles_config_hash",
        "length(config_hash) > 0",
    )
    _add_validated_check(
        "document_artifacts",
        "ck_document_artifacts_type",
        "artifact_type IN "
        "('original','normalized_json','normalized_md','page_image','thumbnail','ocr_json','scan_config')",
    )
    _add_validated_check(
        "messages",
        "ck_messages_role",
        "role IN ('system','user','assistant','tool')",
    )

    _execute(
        """
        CREATE FUNCTION cv3_guard_active_document_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.active_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM document_versions v
            WHERE v.id = NEW.active_version_id
              AND v.document_id = NEW.id
              AND v.status IN ('ready', 'completed')
          ) THEN
            RAISE EXCEPTION 'active version must be ready/completed and belong to document';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_cv3_guard_active_document_version
        BEFORE INSERT OR UPDATE OF active_version_id ON documents
        FOR EACH ROW EXECUTE FUNCTION cv3_guard_active_document_version();

        CREATE FUNCTION cv3_guard_active_version_status() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status NOT IN ('ready', 'completed') AND EXISTS (
            SELECT 1 FROM documents d WHERE d.active_version_id = NEW.id
          ) THEN
            RAISE EXCEPTION 'active document version status cannot leave ready/completed';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_cv3_guard_active_version_status
        BEFORE UPDATE OF status ON document_versions
        FOR EACH ROW EXECUTE FUNCTION cv3_guard_active_version_status();

        CREATE FUNCTION cv3_guard_terminal_ingestion_job() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status IN ('completed','failed','cancelled') AND NEW.status <> OLD.status THEN
            RAISE EXCEPTION 'terminal ingestion job status is immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_cv3_guard_terminal_ingestion_job
        BEFORE UPDATE OF status ON ingestion_jobs
        FOR EACH ROW EXECUTE FUNCTION cv3_guard_terminal_ingestion_job();

        CREATE FUNCTION cv3_guard_embedding_profile_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF (OLD.provider, OLD.model, OLD.dimension, OLD.distance_metric,
              OLD.query_prefix, OLD.passage_prefix, OLD.profile_version, OLD.config_hash)
             IS DISTINCT FROM
             (NEW.provider, NEW.model, NEW.dimension, NEW.distance_metric,
              NEW.query_prefix, NEW.passage_prefix, NEW.profile_version, NEW.config_hash)
          THEN
            RAISE EXCEPTION 'embedding profile identity is immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_cv3_guard_embedding_profile_identity
        BEFORE UPDATE ON embedding_profiles
        FOR EACH ROW EXECUTE FUNCTION cv3_guard_embedding_profile_identity();
        """
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_principal_id",
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
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(), nullable=False),
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
    )

    op.create_index(
        "ix_documents_project_status_active",
        "documents",
        ["project_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_document_versions_document_status",
        "document_versions",
        ["document_id", "status"],
    )
    op.create_index(
        "ix_chunks_document_version", "chunks", ["document_id", "version_id"]
    )
    op.create_index(
        "ix_ingestion_jobs_status_stage", "ingestion_jobs", ["status", "stage"]
    )
    op.create_index(
        "ix_conversations_project_active",
        "conversations",
        ["project_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_audit_events_actor_principal_id", "audit_events", ["actor_principal_id"]
    )
    op.create_index("ix_audit_events_workspace_id", "audit_events", ["workspace_id"])
    op.create_index("ix_audit_events_project_id", "audit_events", ["project_id"])
    op.create_index(
        "ix_audit_events_workspace_created",
        "audit_events",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    for table, index in (
        ("audit_events", "ix_audit_events_workspace_created"),
        ("audit_events", "ix_audit_events_project_id"),
        ("audit_events", "ix_audit_events_workspace_id"),
        ("audit_events", "ix_audit_events_actor_principal_id"),
    ):
        op.drop_index(index, table_name=table)
    op.drop_table("audit_events")

    op.drop_index("ix_conversations_project_active", table_name="conversations")
    op.drop_index("ix_ingestion_jobs_status_stage", table_name="ingestion_jobs")
    op.drop_index("ix_chunks_document_version", table_name="chunks")
    op.drop_index(
        "ix_document_versions_document_status", table_name="document_versions"
    )
    op.drop_index("ix_documents_project_status_active", table_name="documents")

    _execute(
        "DROP TRIGGER trg_cv3_guard_embedding_profile_identity ON embedding_profiles"
    )
    _execute("DROP FUNCTION cv3_guard_embedding_profile_identity()")
    _execute("DROP TRIGGER trg_cv3_guard_terminal_ingestion_job ON ingestion_jobs")
    _execute("DROP FUNCTION cv3_guard_terminal_ingestion_job()")
    _execute("DROP TRIGGER trg_cv3_guard_active_version_status ON document_versions")
    _execute("DROP FUNCTION cv3_guard_active_version_status()")
    _execute("DROP TRIGGER trg_cv3_guard_active_document_version ON documents")
    _execute("DROP FUNCTION cv3_guard_active_document_version()")

    for table, name in (
        ("messages", "ck_messages_role"),
        ("document_artifacts", "ck_document_artifacts_type"),
        ("embedding_profiles", "ck_embedding_profiles_config_hash"),
        ("embedding_profiles", "ck_embedding_profiles_dimension"),
        ("ingestion_jobs", "ck_ingestion_jobs_failed_error"),
        ("ingestion_jobs", "ck_ingestion_jobs_progress"),
        ("ingestion_jobs", "ck_ingestion_jobs_stage"),
        ("ingestion_jobs", "ck_ingestion_jobs_status"),
        ("ingestion_events", "ck_ingestion_events_status"),
        ("ingestion_events", "ck_ingestion_events_stage"),
        ("document_versions", "ck_document_versions_failed_error"),
        ("document_versions", "ck_document_versions_activation"),
        ("document_versions", "ck_document_versions_status"),
        ("documents", "ck_documents_error_details"),
        ("documents", "ck_documents_data_classification"),
        ("documents", "ck_documents_source_type"),
        ("documents", "ck_documents_status"),
    ):
        op.drop_constraint(name, table, type_="check")

    op.drop_constraint("fk_chunks_version_same_document", "chunks", type_="foreignkey")
    op.alter_column("chunks", "version_id", nullable=True)
    op.create_foreign_key(
        "chunks_version_id_fkey",
        "chunks",
        "document_versions",
        ["version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "fk_documents_active_version_same_document", "documents", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_documents_active_version_id_document_versions",
        "documents",
        "document_versions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "uq_document_versions_document_id_id",
        "document_versions",
        type_="unique",
    )

    _convert_timestamps(to_timezone=False)
    op.drop_column("documents", "data_classification")
    op.drop_column("document_versions", "error_code")
    op.drop_column("documents", "error_code")
    op.drop_column("conversations", "deleted_at")
    op.drop_column("projects", "deleted_at")
    op.drop_column("projects", "updated_at")
