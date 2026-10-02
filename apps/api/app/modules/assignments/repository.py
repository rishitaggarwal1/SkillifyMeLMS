"""Data access for the assignments module's own tables. RLS scopes every query (see migration
0011); explicit organization filters here are for index use."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.modules.assignments.models import (
    Assignment,
    AssignmentGrade,
    AssignmentSubmission,
    SubmissionStatus,
)


class AssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_lesson(self, lesson_id: UUID) -> Assignment | None:
        return await self.session.scalar(
            select(Assignment).where(Assignment.lesson_id == lesson_id)
        )

    async def by_lessons(self, lesson_ids: Sequence[UUID]) -> list[Assignment]:
        if not lesson_ids:
            return []
        return list(
            await self.session.scalars(
                select(Assignment).where(Assignment.lesson_id.in_(lesson_ids))
            )
        )

    async def upsert(self, values: dict[str, Any]) -> Assignment:
        """One definition per lesson: insert, or update the lesson's existing one."""
        stmt = pg_insert(Assignment).values(id=new_id(), **values)
        changes = {k: v for k, v in values.items() if k not in {"lesson_id", "created_by"}}
        row = await self.session.scalar(
            stmt.on_conflict_do_update(
                constraint="uq_assignments_lesson_id",
                set_={**changes, "updated_at": func.now()},
            )
            .returning(Assignment)
            .execution_options(populate_existing=True)
        )
        assert row is not None  # noqa: S101 - RETURNING always yields the row
        return row


# Graders see ungraded work first (rank 0), then graded (rank 1); oldest submitted first.
_pending_rank = case((AssignmentSubmission.status == SubmissionStatus.SUBMITTED, 0), else_=1)


class SubmissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, submission_id: UUID) -> AssignmentSubmission | None:
        return await self.session.get(AssignmentSubmission, submission_id, populate_existing=True)

    async def for_enrollment(
        self, enrollment_id: UUID, assignment_id: UUID
    ) -> AssignmentSubmission | None:
        return await self.session.scalar(
            select(AssignmentSubmission).where(
                AssignmentSubmission.enrollment_id == enrollment_id,
                AssignmentSubmission.assignment_id == assignment_id,
            )
        )

    async def for_enrollments(
        self, enrollment_ids: Sequence[UUID], lesson_ids: Sequence[UUID]
    ) -> list[AssignmentSubmission]:
        """Submissions of many enrollments for some lessons (one query; progress reports)."""
        if not enrollment_ids or not lesson_ids:
            return []
        return list(
            await self.session.scalars(
                select(AssignmentSubmission).where(
                    AssignmentSubmission.enrollment_id.in_(enrollment_ids),
                    AssignmentSubmission.lesson_id.in_(lesson_ids),
                )
            )
        )

    async def create(self, submission: AssignmentSubmission) -> AssignmentSubmission:
        self.session.add(submission)
        await self.session.flush()
        await self.session.refresh(submission)
        return submission

    async def update(
        self, submission_id: UUID, expected_revision: int, values: dict[str, Any]
    ) -> AssignmentSubmission | None:
        """Update and bump the revision, only if it is still `expected_revision` (else None)."""
        return await self.session.scalar(
            update(AssignmentSubmission)
            .where(
                AssignmentSubmission.id == submission_id,
                AssignmentSubmission.revision == expected_revision,
            )
            .values(**values, revision=AssignmentSubmission.revision + 1, updated_at=func.now())
            .returning(AssignmentSubmission)
            .execution_options(populate_existing=True)
        )

    async def queue(
        self,
        organization_id: UUID,
        lesson_id: UUID,
        *,
        status: str | None,
        user_ids: Sequence[UUID] | None,
        after: tuple[int, datetime, UUID] | None,
        limit: int,
    ) -> list[AssignmentSubmission]:
        """One lesson's submissions in an org: ungraded first, then oldest submitted first.
        `after` is the (pending-first rank, submitted_at, id) of the previous page's last row."""
        stmt = select(AssignmentSubmission).where(
            AssignmentSubmission.organization_id == organization_id,
            AssignmentSubmission.lesson_id == lesson_id,
        )
        if status is not None:
            stmt = stmt.where(AssignmentSubmission.status == status)
        if user_ids is not None:
            stmt = stmt.where(AssignmentSubmission.user_id.in_(list(user_ids)))
        if after is not None:
            rank, at, last_id = after
            stmt = stmt.where(
                or_(
                    _pending_rank > rank,
                    and_(
                        _pending_rank == rank,
                        or_(
                            AssignmentSubmission.submitted_at > at,
                            and_(
                                AssignmentSubmission.submitted_at == at,
                                AssignmentSubmission.id > last_id,
                            ),
                        ),
                    ),
                )
            )
        stmt = stmt.order_by(
            _pending_rank, AssignmentSubmission.submitted_at, AssignmentSubmission.id
        ).limit(limit)
        return list(await self.session.scalars(stmt))


class GradeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def for_submissions(self, submission_ids: Sequence[UUID]) -> dict[UUID, AssignmentGrade]:
        if not submission_ids:
            return {}
        rows = await self.session.scalars(
            select(AssignmentGrade).where(AssignmentGrade.submission_id.in_(submission_ids))
        )
        return {g.submission_id: g for g in rows}

    async def upsert(self, values: dict[str, Any]) -> AssignmentGrade:
        stmt = pg_insert(AssignmentGrade).values(id=new_id(), **values)
        changes = {k: v for k, v in values.items() if k not in {"submission_id", "organization_id"}}
        row = await self.session.scalar(
            stmt.on_conflict_do_update(
                constraint="uq_assignment_grades_submission",
                set_={**changes, "graded_at": func.now(), "updated_at": func.now()},
            )
            .returning(AssignmentGrade)
            .execution_options(populate_existing=True)
        )
        assert row is not None  # noqa: S101 - RETURNING always yields the row
        return row
