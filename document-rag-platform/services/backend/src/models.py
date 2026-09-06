import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import relationship

from .persistence import Base
from .domain.clock import utc_now

EMBEDDING_DIMENSION = 1024  # BAAI/bge-m3 output size


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_projects_workspace_name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    documents = relationship(
        "Document", back_populates="project", cascade="all, delete-orphan"
    )


class Principal(Base):
    """Human or service identity. API credentials never live on this row."""

    __tablename__ = "principals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject = Column(String, nullable=False, unique=True)
    display_name = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin','member','reader')",
            name="ck_workspace_memberships_role",
        ),
    )

    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    principal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key_prefix = Column(String(16), nullable=False, index=True)
    key_hash = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Content-free security/audit receipt tied to an authenticated actor."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_workspace_created", "workspace_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_principal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type = Column(String, nullable=False)
    metadata_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "active_version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="fk_documents_active_version_same_document",
            use_alter=True,
        ),
        CheckConstraint(
            "status IN ('uploaded','processing','indexed','error','deleted')",
            name="ck_documents_status",
        ),
        CheckConstraint(
            "source_type IS NULL OR source_type IN "
            "('document','image','repository','directory','archive')",
            name="ck_documents_source_type",
        ),
        CheckConstraint(
            "data_classification IN ('public','internal','confidential','restricted')",
            name="ck_documents_data_classification",
        ),
        CheckConstraint(
            "status <> 'error' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_documents_error_details",
        ),
        Index(
            "ix_documents_project_status_active",
            "project_id",
            "status",
            postgresql_where=sa_text("deleted_at IS NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="uploaded")
    error_code = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    # --- Aşama 2 additive fields (nullable; see AKTIF_GOREV.md Bölüm 8.1) --
    source_type = Column(
        String, nullable=True
    )  # document | image | repository | directory | archive
    origin_uri = Column(Text, nullable=True)
    mime_type = Column(String, nullable=True)
    checksum = Column(String, nullable=True)
    data_classification = Column(String, nullable=False, default="internal")
    active_version_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="documents")
    chunks = relationship(
        "Chunk",
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="Chunk.document_id",
    )
    versions = relationship(
        "DocumentVersion",
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="DocumentVersion.document_id",
    )
    active_version = relationship(
        "DocumentVersion", foreign_keys=[active_version_id], post_update=True
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "sequence_no", name="uq_chunks_version_sequence"
        ),
        ForeignKeyConstraint(
            ["document_id", "version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="fk_chunks_version_same_document",
            ondelete="CASCADE",
        ),
        Index("ix_chunks_document_version", "document_id", "version_id"),
        Index(
            "ix_chunks_search_vector_gin",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_chunks_symbol_name_trgm",
            sa_text("lower(symbol_name)"),
            postgresql_using="gin",
            postgresql_ops={"lower(symbol_name)": "gin_trgm_ops"},
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    # --- Aşama 2 additive fields (nullable; see AKTIF_GOREV.md Bölüm 8.7) --
    version_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    source_file_id = Column(
        UUID(as_uuid=True),
        ForeignKey("source_files.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sequence_no = Column(Integer, nullable=False)
    chunk_type = Column(String, nullable=True)
    heading_path = Column(JSONB, nullable=True)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    line_start = Column(Integer, nullable=True)
    line_end = Column(Integer, nullable=True)
    bbox = Column(JSONB, nullable=True)
    symbol_name = Column(String, nullable=True)
    symbol_type = Column(String, nullable=True)
    symbol_qualified_name = Column(Text, nullable=True)
    package_name = Column(Text, nullable=True)
    schema_name = Column(Text, nullable=True)
    table_name = Column(Text, nullable=True)
    column_name = Column(Text, nullable=True)
    token_count = Column(Integer, nullable=True)
    content_hash = Column(String, nullable=True)
    parent_chunk_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    metadata_json = Column(JSONB, nullable=True)
    search_vector = Column(TSVECTOR, nullable=True)
    search_profile = Column(String, nullable=False, default="simple-websearch-v1")
    identifiers = Column(ARRAY(Text), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    document = relationship(
        "Document", back_populates="chunks", foreign_keys=[document_id]
    )
    version = relationship(
        "DocumentVersion",
        back_populates="chunks",
        foreign_keys=[version_id],
        overlaps="chunks,document",
    )
    source_file = relationship("SourceFile", foreign_keys=[source_file_id])
    parent_chunk = relationship(
        "Chunk", remote_side=[id], foreign_keys=[parent_chunk_id]
    )
    chunk_embeddings = relationship(
        "ChunkEmbedding", back_populates="chunk", cascade="all, delete-orphan"
    )


class DocumentVersion(Base):
    """A single, immutable ingestion result for a document (Bölüm 8.2).

    Every new upload, repository commit, or re-index produces a new
    version. ``documents.active_version_id`` only moves to a version once
    it is fully ready.
    """

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_no",
            name="uq_document_versions_document_id_version_no",
        ),
        UniqueConstraint(
            "document_id",
            "id",
            name="uq_document_versions_document_id_id",
        ),
        CheckConstraint(
            "status IN ('pending','processing','completed','ready','failed','superseded')",
            name="ck_document_versions_status",
        ),
        CheckConstraint(
            "activated_at IS NULL OR status IN ('completed','ready','superseded')",
            name="ck_document_versions_activation",
        ),
        CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_document_versions_failed_error",
        ),
        Index("ix_document_versions_document_status", "document_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no = Column(Integer, nullable=False)
    source_revision = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    parser_profile = Column(String, nullable=False, default="context-vault-parser-v1")
    chunker_profile = Column(String, nullable=False, default="context-vault-chunker-v4")
    embedding_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("embedding_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    content_policy_decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("content_policy_decisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    storage_key = Column(Text, nullable=True)
    normalized_artifact_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "document_artifacts.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_document_versions_normalized_artifact_id_document_artifacts",
        ),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)

    document = relationship(
        "Document", back_populates="versions", foreign_keys=[document_id]
    )
    source_files = relationship(
        "SourceFile", back_populates="version", cascade="all, delete-orphan"
    )
    artifacts = relationship(
        "DocumentArtifact",
        back_populates="version",
        cascade="all, delete-orphan",
        foreign_keys="DocumentArtifact.version_id",
    )
    normalized_artifact = relationship(
        "DocumentArtifact", foreign_keys=[normalized_artifact_id], post_update=True
    )
    ingestion_jobs = relationship(
        "IngestionJob", back_populates="version", cascade="all, delete-orphan"
    )
    chunks = relationship("Chunk", back_populates="version", overlaps="chunks,document")


class SourceFile(Base):
    """A single file inside a repository/directory/archive version (Bölüm 8.3)."""

    __tablename__ = "source_files"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "relative_path", name="uq_source_files_version_path"
        ),
        Index(
            "ix_source_files_relative_path_trgm",
            sa_text("lower(relative_path)"),
            postgresql_using="gin",
            postgresql_ops={"lower(relative_path)": "gin_trgm_ops"},
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relative_path = Column(Text, nullable=False)
    language = Column(String, nullable=True)
    mime_type = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    content_hash = Column(String, nullable=True)
    is_binary = Column(Boolean, nullable=False, default=False)
    is_generated = Column(Boolean, nullable=False, default=False)
    is_ignored = Column(Boolean, nullable=False, default=False)
    metadata_json = Column(JSONB, nullable=True)

    version = relationship("DocumentVersion", back_populates="source_files")


class DocumentArtifact(Base):
    """An immutable object-storage artifact produced for a version (Bölüm 8.4).

    artifact_type: original | normalized_json | normalized_md | page_image |
    thumbnail | ocr_json
    """

    __tablename__ = "document_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "artifact_type",
            "storage_key",
            name="uq_document_artifacts_version_type_key",
        ),
        CheckConstraint(
            "artifact_type IN "
            "('original','normalized_json','normalized_md','page_image','thumbnail','ocr_json','scan_config')",
            name="ck_document_artifacts_type",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_type = Column(String, nullable=False)
    storage_key = Column(Text, nullable=False)
    checksum = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    version = relationship(
        "DocumentVersion", back_populates="artifacts", foreign_keys=[version_id]
    )


class IngestionJob(Base):
    """Async ingestion job/worker state for a version (Bölüm 8.5).

    status: queued | running | completed | failed | cancelled
    stage: validating | storing | parsing | ocr | normalizing | chunking |
           embedding | indexing | activating
    """

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','retrying','completed','failed','cancelled')",
            name="ck_ingestion_jobs_status",
        ),
        CheckConstraint(
            "stage IS NULL OR stage IN "
            "('validating','storing','parsing','ocr','normalizing','chunking','embedding','indexing','activating')",
            name="ck_ingestion_jobs_stage",
        ),
        CheckConstraint(
            "progress IS NULL OR progress BETWEEN 0 AND 100",
            name="ck_ingestion_jobs_progress",
        ),
        CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_ingestion_jobs_failed_error",
        ),
        CheckConstraint(
            "lease_expires_at IS NULL OR lease_owner IS NOT NULL",
            name="ck_ingestion_jobs_lease_owner",
        ),
        Index("ix_ingestion_jobs_status_stage", "status", "stage"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key = Column(String, nullable=False, unique=True)
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_principal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    status = Column(String, nullable=False, default="queued")
    stage = Column(String, nullable=True)
    progress = Column(Integer, nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    lease_owner = Column(String, nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    version = relationship("DocumentVersion", back_populates="ingestion_jobs")
    events = relationship(
        "IngestionEvent", back_populates="job", cascade="all, delete-orphan"
    )


class IngestionEvent(Base):
    """Durable progress/error event for an ingestion job (Bölüm 8.6).

    Redis is only the live-delivery layer; the durable record lives here.
    """

    __tablename__ = "ingestion_events"
    __table_args__ = (
        CheckConstraint(
            "stage IS NULL OR stage IN "
            "('validating','storing','parsing','ocr','normalizing','chunking','embedding','indexing','activating')",
            name="ck_ingestion_events_stage",
        ),
        CheckConstraint(
            "status IS NULL OR status IN "
            "('started','queued','running','retrying','completed','failed','cancelled')",
            name="ck_ingestion_events_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String, nullable=True)
    status = Column(String, nullable=True)
    message = Column(Text, nullable=True)
    payload_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    job = relationship("IngestionJob", back_populates="events")


class ContentPolicyDecisionRecord(Base):
    """Immutable, content-free policy decision made before processing."""

    __tablename__ = "content_policy_decisions"
    __table_args__ = (
        CheckConstraint(
            "classification IN ('public','internal','confidential','restricted')",
            name="ck_content_policy_classification",
        ),
        CheckConstraint(
            "NOT (contains_credentials OR contains_private_key) OR quarantine_reason IS NOT NULL",
            name="ck_content_policy_high_risk_quarantine",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    classification = Column(String, nullable=False)
    contains_credentials = Column(Boolean, nullable=False, default=False)
    contains_private_key = Column(Boolean, nullable=False, default=False)
    contains_pii = Column(Boolean, nullable=False, default=False)
    permit_original_storage = Column(Boolean, nullable=False)
    permit_normalized_storage = Column(Boolean, nullable=False)
    permit_local_embedding = Column(Boolean, nullable=False)
    permit_remote_embedding = Column(Boolean, nullable=False)
    permit_local_generation = Column(Boolean, nullable=False)
    permit_remote_generation = Column(Boolean, nullable=False)
    redaction_required = Column(Boolean, nullable=False)
    quarantine_reason = Column(String, nullable=True)
    policy_version = Column(String, nullable=False)
    source_fingerprint = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class OutboxEvent(Base):
    """Durable queue intent committed with the document/version/job."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','dispatching','published','failed')",
            name="ck_outbox_events_status",
        ),
        CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_outbox_events_failed_error",
        ),
        Index(
            "ix_outbox_events_pending",
            "available_at",
            "created_at",
            postgresql_where=sa_text("status IN ('pending','failed')"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_type = Column(String, nullable=False)
    aggregate_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    event_type = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False, unique=True)
    payload_json = Column(JSONB, nullable=False, default=dict)
    status = Column(String, nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class InboxReceipt(Base):
    """Consumer idempotency record for one delivered outbox message."""

    __tablename__ = "inbox_receipts"
    __table_args__ = (
        UniqueConstraint("consumer", "idempotency_key", name="uq_inbox_consumer_key"),
        CheckConstraint(
            "status IN ('received','completed','failed')",
            name="ck_inbox_receipts_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consumer = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String, nullable=False, default="received")
    received_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class IngestionAttempt(Base):
    __tablename__ = "ingestion_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_no", name="uq_ingestion_attempt_job_no"),
        CheckConstraint(
            "status IN ('claimed','running','completed','failed','cancelled','stale')",
            name="ck_ingestion_attempts_status",
        ),
        CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_ingestion_attempts_failed_error",
        ),
        Index("ix_ingestion_attempts_lease", "status", "lease_expires_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_no = Column(Integer, nullable=False)
    worker_id = Column(String, nullable=False)
    celery_task_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="claimed")
    claimed_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    lease_expires_at = Column(DateTime(timezone=True), nullable=False)
    heartbeat_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)


class IngestionReceipt(Base):
    __tablename__ = "ingestion_receipts"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "attempt_id", "stage", "status", name="uq_ingestion_receipt_step"
        ),
        CheckConstraint(
            "status IN ('started','completed','failed','retrying','cancelled')",
            name="ck_ingestion_receipts_status",
        ),
        CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL",
            name="ck_ingestion_receipts_failed_error",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String, nullable=False)
    status = Column(String, nullable=False)
    error_code = Column(String, nullable=True)
    evidence_hash = Column(String(64), nullable=True)
    metadata_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class StorageObject(Base):
    """Registry entry used by staging/final orphan reconciliation and GC."""

    __tablename__ = "storage_objects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('staged','referenced','quarantined','deleted')",
            name="ck_storage_objects_status",
        ),
        Index(
            "ix_storage_objects_orphan_sweep",
            "status",
            "created_at",
            postgresql_where=sa_text("status IN ('staged','quarantined')"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_key = Column(Text, nullable=False, unique=True)
    checksum = Column(String(64), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    status = Column(String, nullable=False)
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    artifact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_artifacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    retention_until = Column(DateTime(timezone=True), nullable=True)
    legal_hold = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    referenced_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class StorageGcReceipt(Base):
    __tablename__ = "storage_gc_receipts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planned','deleted','skipped','failed')",
            name="ck_storage_gc_receipts_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_object_id = Column(
        UUID(as_uuid=True),
        ForeignKey("storage_objects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    key_hash = Column(String(64), nullable=False)
    action = Column(String, nullable=False)
    status = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    dry_run = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class EmbeddingProfile(Base):
    """A versioned dense-embedding configuration (Bölüm 8.8).

    Only one profile is the initial active 1024-dim profile; differently
    sized embeddings are never mixed into the canonical profile-bound
    ``chunk_embeddings.embedding`` Vector(1024) column.
    """

    __tablename__ = "embedding_profiles"
    __table_args__ = (
        Index(
            "uq_embedding_profiles_one_active",
            "is_active",
            unique=True,
            postgresql_where=sa_text("is_active"),
        ),
        CheckConstraint("dimension = 1024", name="ck_embedding_profiles_dimension"),
        CheckConstraint(
            "length(config_hash) > 0", name="ck_embedding_profiles_config_hash"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)
    dimension = Column(Integer, nullable=False)
    distance_metric = Column(String, nullable=False)
    query_prefix = Column(Text, nullable=True)
    passage_prefix = Column(Text, nullable=True)
    profile_version = Column(Integer, nullable=False, default=1)
    config_hash = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    chunk_embeddings = relationship(
        "ChunkEmbedding",
        back_populates="embedding_profile",
        cascade="all, delete-orphan",
    )


class ChunkEmbedding(Base):
    """A chunk's embedding under a specific profile (Bölüm 8.9).

    UNIQUE(chunk_id, embedding_profile_id) is enforced via the composite
    primary key below.
    """

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        Index(
            "ix_chunk_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    chunk_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("embedding_profiles.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    embedding = Column(Vector(EMBEDDING_DIMENSION), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    chunk = relationship("Chunk", back_populates="chunk_embeddings")
    embedding_profile = relationship(
        "EmbeddingProfile", back_populates="chunk_embeddings"
    )


class Conversation(Base):
    """A chat conversation scoped to a project (Bölüm 8.10)."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index(
            "ix_conversations_project_active",
            "project_id",
            postgresql_where=sa_text("deleted_at IS NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    principal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    title = Column(String, nullable=True)
    title_status = Column(String, nullable=False, default="unset")
    title_model = Column(String, nullable=True)
    title_prompt_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    messages = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )


class RetrievalRun(Base):
    """Content-free durable provenance for one scoped retrieval request."""

    __tablename__ = "retrieval_runs"
    __table_args__ = (
        CheckConstraint(
            "candidate_count >= 0 AND selected_count >= 0",
            name="ck_retrieval_runs_counts",
        ),
        Index("ix_retrieval_runs_project_created", "project_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    embedding_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("embedding_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    query_hash = Column(String(64), nullable=False)
    retriever_versions = Column(JSONB, nullable=False, default=dict)
    config_json = Column(JSONB, nullable=False, default=dict)
    candidate_count = Column(Integer, nullable=False, default=0)
    selected_count = Column(Integer, nullable=False, default=0)
    stage_latency_ms = Column(JSONB, nullable=False, default=dict)
    no_answer_reason = Column(String, nullable=True)
    fallback_reason = Column(String, nullable=True)
    bundle_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class Message(Base):
    """A single turn in a conversation (Bölüm 8.10)."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('system','user','assistant','tool')",
            name="ck_messages_role",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    model = Column(String, nullable=True)
    answerable = Column(Boolean, nullable=True)
    no_answer_reason = Column(String, nullable=True)
    prompt_template_version = Column(String, nullable=True)
    prompt_hash = Column(String(64), nullable=True)
    generation_config = Column(JSONB, nullable=False, default=dict)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    conversation = relationship("Conversation", back_populates="messages")
    citations = relationship(
        "MessageCitation", back_populates="message", cascade="all, delete-orphan"
    )
    claims = relationship(
        "MessageClaim", back_populates="message", cascade="all, delete-orphan"
    )


class MessageClaim(Base):
    """One validated answer claim; citation links live in claim_citations."""

    __tablename__ = "message_claims"
    __table_args__ = (
        UniqueConstraint("message_id", "claim_index", name="uq_message_claim_index"),
        CheckConstraint("claim_index > 0", name="ck_message_claim_index_positive"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    claim_index = Column(Integer, nullable=False)
    claim_text = Column(Text, nullable=False)
    claim_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    message = relationship("Message", back_populates="claims")
    citation_links = relationship(
        "ClaimCitation", back_populates="claim", cascade="all, delete-orphan"
    )


class MessageCitation(Base):
    """A single evidence citation backing a generated message (Bölüm 8.10)."""

    __tablename__ = "message_citations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    retrieval_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("retrieval_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_id = Column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True
    )
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_file_id = Column(
        UUID(as_uuid=True),
        ForeignKey("source_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    embedding_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("embedding_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    rank = Column(Integer, nullable=True)
    usage_order = Column(Integer, nullable=False, default=1)
    retrieval_score = Column(Float, nullable=True)
    fusion_score = Column(Float, nullable=True)
    reranker_score = Column(Float, nullable=True)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    line_start = Column(Integer, nullable=True)
    line_end = Column(Integer, nullable=True)
    citation_label = Column(String, nullable=True)
    locator_json = Column(JSONB, nullable=False, default=dict)
    evidence_snapshot_encrypted = Column(LargeBinary, nullable=True)
    evidence_hash = Column(String(64), nullable=True)
    content_hash = Column(String(64), nullable=True)
    model = Column(String, nullable=True)
    prompt_template_version = Column(String, nullable=True)
    prompt_hash = Column(String(64), nullable=True)
    generation_config = Column(JSONB, nullable=False, default=dict)
    validation_result = Column(String, nullable=False, default="valid")
    evidence_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    message = relationship("Message", back_populates="citations")
    claim_links = relationship(
        "ClaimCitation", back_populates="citation", cascade="all, delete-orphan"
    )


class ClaimCitation(Base):
    """Many-to-many ordered relation between claims and actually used sources."""

    __tablename__ = "claim_citations"
    __table_args__ = (
        CheckConstraint("source_order > 0", name="ck_claim_citation_order_positive"),
    )

    claim_id = Column(
        UUID(as_uuid=True),
        ForeignKey("message_claims.id", ondelete="CASCADE"),
        primary_key=True,
    )
    citation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("message_citations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_order = Column(Integer, nullable=False)

    claim = relationship("MessageClaim", back_populates="citation_links")
    citation = relationship("MessageCitation", back_populates="claim_links")


class WorkItem(Base):
    """Authoritative unit of mutable work in the PostgreSQL Work Graph."""

    __tablename__ = "work_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','READY','CLAIMED','RUNNING','BLOCKED','VERIFYING',"
            "'COMPLETED','FAILED','CANCELLED','RECOVERY_REQUIRED')",
            name="ck_work_items_status",
        ),
        CheckConstraint(
            "risk_class IN ('low','medium','high','critical')",
            name="ck_work_items_risk_class",
        ),
        CheckConstraint("revision >= 0", name="ck_work_items_revision"),
        CheckConstraint(
            "next_fencing_token >= 0", name="ck_work_items_next_fencing_token"
        ),
        Index("ix_work_items_project_status", "project_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    title = Column(Text, nullable=False)
    objective = Column(Text, nullable=False)
    scope_json = Column(JSONB, nullable=False, default=dict)
    exclusions_json = Column(JSONB, nullable=False, default=list)
    priority = Column(Integer, nullable=False, default=0)
    risk_class = Column(String, nullable=False, default="low")
    required_approvals = Column(JSONB, nullable=False, default=list)
    expected_revision = Column(String, nullable=False)
    expected_baseline_hash = Column(String(64), nullable=True)
    status = Column(String, nullable=False, default="DRAFT")
    created_by_principal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_principal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    parent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    acceptance_criteria = Column(JSONB, nullable=False, default=list)
    evidence_requirements = Column(JSONB, nullable=False, default=list)
    revision = Column(BigInteger, nullable=False, default=0)
    next_fencing_token = Column(BigInteger, nullable=False, default=0)
    active_fencing_token = Column(BigInteger, nullable=True)
    active_attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "work_attempts.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_work_items_active_attempt",
        ),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class WorkItemDependency(Base):
    __tablename__ = "work_item_dependencies"
    __table_args__ = (
        CheckConstraint(
            "work_item_id <> depends_on_work_item_id",
            name="ck_work_item_dependencies_not_self",
        ),
    )

    work_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    depends_on_work_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    dependency_type = Column(String, nullable=False, default="blocks")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class WorkAttempt(Base):
    __tablename__ = "work_attempts"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id", "attempt_no", name="uq_work_attempt_item_number"
        ),
        UniqueConstraint(
            "work_item_id", "idempotency_key", name="uq_work_attempt_item_idempotency"
        ),
        UniqueConstraint(
            "work_item_id",
            "effect_idempotency_key",
            name="uq_work_attempt_effect_idempotency",
        ),
        CheckConstraint("attempt_no > 0", name="ck_work_attempt_number_positive"),
        CheckConstraint(
            "status IN ('PREPARED','CLAIMED','RUNNING','BLOCKED','VERIFYING',"
            "'COMPLETED','FAILED','CANCELLED','RECOVERY_REQUIRED')",
            name="ck_work_attempts_status",
        ),
        CheckConstraint(
            "effect_started_at IS NULL OR effect_idempotency_key IS NOT NULL",
            name="ck_work_attempt_effect_intent",
        ),
        CheckConstraint(
            "rollback_started_at IS NULL OR "
            "(rollback_idempotency_key IS NOT NULL AND rollback_request_hash IS NOT NULL)",
            name="ck_work_attempt_rollback_intent",
        ),
        UniqueConstraint(
            "work_item_id",
            "rollback_idempotency_key",
            name="uq_work_attempt_rollback_idempotency",
        ),
        Index("ix_work_attempts_item_status", "work_item_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_no = Column(Integer, nullable=False)
    executor_id = Column(String, nullable=False)
    adapter_id = Column(String, nullable=True)
    model_id = Column(String, nullable=True)
    provider_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="PREPARED")
    started_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    input_context_manifest_hash = Column(String(64), nullable=True)
    expected_revision = Column(String, nullable=False)
    drift_token = Column(String(64), nullable=False)
    idempotency_key = Column(String, nullable=False)
    claim_request_hash = Column(String(64), nullable=False)
    effect_idempotency_key = Column(String, nullable=True)
    effect_started_at = Column(DateTime(timezone=True), nullable=True)
    rollback_idempotency_key = Column(String, nullable=True)
    rollback_request_hash = Column(String(64), nullable=True)
    rollback_started_at = Column(DateTime(timezone=True), nullable=True)
    outcome_json = Column(JSONB, nullable=False, default=dict)
    terminal_reason = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class WorkClaim(Base):
    __tablename__ = "work_claims"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id", "fencing_token", name="uq_work_claim_item_fencing"
        ),
        UniqueConstraint("attempt_id", name="uq_work_claim_attempt"),
        CheckConstraint("fencing_token > 0", name="ck_work_claim_fencing_positive"),
        CheckConstraint(
            "status IN ('active','released','reconciled')",
            name="ck_work_claims_status",
        ),
        CheckConstraint(
            "expires_at > acquired_at", name="ck_work_claim_expiry_after_acquired"
        ),
        CheckConstraint(
            "heartbeat_at >= acquired_at AND heartbeat_at <= expires_at",
            name="ck_work_claim_heartbeat_window",
        ),
        Index("ix_work_claims_expiry", "status", "expires_at"),
        Index(
            "uq_work_claim_one_active",
            "work_item_id",
            unique=True,
            postgresql_where=sa_text("status = 'active'"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_scope = Column(JSONB, nullable=False)
    scope_hash = Column(String(64), nullable=False)
    claimant_id = Column(String, nullable=False)
    acquired_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    heartbeat_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    fencing_token = Column(BigInteger, nullable=False)
    status = Column(String, nullable=False, default="active")
    released_reason = Column(String, nullable=True)
    reconciled_reason = Column(String, nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)


class WorkEvent(Base):
    """Append-only state transition and observation ledger."""

    __tablename__ = "work_events"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id", "event_sequence", name="uq_work_event_item_sequence"
        ),
        CheckConstraint("event_sequence > 0", name="ck_work_event_sequence_positive"),
        CheckConstraint(
            "actor_type IN ('human','policy','service','cli','model')",
            name="ck_work_event_actor_type",
        ),
        Index("ix_work_events_item_created", "work_item_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_sequence = Column(BigInteger, nullable=False)
    event_type = Column(String, nullable=False)
    actor_type = Column(String, nullable=False)
    actor_id = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    correlation_id = Column(UUID(as_uuid=True), nullable=False)
    causation_id = Column(UUID(as_uuid=True), nullable=True)
    previous_state = Column(String, nullable=True)
    new_state = Column(String, nullable=True)
    payload_schema_version = Column(String, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    payload_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class WorkApproval(Base):
    __tablename__ = "work_approvals"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('human','policy')", name="ck_work_approval_actor_type"
        ),
        CheckConstraint(
            "decision IN ('approved','rejected','revoked')",
            name="ck_work_approval_decision",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > granted_at",
            name="ck_work_approval_expiry",
        ),
        UniqueConstraint(
            "work_item_id",
            "approval_type",
            "scope_hash",
            "actor_id",
            name="uq_work_approval_actor_scope",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )
    approval_type = Column(String, nullable=False)
    scope_hash = Column(String(64), nullable=False)
    decision = Column(String, nullable=False)
    actor_type = Column(String, nullable=False)
    actor_id = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    evidence_refs = Column(JSONB, nullable=False, default=list)
    granted_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class WorkReceipt(Base):
    __tablename__ = "work_receipts"
    __table_args__ = (
        CheckConstraint(
            "receipt_type IN ('prepare','apply','verify','close','rollback')",
            name="ck_work_receipts_type",
        ),
        CheckConstraint(
            "length(request_hash) = 64", name="ck_work_receipt_request_hash"
        ),
        CheckConstraint(
            "(receipt_type = 'apply' AND parent_receipt_id IS NULL) OR "
            "(receipt_type IN ('verify','close','rollback') AND parent_receipt_id IS NOT NULL) OR "
            "receipt_type = 'prepare'",
            name="ck_work_receipt_parent_chain",
        ),
        CheckConstraint(
            "is_terminal = (receipt_type = 'close')",
            name="ck_work_receipt_terminal_close",
        ),
        CheckConstraint(
            "parent_receipt_id IS NULL OR parent_receipt_id <> id",
            name="ck_work_receipt_parent_not_self",
        ),
        CheckConstraint("ended_at >= started_at", name="ck_work_receipt_time_order"),
        CheckConstraint(
            "signer_type IN ('human','policy','service','cli')",
            name="ck_work_receipt_signer_type",
        ),
        UniqueConstraint(
            "work_item_id",
            "receipt_type",
            "idempotency_key",
            name="uq_work_receipt_item_type_idempotency",
        ),
        Index(
            "uq_work_receipt_one_terminal",
            "work_item_id",
            unique=True,
            postgresql_where=sa_text("is_terminal"),
        ),
        Index("ix_work_receipts_attempt", "attempt_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    claim_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_claims.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_receipt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_receipts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    receipt_type = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    request_hash = Column(String(64), nullable=False)
    command = Column(Text, nullable=False)
    tool = Column(String, nullable=False)
    action = Column(String, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=False)
    exit_status = Column(Integer, nullable=False)
    input_artifact_hash = Column(String(64), nullable=True)
    output_artifact_hash = Column(String(64), nullable=True)
    before_revision = Column(String, nullable=False)
    after_revision = Column(String, nullable=False)
    test_evidence_refs = Column(JSONB, nullable=False, default=list)
    acceptance_evidence = Column(JSONB, nullable=False, default=list)
    rollback_result = Column(JSONB, nullable=True)
    signer_type = Column(String, nullable=False)
    signer_id = Column(String, nullable=False)
    attestation = Column(JSONB, nullable=False, default=dict)
    is_terminal = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ArtifactRef(Base):
    __tablename__ = "artifact_refs"
    __table_args__ = (
        CheckConstraint(
            "classification IN ('public','internal','confidential','restricted')",
            name="ck_artifact_ref_classification",
        ),
        UniqueConstraint("uri", "sha256", name="uq_artifact_ref_uri_hash"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )
    receipt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_receipts.id", ondelete="SET NULL"),
        nullable=True,
    )
    artifact_type = Column(String, nullable=False)
    uri = Column(Text, nullable=False)
    sha256 = Column(String(64), nullable=False)
    classification = Column(String, nullable=False, default="internal")
    metadata_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class DecisionRecord(Base):
    __tablename__ = "decision_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "decision_key", name="uq_decision_record_workspace_key"
        ),
        CheckConstraint(
            "status IN ('PROPOSED','APPROVED','SUPERSEDED','REVOKED')",
            name="ck_decision_record_status",
        ),
        CheckConstraint(
            "approved_by_type IS NULL OR approved_by_type IN ('human','policy')",
            name="ck_decision_record_approver",
        ),
        CheckConstraint(
            "status <> 'APPROVED' OR approved_by_type IN ('human','policy')",
            name="ck_decision_record_approved_actor",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    decision_key = Column(String, nullable=False)
    title = Column(Text, nullable=False)
    statement = Column(Text, nullable=False)
    rationale = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="PROPOSED")
    source_artifact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("artifact_refs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supersedes_decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("decision_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    owner_principal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_by_type = Column(String, nullable=True)
    approved_by_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        Index(
            "uq_knowledge_item_scope_key",
            "workspace_id",
            sa_text(
                "coalesce(project_id, '00000000-0000-0000-0000-000000000000'::uuid)"
            ),
            "stable_key",
            unique=True,
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    stable_key = Column(Text, nullable=False)
    scope = Column(Text, nullable=False)
    classification = Column(String, nullable=False)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    active_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "knowledge_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_knowledge_items_active_revision",
        ),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class KnowledgeRevision(Base):
    __tablename__ = "knowledge_revisions"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_item_id", "version", name="uq_knowledge_revision_version"
        ),
        UniqueConstraint(
            "knowledge_item_id", "content_hash", name="uq_knowledge_revision_hash"
        ),
        CheckConstraint("version > 0", name="ck_knowledge_revision_version"),
        CheckConstraint(
            "status IN ('PROPOSED','REVIEWED','APPROVED','SUPERSEDED','REVOKED','EXPIRED')",
            name="ck_knowledge_revision_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_knowledge_revision_confidence",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_knowledge_revision_validity",
        ),
        CheckConstraint(
            "proposed_by_type IN ('human','policy','model','service')",
            name="ck_knowledge_revision_proposer",
        ),
        CheckConstraint(
            "reviewed_by_type IS NULL OR reviewed_by_type IN ('human','policy')",
            name="ck_knowledge_revision_reviewer",
        ),
        CheckConstraint(
            "approved_by_type IS NULL OR approved_by_type IN ('human','policy')",
            name="ck_knowledge_revision_approver",
        ),
        CheckConstraint(
            "(reviewed_by_type IS NULL) = (reviewed_by_id IS NULL)",
            name="ck_knowledge_revision_reviewer_pair",
        ),
        CheckConstraint(
            "(approved_by_type IS NULL) = (approved_by_id IS NULL)",
            name="ck_knowledge_revision_approver_pair",
        ),
        CheckConstraint(
            "(terminal_by_type IS NULL) = (terminal_by_id IS NULL)",
            name="ck_knowledge_revision_terminal_pair",
        ),
        CheckConstraint(
            "CASE status "
            "WHEN 'PROPOSED' THEN reviewed_by_type IS NULL AND approved_by_type IS NULL "
            "WHEN 'REVIEWED' THEN reviewed_by_type IN ('human','policy') "
            "AND approved_by_type IS NULL "
            "ELSE reviewed_by_type IN ('human','policy') "
            "AND approved_by_type IN ('human','policy') END",
            name="ck_knowledge_revision_authority_audit",
        ),
        CheckConstraint(
            "terminal_by_type IS NULL OR terminal_by_type IN ('human','policy')",
            name="ck_knowledge_revision_terminal_actor",
        ),
        CheckConstraint(
            "(status IN ('SUPERSEDED','REVOKED','EXPIRED')) = "
            "(terminal_by_type IS NOT NULL AND terminal_by_id IS NOT NULL "
            "AND terminal_at IS NOT NULL)",
            name="ck_knowledge_revision_terminal_audit",
        ),
        Index(
            "ix_knowledge_revision_item_status_validity",
            "knowledge_item_id",
            "status",
            "valid_from",
            "valid_to",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="PROPOSED")
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    source_refs = Column(JSONB, nullable=False, default=list)
    evidence_refs = Column(JSONB, nullable=False, default=list)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    scope = Column(Text, nullable=False)
    classification = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    supersedes_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    proposed_by_type = Column(String, nullable=False)
    proposed_by_id = Column(String, nullable=False)
    reviewed_by_type = Column(String, nullable=True)
    reviewed_by_id = Column(String, nullable=True)
    approved_by_type = Column(String, nullable=True)
    approved_by_id = Column(String, nullable=True)
    terminal_by_type = Column(String, nullable=True)
    terminal_by_id = Column(String, nullable=True)
    terminal_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class KnowledgeConflict(Base):
    __tablename__ = "knowledge_conflicts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','RESOLVED')", name="ck_knowledge_conflict_status"
        ),
        CheckConstraint(
            "left_revision_id <> right_revision_id",
            name="ck_knowledge_conflict_not_self",
        ),
        CheckConstraint(
            "resolved_by_type IS NULL OR resolved_by_type IN ('human','policy')",
            name="ck_knowledge_conflict_resolver",
        ),
        CheckConstraint(
            "(status = 'RESOLVED') = "
            "(resolution_revision_id IS NOT NULL AND resolved_by_type IS NOT NULL "
            "AND resolved_by_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_knowledge_conflict_resolution_audit",
        ),
        Index(
            "uq_knowledge_conflict_pair",
            "knowledge_item_id",
            sa_text("least(left_revision_id, right_revision_id)"),
            sa_text("greatest(left_revision_id, right_revision_id)"),
            unique=True,
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    left_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    right_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status = Column(String, nullable=False, default="OPEN")
    reason = Column(Text, nullable=False)
    resolution_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    resolved_by_type = Column(String, nullable=True)
    resolved_by_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class ContextSource(Base):
    __tablename__ = "context_sources"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_id", "version", name="uq_context_source_version"
        ),
        CheckConstraint("version > 0", name="ck_context_source_version"),
        CheckConstraint("token_cost >= 0", name="ck_context_source_token_cost"),
        CheckConstraint(
            "load_tier IN ('MUST_LOAD','SHOULD_LOAD_IF_RELEVANT',"
            "'RETRIEVE_ON_DEMAND','NEVER_AUTO_LOAD')",
            name="ck_context_source_load_tier",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','SUPERSEDED','STALE')",
            name="ck_context_source_status",
        ),
        Index("ix_context_source_project_status", "project_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=False)
    load_tier = Column(String, nullable=False)
    classification = Column(String, nullable=False)
    status = Column(String, nullable=False, default="ACTIVE")
    supersedes_source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_sources.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provider_policy = Column(JSONB, nullable=False, default=dict)
    token_cost = Column(Integer, nullable=False)
    metadata_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ProviderRegistry(Base):
    __tablename__ = "provider_registry"
    __table_args__ = (
        CheckConstraint(
            "locality IN ('local','remote')", name="ck_provider_registry_locality"
        ),
        CheckConstraint(
            "health_state IN ('healthy','degraded','unhealthy','disabled')",
            name="ck_provider_registry_health",
        ),
        CheckConstraint("length(config_hash) = 64", name="ck_provider_config_hash"),
    )

    provider_id = Column(String, primary_key=True)
    adapter_id = Column(String, nullable=False)
    locality = Column(String, nullable=False)
    network_required = Column(Boolean, nullable=False, default=False)
    secret_ref = Column(Text, nullable=True)
    health_state = Column(String, nullable=False)
    circuit_open = Column(Boolean, nullable=False, default=False)
    config_hash = Column(String(64), nullable=False)
    version = Column(String, nullable=False)
    metadata_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ModelRegistry(Base):
    __tablename__ = "model_registry"
    __table_args__ = (
        CheckConstraint("context_limit > 0", name="ck_model_context_limit"),
        CheckConstraint("output_limit > 0", name="ck_model_output_limit"),
        CheckConstraint(
            "health_state IN ('healthy','degraded','unhealthy','disabled')",
            name="ck_model_registry_health",
        ),
        CheckConstraint("length(config_hash) = 64", name="ck_model_config_hash"),
    )

    model_id = Column(String, primary_key=True)
    provider_id = Column(
        String,
        ForeignKey("provider_registry.provider_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    capabilities = Column(JSONB, nullable=False, default=list)
    context_limit = Column(Integer, nullable=False)
    output_limit = Column(Integer, nullable=False)
    data_classifications = Column(JSONB, nullable=False, default=list)
    embedding_profile = Column(JSONB, nullable=True)
    benchmark = Column(JSONB, nullable=False, default=dict)
    health_state = Column(String, nullable=False, default="healthy")
    circuit_open = Column(Boolean, nullable=False, default=False)
    accessible = Column(Boolean, nullable=False, default=True)
    fallback_model_ids = Column(JSONB, nullable=False, default=list)
    version = Column(String, nullable=False)
    config_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class SkillRegistry(Base):
    __tablename__ = "skill_registry"
    __table_args__ = (
        CheckConstraint("length(commit_sha) = 40", name="ck_skill_commit_sha"),
        CheckConstraint("length(package_hash) = 64", name="ck_skill_package_hash"),
        CheckConstraint(
            "lower(version) NOT IN ('main','master','latest','head')",
            name="ck_skill_immutable_version",
        ),
        CheckConstraint(
            "trust_level IN ('untrusted','restricted','verified')",
            name="ck_skill_trust_level",
        ),
    )

    skill_id = Column(String, primary_key=True)
    source_uri = Column(Text, nullable=False)
    version = Column(String, nullable=False)
    commit_sha = Column(String(40), nullable=False)
    package_hash = Column(String(64), nullable=False)
    publisher = Column(String, nullable=False)
    owner = Column(String, nullable=False)
    trust_level = Column(String, nullable=False)
    required_tools = Column(JSONB, nullable=False, default=list)
    required_permissions = Column(JSONB, nullable=False, default=list)
    network_scope = Column(JSONB, nullable=False, default=list)
    filesystem_scope = Column(JSONB, nullable=False, default=list)
    supported_adapters = Column(JSONB, nullable=False, default=list)
    receipt_refs = Column(JSONB, nullable=False, default=dict)
    license_id = Column(String, nullable=True)
    security_scan_receipt = Column(Text, nullable=False)
    enabled_scope_type = Column(String, nullable=False)
    enabled_scope_id = Column(String, nullable=True)
    enabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ContextManifest(Base):
    __tablename__ = "context_manifests"
    __table_args__ = (
        UniqueConstraint("manifest_hash", name="uq_context_manifest_hash"),
        UniqueConstraint("attempt_id", name="uq_context_manifest_attempt"),
        CheckConstraint(
            "token_budget > 0 AND reserved_output >= 0 AND safety_margin >= 0",
            name="ck_context_manifest_budget",
        ),
        CheckConstraint(
            "token_budget > reserved_output + safety_margin",
            name="ck_context_manifest_available_budget",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("work_attempts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    manifest_hash = Column(String(64), nullable=False)
    schema_version = Column(String, nullable=False)
    provider_id = Column(
        String,
        ForeignKey("provider_registry.provider_id", ondelete="RESTRICT"),
        nullable=True,
    )
    model_id = Column(
        String,
        ForeignKey("model_registry.model_id", ondelete="RESTRICT"),
        nullable=True,
    )
    token_budget = Column(Integer, nullable=False)
    reserved_output = Column(Integer, nullable=False)
    safety_margin = Column(Integer, nullable=False)
    core_json = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class CompiledContext(Base):
    __tablename__ = "compiled_contexts"
    __table_args__ = (
        UniqueConstraint(
            "context_manifest_id", "adapter_id", name="uq_compiled_context_adapter"
        ),
        CheckConstraint("token_cost >= 0", name="ck_compiled_context_token_cost"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_manifest_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_manifests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    adapter_id = Column(String, nullable=False)
    rendered_hash = Column(String(64), nullable=False)
    rendered_artifact_ref = Column(
        UUID(as_uuid=True),
        ForeignKey("artifact_refs.id", ondelete="SET NULL"),
        nullable=True,
    )
    token_cost = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
