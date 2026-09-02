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
        Index(
            "chunks_embedding_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_document_version", "document_id", "version_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIMENSION), nullable=True)

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
    sized embeddings are never mixed into the same indexed column (see
    chunks.embedding / chunk_embeddings.embedding, both Vector(1024)).
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
    title = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    messages = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )


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
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    conversation = relationship("Conversation", back_populates="messages")
    citations = relationship(
        "MessageCitation", back_populates="message", cascade="all, delete-orphan"
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
    rank = Column(Integer, nullable=True)
    retrieval_score = Column(Float, nullable=True)
    reranker_score = Column(Float, nullable=True)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    line_start = Column(Integer, nullable=True)
    line_end = Column(Integer, nullable=True)
    citation_label = Column(String, nullable=True)

    message = relationship("Message", back_populates="citations")
