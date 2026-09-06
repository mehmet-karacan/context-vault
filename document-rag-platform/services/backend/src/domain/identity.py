"""Identity values shared by API, application and persistence boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


LOCAL_PRINCIPAL_ID = UUID("11111111-1111-4111-8111-111111111111")
LOCAL_WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")


@dataclass(frozen=True)
class PrincipalContext:
    principal_id: UUID
    workspace_id: UUID
    roles: frozenset[str]
    auth_mode: str

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles
