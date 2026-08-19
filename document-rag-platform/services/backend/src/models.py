import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import relationship

from .db import Base

EMBEDDING_DIMENSION = 1024  # BAAI/bge-m3 output size


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    documents = relationship(
        "Document", back_populates="project", cascade="all, delete-orphan"
    )


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="uploaded")
    error_message = Column(String, nullable=True)
    uploaded_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # --- Aşama 2 additive fields (nullable; see AKTIF_GOREV.md Bölüm 8.1) --
    source_type = Column(String, nullable=True)  # document | image | repository | directory | archive
    origin_uri = Column(Text, nullable=True)
    mime_type = Column(String, nullable=True)
    checksum = Column(String, nullable=True)
    active_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIMENSION), nullable=True)

    # --- Aşama 2 additive fields (nullable; see AKTIF_GOREV.md Bölüm 8.7) --
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_file_id = Column(
        UUID(as_uuid=True), ForeignKey("source_files.id", ondelete="SET NULL"), nullable=True
    )
    sequence_no = Column(Integer, nullable=True)
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
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True
    )
    metadata_json = Column(JSONB, nullable=True)
    search_vector = Column(TSVECTOR, nullable=True)
    identifiers = Column(ARRAY(Text), nullable=True)
    created_at = Column(DateTime, nullable=True)

    document = relationship("Document", back_populates="chunks", foreign_keys=[document_id])
    version = relationship("DocumentVersion", back_populates="chunks", foreign_keys=[version_id])
    source_file = relationship("SourceFile", foreign_keys=[source_file_id])
    parent_chunk = relationship("Chunk", remote_side=[id], foreign_keys=[parent_chunk_id])
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
        UniqueConstraint("document_id", "version_no", name="uq_document_versions_document_id_version_no"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version_no = Column(Integer, nullable=False)
    source_revision = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    parser_profile = Column(String, nullable=True)
    chunker_profile = Column(String, nullable=True)
    storage_key = Column(Text, nullable=True)
    normalized_artifact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)
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
    chunks = relationship("Chunk", back_populates="version")


class SourceFile(Base):
    """A single file inside a repository/directory/archive version (Bölüm 8.3)."""

    __tablename__ = "source_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id = Column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id = Column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type = Column(String, nullable=False)
    storage_key = Column(Text, nullable=False)
    checksum = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id = Column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(String, nullable=False, default="queued")
    stage = Column(String, nullable=True)
    progress = Column(Integer, nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    version = relationship("DocumentVersion", back_populates="ingestion_jobs")
    events = relationship(
        "IngestionEvent", back_populates="job", cascade="all, delete-orphan"
    )


class IngestionEvent(Base):
    """Durable progress/error event for an ingestion job (Bölüm 8.6).

    Redis is only the live-delivery layer; the durable record lives here.
    """

    __tablename__ = "ingestion_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True), ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), nullable=False
    )
    stage = Column(String, nullable=True)
    status = Column(String, nullable=True)
    message = Column(Text, nullable=True)
    payload_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    job = relationship("IngestionJob", back_populates="events")


class EmbeddingProfile(Base):
    """A versioned dense-embedding configuration (Bölüm 8.8).

    Only one profile is the initial active 1024-dim profile; differently
    sized embeddings are never mixed into the same indexed column (see
    chunks.embedding / chunk_embeddings.embedding, both Vector(1024)).
    """

    __tablename__ = "embedding_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)
    dimension = Column(Integer, nullable=False)
    distance_metric = Column(String, nullable=False)
    query_prefix = Column(Text, nullable=True)
    passage_prefix = Column(Text, nullable=True)
    profile_version = Column(Integer, nullable=False, default=1)
    config_hash = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    chunk_embeddings = relationship(
        "ChunkEmbedding", back_populates="embedding_profile", cascade="all, delete-orphan"
    )


class ChunkEmbedding(Base):
    """A chunk's embedding under a specific profile (Bölüm 8.9).

    UNIQUE(chunk_id, embedding_profile_id) is enforced via the composite
    primary key below.
    """

    __tablename__ = "chunk_embeddings"

    chunk_id = Column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    embedding_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("embedding_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding = Column(Vector(EMBEDDING_DIMENSION), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    chunk = relationship("Chunk", back_populates="chunk_embeddings")
    embedding_profile = relationship("EmbeddingProfile", back_populates="chunk_embeddings")


class Conversation(Base):
    """A chat conversation scoped to a project (Bölüm 8.10)."""

    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)

    messages = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """A single turn in a conversation (Bölüm 8.10)."""

    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    model = Column(String, nullable=True)
    answerable = Column(Boolean, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")
    citations = relationship(
        "MessageCitation", back_populates="message", cascade="all, delete-orphan"
    )


class MessageCitation(Base):
    """A single evidence citation backing a generated message (Bölüm 8.10)."""

    __tablename__ = "message_citations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id = Column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True
    )
    document_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    version_id = Column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    source_file_id = Column(
        UUID(as_uuid=True), ForeignKey("source_files.id", ondelete="SET NULL"), nullable=True
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
