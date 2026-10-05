"""Batched completion-evidence service interfaces; registered only in app.wiring."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.courses.models import LessonType


class CompletionSource(Protocol):
    async def evidence(
        self,
        session: AsyncSession,
        enrollment_ids: Sequence[UUID],
        lesson_ids: Sequence[UUID],
        *,
        major: int,
    ) -> set[tuple[UUID, UUID]]: ...
    async def close_major(
        self, session: AsyncSession, enrollment_ids: Sequence[UUID], major: int
    ) -> None: ...


_sources: dict[LessonType, CompletionSource] = {}


def register(kind: LessonType, source: CompletionSource) -> None:
    _sources[kind] = source


def get(kind: LessonType) -> CompletionSource:
    if kind not in _sources:
        from app.wiring import wire  # noqa: PLC0415 - central lazy wiring

        wire()
    if kind not in _sources:
        raise RuntimeError(f"Missing completion service for {kind}")
    return _sources[kind]
