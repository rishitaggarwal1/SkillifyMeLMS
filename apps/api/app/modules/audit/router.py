from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.pagination import CursorPage, PageParams
from app.modules.audit import service
from app.modules.identity.authz import Permission, require_permission
from app.modules.identity.dependencies import CurrentPrincipal, TenantSession

router = APIRouter(prefix="/audit-log", tags=["audit"])


class AuditEntryOut(BaseModel):
    id: UUID
    organization_id: UUID | None
    actor_user_id: UUID | None
    actor_is_platform_admin: bool
    action: str
    target_type: str
    target_id: str | None
    before: Any
    after: Any
    ip: str | None
    request_id: str | None
    created_at: datetime


@router.get("", operation_id="list_audit_log")
async def list_audit_log(
    principal: CurrentPrincipal,
    session: TenantSession,
    page: PageParams,
    *,
    action: str | None = None,
    actor_user_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> CursorPage[AuditEntryOut]:
    """Admin actions in the active organization, newest first (org admins; platform admins
    without an active organization see every organization)."""
    org_id = (
        principal.organization_id
        if principal.is_platform_admin
        else require_permission(principal, Permission.AUDIT_READ)
    )
    entries, cursor = await service.list_entries(
        session,
        page,
        organization_id=org_id,
        action=action,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
    )
    return CursorPage(
        items=[
            AuditEntryOut.model_validate(
                {
                    **{c: getattr(e, c) for c in AuditEntryOut.model_fields},
                    "ip": str(e.ip) if e.ip else None,
                }
            )
            for e in entries
        ],
        next_cursor=cursor,
    )
