from typing import Any
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorParams, paginate_by_id
from app.db.base import new_id
from app.modules.audit.models import AuditLog


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert(self, values: dict[str, Any]) -> UUID:
        """Plain INSERT without RETURNING: the writer (any org member) may not be allowed to read
        the audit log back under RLS, and RETURNING would require that."""
        entry_id = new_id()
        await self.session.execute(insert(AuditLog).values(id=entry_id, **values))
        return entry_id

    async def list_page(
        self,
        params: CursorParams,
        *,
        organization_id: UUID | None,
        action: str | None = None,
        actor_user_id: UUID | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> tuple[list[AuditLog], str | None]:
        stmt = select(AuditLog)
        if organization_id is not None:
            stmt = stmt.where(AuditLog.organization_id == organization_id)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if actor_user_id is not None:
            stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
        if target_type is not None:
            stmt = stmt.where(AuditLog.target_type == target_type)
        if target_id is not None:
            stmt = stmt.where(AuditLog.target_id == target_id)
        return await paginate_by_id(self.session, stmt, AuditLog.id, params)
