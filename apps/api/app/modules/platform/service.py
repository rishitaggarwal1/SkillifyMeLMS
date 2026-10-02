"""Platform dashboard: counts from each module's service interface (never their tables)."""

from datetime import UTC, datetime

from app.modules.courses import service as courses
from app.modules.enrollments import service as enrollments
from app.modules.identity import service as identity
from app.modules.identity.authz import require_platform_admin
from app.modules.identity.dependencies import RequestContext
from app.modules.platform.schemas import PlatformSummary


async def summary(ctx: RequestContext, now: datetime | None = None) -> PlatformSummary:
    require_platform_admin(ctx.principal)
    now = now or datetime.now(UTC)
    people = await identity.identity_counts(ctx.session)
    course_counts = await courses.course_counts(ctx.session)
    learning = await enrollments.enrollment_counts(ctx.session, now)
    return PlatformSummary(
        organizations=people.organizations,
        users=people.users,
        users_by_role=people.users_by_role,
        courses=course_counts.courses,
        enrollments=learning.enrollments,
        active_today=learning.active_today,
        active_since=learning.active_since,
        generated_at=now,
    )
