"""Versioned REST API root. Each module's router is included here, e.g.:

from app.modules.courses.router import router as courses_router
router.include_router(courses_router)
"""

from fastapi import APIRouter

from app.core.errors import ERROR_RESPONSES
from app.modules.audit.router import router as audit_router
from app.modules.courses.router import router as courses_router
from app.modules.enrollments.router import router as enrollments_router
from app.modules.identity.router import router as identity_router
from app.modules.media.router import router as media_router
from app.modules.skills.router import router as skills_router

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)
router.include_router(identity_router)
router.include_router(audit_router)
router.include_router(skills_router)
router.include_router(courses_router)
router.include_router(enrollments_router)
router.include_router(media_router)
