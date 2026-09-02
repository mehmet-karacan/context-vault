"""Fail-closed authorization and retrieval scope value object."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


SourceType = Literal["document", "image", "repository", "directory", "archive"]
DataPolicy = Literal["public", "internal", "confidential", "restricted"]


class RetrievalScope(BaseModel):
    """The complete, immutable scope required by every retrieval operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: UUID
    workspace_id: UUID
    project_id: UUID
    allowed_document_ids: tuple[UUID, ...] | None = None
    active_versions_only: bool = True
    allowed_source_types: tuple[SourceType, ...] = Field(
        default=("document", "image", "repository", "directory", "archive")
    )
    embedding_profile_id: UUID | None = None
    data_policy: DataPolicy = "internal"

    def retrieval_filters(self) -> dict[str, object]:
        """Return the strict repository filter projection.

        Empty document/source lists are deliberately preserved. The SQL filter
        layer renders them as ``FALSE`` instead of widening access.
        """

        filters: dict[str, object] = {
            "project_id": self.project_id,
            "source_types": list(self.allowed_source_types),
            "active_versions_only": self.active_versions_only,
        }
        if self.allowed_document_ids is not None:
            filters["document_ids"] = list(self.allowed_document_ids)
        if self.embedding_profile_id is not None:
            filters["embedding_profile_id"] = self.embedding_profile_id
        return filters
