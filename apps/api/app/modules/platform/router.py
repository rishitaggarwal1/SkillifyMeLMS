"""Platform dashboard HTTP API (platform admins). The other `/platform/*` endpoints live in the
modules that own the data: users in identity, courses in courses, the audit log in audit."""

from fastapi import APIRouter

from app.modules.identity.dependencies import RequestCtx
from app.modules.platform import service
from app.modules.platform.schemas import PlatformSummary

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/summary", operation_id="platform_summary")
async def platform_summary(ctx: RequestCtx) -> PlatformSummary:
    """Counts across the platform: organizations, users by role, courses, enrollments and
    students active today (platform admins)."""
    return await service.summary(ctx)
