"""Atomic compare-and-swap activation for immutable document versions."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text

from src.domain.clock import Clock, SYSTEM_CLOCK


class ConcurrentActivationError(RuntimeError):
    pass


def activate_document_version(
    session,
    *,
    document_id: UUID,
    version_id: UUID,
    expected_current_version_id: UUID | None,
    clock: Clock = SYSTEM_CLOCK,
) -> None:
    """Activate exactly one ready version if the caller's snapshot is current."""
    result = session.execute(
        text(
            """
            WITH activated_document AS (
              UPDATE documents
                 SET active_version_id = :version_id,
                     status = 'indexed',
                     updated_at = :activated_at
               WHERE id = :document_id
                 AND active_version_id IS NOT DISTINCT FROM :expected_version_id
               RETURNING id
            )
            UPDATE document_versions
               SET activated_at = :activated_at
             WHERE id = :version_id
               AND document_id = :document_id
               AND status IN ('ready', 'completed')
               AND EXISTS (SELECT 1 FROM activated_document)
            RETURNING id
            """
        ),
        {
            "document_id": document_id,
            "version_id": version_id,
            "expected_version_id": expected_current_version_id,
            "activated_at": clock.now(),
        },
    )
    if result.first() is None:
        raise ConcurrentActivationError(
            "document activation lost compare-and-swap or version is not ready"
        )
