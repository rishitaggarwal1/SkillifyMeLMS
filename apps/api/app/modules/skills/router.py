"""Skills taxonomy HTTP API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.core.pagination import CursorPage, PageParams
from app.modules.identity.dependencies import CurrentPrincipal, RequestCtx, TenantSession
from app.modules.skills import service
from app.modules.skills.schemas import SkillCreate, SkillOut, SkillPath, SkillUpdate

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", operation_id="list_skills")
async def list_skills(
    _principal: CurrentPrincipal,
    session: TenantSession,
    page: PageParams,
    under: Annotated[SkillPath | None, Query(description="This skill and its subtree")] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> CursorPage[SkillOut]:
    """The skills taxonomy (any signed-in user)."""
    items, cursor = await service.list_skills(session, page, under=under, q=q)
    return CursorPage(items=items, next_cursor=cursor)


@router.post("", status_code=status.HTTP_201_CREATED, operation_id="create_skill")
async def create_skill(ctx: RequestCtx, body: SkillCreate) -> SkillOut:
    """Add a skill (platform admins, and staff of content-publisher organizations)."""
    return await service.create_skill(ctx, body)


@router.patch("/{skill_id}", operation_id="update_skill")
async def update_skill(ctx: RequestCtx, skill_id: UUID, body: SkillUpdate) -> SkillOut:
    return await service.update_skill(ctx, skill_id, body)
