"""Lesson content that lives in another module (assignments): how publishing gets it.

The courses module owns the draft outline and publishing; the assignments module owns assignment
definitions and depends on courses. So that courses never imports assignments, a module provides
a `LessonContentSource` for its lesson type and `app.wiring` registers it. Publishing asks the
source for each lesson's published content; a lesson missing from the answer isn't ready and
blocks publishing.

**Fail closed:** if a course has lessons of a sourced type and no source is registered (even after
loading `app.wiring`), `required_source` raises `ContentSourceMissingError` (500). Publishing
never silently skips the check.
"""

import importlib
from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.courses.models import LessonType


class LessonContentSource(Protocol):
    async def published(
        self, session: AsyncSession, lesson_ids: Sequence[UUID]
    ) -> dict[UUID, dict[str, Any]]:
        """The content each ready lesson publishes (JSON-serializable). Lessons that aren't
        ready are absent."""
        ...


class ContentSourceMissingError(AppError):
    status_code = 500
    code = "content_source_not_registered"
    message = "Publishing is misconfigured: lesson content can't be resolved."


# Lesson types whose content another module provides.
SOURCED_TYPES = frozenset({LessonType.ASSIGNMENT})

logger = get_logger(__name__)
_SOURCES: dict[LessonType, LessonContentSource] = {}
_WIRING_MODULE = "app.wiring"


def register(lesson_type: LessonType, source: LessonContentSource) -> None:
    """Called by `app.wiring`; registering again replaces the source."""
    _SOURCES[lesson_type] = source


def _load_wiring() -> None:
    # Imported by name: a static import would make courses depend on the modules it wires.
    importlib.import_module(_WIRING_MODULE).wire()


def required_source(lesson_type: LessonType) -> LessonContentSource:
    """The source for a sourced lesson type, loading the wiring if needed; raises if absent."""
    if lesson_type not in _SOURCES:
        _load_wiring()
    source = _SOURCES.get(lesson_type)
    if source is None:
        logger.error("content_source_missing", lesson_type=lesson_type.value)
        raise ContentSourceMissingError(details={"lesson_type": lesson_type.value})
    return source
