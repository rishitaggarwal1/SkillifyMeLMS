"""Versioned REST API root. Each module's router is included here, e.g.:

from app.modules.courses.router import router as courses_router
router.include_router(courses_router)
"""

from fastapi import APIRouter

from app.core.errors import ERROR_RESPONSES

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)
