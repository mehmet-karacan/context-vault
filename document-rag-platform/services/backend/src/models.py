import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
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

    project = relationship("Project", back_populates="documents")
    chunks = relationship(
        "Chunk", back_populates="document", cascade="all, delete-orphan"
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

    document = relationship("Document", back_populates="chunks")
