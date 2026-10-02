from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel

from app.core.pagination import CursorPage, PageParams
from app.modules.audit import service
from app.modules.audit.models import AuditLog
from app.modules.identity.authz import Permission, require_permission, require_platform_admin
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
    return CursorPage(items=[_entry_out(e) for e in entries], next_cursor=cursor)


def _entry_out(e: AuditLog) -> AuditEntryOut:
    fields = {c: getattr(e, c) for c in AuditEntryOut.model_fields}
    return AuditEntryOut.model_validate({**fields, "ip": str(e.ip) if e.ip else None})


platform = APIRouter(prefix="/platform/audit-log", tags=["platform"])


@platform.get("", operation_id="platform_list_audit_log")
async def platform_list_audit_log(
    principal: CurrentPrincipal,
    session: TenantSession,
    page: PageParams,
    *,
    organization_id: Annotated[
        UUID | None,
        Query(description="One organization; omit for all (including platform-level entries)"),
    ] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    actor_user_id: UUID | None = None,
    target_type: Annotated[str | None, Query(max_length=50)] = None,
    target_id: Annotated[str | None, Query(max_length=100)] = None,
    since: Annotated[AwareDatetime | None, Query(description="Entries at or after")] = None,
    until: Annotated[AwareDatetime | None, Query(description="Entries before")] = None,
) -> CursorPage[AuditEntryOut]:
    """Admin actions across every organization, newest first (platform admins), whatever
    organization is active."""
    require_platform_admin(principal)
    entries, cursor = await service.list_entries(
        session,
        page,
        organization_id=organization_id,
        action=action,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        since=since,
        until=until,
    )
    return CursorPage(items=[_entry_out(e) for e in entries], next_cursor=cursor)
