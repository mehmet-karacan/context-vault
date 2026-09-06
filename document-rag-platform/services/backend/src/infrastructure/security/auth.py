"""Fail-closed request authentication and workspace authorization."""

from __future__ import annotations

import secrets
from typing import Protocol
from uuid import UUID

from cryptography.hazmat.primitives import hashes, hmac
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.db import get_db
from src.domain.identity import (
    LOCAL_PRINCIPAL_ID,
    LOCAL_WORKSPACE_ID,
    PrincipalContext,
)
from src.domain.clock import utc_now
from src.models import ApiKey, Principal, Project, WorkspaceMembership


class OidcIdentityAdapter(Protocol):
    """Adapter contract; an enterprise provider is intentionally not assumed."""

    def authenticate(
        self, bearer_token: str, workspace_id: UUID
    ) -> PrincipalContext: ...


def hash_api_key(raw_key: str, pepper: str) -> str:
    """Return the stable keyed digest used for high-entropy API tokens.

    This remains byte-for-byte compatible with the existing HMAC-SHA-256
    records while using the cryptography library's explicit MAC primitive.
    It is not a password hash and never stores the bearer token itself.
    """

    signer = hmac.HMAC(pepper.encode("utf-8"), hashes.SHA256())
    signer.update(raw_key.encode("utf-8"))
    return signer.finalize().hex()


def _bearer_or_api_key(request: Request) -> str | None:
    direct = request.headers.get("x-api-key")
    if direct:
        return direct.strip()
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def _workspace_id(request: Request) -> UUID:
    raw = request.headers.get("x-workspace-id")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Workspace-ID is required",
        )
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="X-Workspace-ID must be a UUID",
        ) from exc


def get_principal_context(
    request: Request, db: Session = Depends(get_db)
) -> PrincipalContext:
    cfg = request.app.state.settings
    if cfg.AUTH_MODE == "disabled":
        context = PrincipalContext(
            principal_id=LOCAL_PRINCIPAL_ID,
            workspace_id=LOCAL_WORKSPACE_ID,
            roles=frozenset({"admin"}),
            auth_mode="disabled",
        )
        request.state.principal = context
        return context

    if cfg.AUTH_MODE == "oidc":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC adapter is not configured",
        )

    raw_key = _bearer_or_api_key(request)
    if not raw_key or len(raw_key) < 24:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid API key required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    workspace_id = _workspace_id(request)
    key_prefix = raw_key[:12]
    expected_hash = hash_api_key(raw_key, cfg.API_KEY_PEPPER)
    candidates = db.query(ApiKey).filter(ApiKey.key_prefix == key_prefix).all()
    api_key = next(
        (
            candidate
            for candidate in candidates
            if secrets.compare_digest(candidate.key_hash, expected_hash)
        ),
        None,
    )
    now = utc_now()
    if (
        api_key is None
        or api_key.revoked_at is not None
        or (api_key.expires_at is not None and api_key.expires_at <= now)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid API key required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal = db.get(Principal, api_key.principal_id)
    if principal is None or not principal.is_active:
        raise HTTPException(status_code=401, detail="Valid API key required")
    membership = (
        db.query(WorkspaceMembership)
        .filter(
            WorkspaceMembership.principal_id == principal.id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
        .first()
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Workspace access denied")
    api_key.last_used_at = now
    db.flush()
    context = PrincipalContext(
        principal_id=principal.id,
        workspace_id=workspace_id,
        roles=frozenset({membership.role}),
        auth_mode="api_key",
    )
    request.state.principal = context
    return context


def require_admin(
    principal: PrincipalContext = Depends(get_principal_context),
) -> PrincipalContext:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Administrator role required")
    return principal


def require_project_access(
    db: Session, principal: PrincipalContext, project_id: UUID | str
) -> Project:
    try:
        parsed_id = project_id if isinstance(project_id, UUID) else UUID(project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="project_id must be a UUID"
        ) from exc
    project = (
        db.query(Project)
        .filter(
            Project.id == parsed_id,
            Project.workspace_id == principal.workspace_id,
            Project.deleted_at.is_(None),
        )
        .first()
    )
    if project is None:
        # Same response for absent and cross-workspace IDs avoids enumeration.
        raise HTTPException(status_code=404, detail="Project not found")
    return project
