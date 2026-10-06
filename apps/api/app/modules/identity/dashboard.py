"""Identity-owned dashboard reads, exposed only through identity.service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import any_, func, literal, select

from app.core.pagination import CursorParams, paginate_by_id
from app.modules.identity.authz import Permission, require_org_permission

if TYPE_CHECKING:
    from app.modules.identity.dependencies import RequestContext
from app.modules.identity.models import Batch, BatchMember, ImportJob, Invitation, Membership


async def dashboard_overview(ctx: RequestContext) -> dict[str, Any]:
    org = require_org_permission(ctx.principal, Permission.BATCH_MANAGE)
    counts = await ctx.session.execute(
        select(
            select(func.count())
            .where(Batch.organization_id == org, Batch.status == "active")
            .scalar_subquery(),
            select(func.count())
            .where(Membership.organization_id == org, Membership.role == "student")
            .scalar_subquery(),
            select(func.count())
            .where(
                Invitation.organization_id == org,
                Invitation.status == "pending",
                Invitation.expires_at > func.now(),
            )
            .scalar_subquery(),
            select(func.count())
            .where(Invitation.organization_id == org, literal("student") == any_(Invitation.roles))
            .scalar_subquery(),
            select(func.count())
            .where(ImportJob.organization_id == org, ImportJob.created_count > 0)
            .scalar_subquery(),
            select(func.count())
            .where(ImportJob.organization_id == org, ImportJob.status.in_(["queued", "running"]))
            .scalar_subquery(),
            select(func.count())
            .where(
                ImportJob.organization_id == org,
                ImportJob.status.in_(["failed", "completed_with_errors"]),
            )
            .scalar_subquery(),
        )
    )
    batches, students, invitations, invited, imported, running, failed = counts.one()
    return {
        "has_batch": bool(batches),
        "has_students": bool(students or invited or imported),
        "pending_invitations": invitations,
        "running_imports": running,
        "failed_imports": failed,
    }


async def dashboard_batch_students(
    ctx: RequestContext, batch_ids: Sequence[UUID]
) -> dict[UUID, list[UUID]]:
    org = require_org_permission(ctx.principal, Permission.BATCH_MANAGE)
    rows = await ctx.session.execute(
        select(BatchMember.batch_id, BatchMember.user_id)
        .join(
            Membership,
            (Membership.user_id == BatchMember.user_id)
            & (Membership.organization_id == org)
            & (Membership.role == "student"),
        )
        .where(BatchMember.organization_id == org, BatchMember.batch_id.in_(batch_ids))
    )
    result: dict[UUID, list[UUID]] = {bid: [] for bid in batch_ids}
    for bid, uid in rows:
        result[bid].append(uid)
    return result


async def dashboard_batches(
    ctx: RequestContext, params: CursorParams
) -> tuple[list[Batch], str | None]:
    org = require_org_permission(ctx.principal, Permission.BATCH_MANAGE)
    return await paginate_by_id(
        ctx.session,
        select(Batch).where(Batch.organization_id == org, Batch.status == "active"),
        Batch.id,
        params,
    )
