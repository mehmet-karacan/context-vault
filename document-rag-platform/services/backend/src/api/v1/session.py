"""Authenticated identity confirmation; no keys or token material returned."""

from fastapi import APIRouter, Depends, Request
from src.domain.identity import PrincipalContext
from src.infrastructure.security.auth import get_principal_context
from .contracts import SessionResponse

router = APIRouter(tags=["session"])


@router.get("/session", response_model=SessionResponse)
def current_session(
    request: Request, principal: PrincipalContext = Depends(get_principal_context)
):
    return {
        "principal_id": str(principal.principal_id),
        "workspace_id": str(principal.workspace_id),
        "roles": sorted(principal.roles),
        "auth_mode": principal.auth_mode,
        "upload_max_bytes": request.app.state.settings.MAX_DOCUMENT_BYTES,
    }
