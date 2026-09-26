"""Skills module public interface.

The taxonomy is global in this phase: everyone reads it; platform admins and staff of a
content-publisher org create and edit it (CLAUDE.md, "Content ownership & sharing"). RLS enforces
the same rule (migration 0004).
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.pagination import CursorParams
from app.modules.audit import service as audit
from app.modules.identity import service as identity
from app.modules.identity.authz import Permission, require_org_permission
from app.modules.identity.dependencies import RequestContext
from app.modules.skills.models import Skill
from app.modules.skills.repository import SkillRepository
from app.modules.skills.schemas import SkillCreate, SkillOut, SkillUpdate


def _out(skill: Skill) -> SkillOut:
    return SkillOut.model_validate(skill)


# ============================================================================ interface


async def existing_skill_ids(session: AsyncSession, skill_ids: Sequence[UUID]) -> set[UUID]:
    return {s.id for s in await SkillRepository(session).get_many(skill_ids)}


async def skill_names(session: AsyncSession, skill_ids: Sequence[UUID]) -> dict[UUID, str]:
    return {s.id: s.name for s in await SkillRepository(session).get_many(skill_ids)}


# ============================================================================ API


async def list_skills(
    session: AsyncSession, params: CursorParams, *, under: str | None, q: str | None
) -> tuple[list[SkillOut], str | None]:
    skills, cursor = await SkillRepository(session).list_page(params, under=under, q=q)
    return [_out(s) for s in skills], cursor


async def _require_manager(ctx: RequestContext) -> None:
    org_id = require_org_permission(ctx.principal, Permission.SKILL_MANAGE)
    if ctx.principal.is_platform_admin:
        return
    if not await identity.org_is_content_publisher(ctx.session, org_id):
        raise PermissionDeniedError(
            "Only content-publisher organizations can edit the skills taxonomy.",
            code="content_publisher_required",
        )


async def create_skill(ctx: RequestContext, data: SkillCreate) -> SkillOut:
    await _require_manager(ctx)
    repo = SkillRepository(ctx.session)
    path = data.slug
    if data.parent_id is not None:
        parent = await repo.get(data.parent_id)
        if parent is None:
            raise NotFoundError("Parent skill not found.")
        path = f"{parent.path}.{data.slug}"
    try:
        async with ctx.session.begin_nested():
            skill = await repo.create(
                parent_id=data.parent_id,
                name=data.name,
                slug=data.slug,
                path=path,
                description=data.description,
            )
    except IntegrityError as exc:
        raise ConflictError("This skill already exists.", code="skill_exists") from exc
    out = _out(skill)
    await audit.record(
        ctx.session, ctx.actor, action="skill.created", target_type="skill", target_id=skill.id,
        after=out,
    )  # fmt: skip
    return out


async def update_skill(ctx: RequestContext, skill_id: UUID, data: SkillUpdate) -> SkillOut:
    await _require_manager(ctx)
    repo = SkillRepository(ctx.session)
    before = await repo.get(skill_id)
    if before is None:
        raise NotFoundError("Skill not found.")
    before_out = _out(before)
    skill = await repo.update(skill_id, data.model_dump(exclude_unset=True, exclude_none=True))
    if skill is None:
        raise NotFoundError("Skill not found.")
    after = _out(skill)
    await audit.record(
        ctx.session, ctx.actor, action="skill.updated", target_type="skill", target_id=skill_id,
        before=before_out, after=after,
    )  # fmt: skip
    return after
