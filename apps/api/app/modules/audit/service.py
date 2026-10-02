"""Audit trail: the public interface other modules use to record admin actions.

Call `record()` inside the same transaction as the change it describes, so the audit row commits or
rolls back with it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorParams
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditLogRepository


@dataclass(frozen=True, slots=True)
class AuditActor:
    user_id: UUID
    organization_id: UUID | None
    is_platform_admin: bool = False
    ip: str | None = None
    request_id: str | None = None


async def record(
    session: AsyncSession,
    actor: AuditActor,
    *,
    action: str,
    target_type: str,
    target_id: UUID | str | None,
    before: Any = None,
    after: Any = None,
    organization_id: UUID | None = None,
) -> UUID:
    """Append one audit entry. `organization_id` defaults to the actor's active org."""
    return await AuditLogRepository(session).insert(
        {
            "organization_id": organization_id
            if organization_id is not None
            else actor.organization_id,
            "actor_user_id": actor.user_id,
            "actor_is_platform_admin": actor.is_platform_admin,
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id) if target_id is not None else None,
            "before": jsonable_encoder(before) if before is not None else None,
            "after": jsonable_encoder(after) if after is not None else None,
            "ip": actor.ip,
            "request_id": actor.request_id,
        }
    )


async def list_entries(
    session: AsyncSession,
    params: CursorParams,
    *,
    organization_id: UUID | None,
    action: str | None = None,
    actor_user_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[list[AuditLog], str | None]:
    return await AuditLogRepository(session).list_page(
        params,
        organization_id=organization_id,
        action=action,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        since=since,
        until=until,
    )
