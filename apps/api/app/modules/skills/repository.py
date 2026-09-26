"""Data access for the skills taxonomy (global: everyone reads; RLS lets only managers write)."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import cast, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorParams, paginate_by_id
from app.db.base import new_id
from app.db.types import LTree
from app.modules.skills.models import Skill


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SkillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, skill_id: UUID) -> Skill | None:
        return await self.session.get(Skill, skill_id)

    async def get_many(self, ids: Sequence[UUID]) -> list[Skill]:
        if not ids:
            return []
        return list(await self.session.scalars(select(Skill).where(Skill.id.in_(ids))))

    async def list_page(
        self, params: CursorParams, *, under: str | None, q: str | None
    ) -> tuple[list[Skill], str | None]:
        stmt = select(Skill)
        if under is not None:
            stmt = stmt.where(Skill.path.op("<@")(cast(under, LTree)))
        if q is not None:
            pattern = f"%{_escape_like(q)}%"
            stmt = stmt.where(or_(Skill.name.ilike(pattern), Skill.slug.ilike(pattern)))
        return await paginate_by_id(self.session, stmt, Skill.id, params, descending=False)

    async def create(
        self, *, parent_id: UUID | None, name: str, slug: str, path: str, description: str
    ) -> Skill:
        skill = Skill(
            id=new_id(),
            parent_id=parent_id,
            name=name,
            slug=slug,
            path=path,
            description=description,
        )
        self.session.add(skill)
        await self.session.flush()
        await self.session.refresh(skill)
        return skill

    async def update(self, skill_id: UUID, values: dict[str, Any]) -> Skill | None:
        if values:
            await self.session.execute(update(Skill).where(Skill.id == skill_id).values(**values))
        skill = await self.get(skill_id)
        if skill is not None:
            await self.session.refresh(skill)
        return skill
