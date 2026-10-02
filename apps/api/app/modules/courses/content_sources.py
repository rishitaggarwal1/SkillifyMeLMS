"""Lesson content that lives in another module (assignments): how publishing gets it.

The courses module owns the draft outline and publishing; the assignments module owns assignment
definitions and depends on courses. So that courses never imports assignments, a module registers
a `LessonContentSource` for its lesson type at app startup (`app.main.create_app`). Publishing
asks it for each lesson's published content; a lesson missing from the answer isn't ready and
blocks publishing.
"""

from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.courses.models import LessonType


class LessonContentSource(Protocol):
    async def published(
        self, session: AsyncSession, lesson_ids: Sequence[UUID]
    ) -> dict[UUID, dict[str, Any]]:
        """The content each ready lesson publishes (JSON-serializable). Lessons that aren't
        ready are absent."""
        ...


_SOURCES: dict[LessonType, LessonContentSource] = {}


def register(lesson_type: LessonType, source: LessonContentSource) -> None:
    """Called once at startup; registering again replaces the source (tests, reloads)."""
    _SOURCES[lesson_type] = source


def source_for(lesson_type: LessonType) -> LessonContentSource | None:
    return _SOURCES.get(lesson_type)
