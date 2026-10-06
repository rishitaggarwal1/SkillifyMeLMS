"""Data access for the assignments module's own tables. RLS scopes every query (see migration
0011); explicit organization filters here are for index use."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.modules.assignments.models import (
    Assignment,
    AssignmentGrade,
    AssignmentSubmission,
    SubmissionAttempt,
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

    async def lock(self, submission_id: UUID) -> AssignmentSubmission | None:
        return await self.session.scalar(
            select(AssignmentSubmission)
            .where(AssignmentSubmission.id == submission_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def bind_first_attempt(
        self, submission_id: UUID, attempt_id: UUID
    ) -> AssignmentSubmission:
        row = await self.session.scalar(
            update(AssignmentSubmission)
            .where(AssignmentSubmission.id == submission_id)
            .values(active_attempt_id=attempt_id)
            .returning(AssignmentSubmission)
            .execution_options(populate_existing=True)
        )
        assert row is not None  # noqa: S101
        return row

    async def database_now(self) -> datetime:
        value = await self.session.scalar(text("SELECT clock_timestamp()"))
        assert isinstance(value, datetime)  # noqa: S101
        return value

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
            select(AssignmentGrade)
            .join(
                AssignmentSubmission,
                AssignmentSubmission.active_attempt_id == AssignmentGrade.attempt_id,
            )
            .where(AssignmentGrade.submission_id.in_(submission_ids))
            .ext(distinct_on(AssignmentGrade.submission_id))
            .order_by(AssignmentGrade.submission_id, AssignmentGrade.grade_sequence.desc())
        )
        return {g.submission_id: g for g in rows}

    async def upsert(self, values: dict[str, Any]) -> AssignmentGrade:
        """Append a correction; the old public interface retains its name."""
        row = AssignmentGrade(id=new_id(), **values)
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def for_attempts(self, attempt_ids: Sequence[UUID]) -> dict[UUID, AssignmentGrade]:
        rows = await self.session.scalars(
            select(AssignmentGrade)
            .where(AssignmentGrade.attempt_id.in_(attempt_ids))
            .ext(distinct_on(AssignmentGrade.attempt_id))
            .order_by(AssignmentGrade.attempt_id, AssignmentGrade.grade_sequence.desc())
        )
        return {g.attempt_id: g for g in rows}

    async def history(
        self, attempt_id: UUID, after: UUID | None, limit: int
    ) -> list[AssignmentGrade]:
        stmt = select(AssignmentGrade).where(AssignmentGrade.attempt_id == attempt_id)
        if after:
            stmt = stmt.where(AssignmentGrade.id < after)
        return list(
            await self.session.scalars(stmt.order_by(AssignmentGrade.id.desc()).limit(limit))
        )


class AttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_many(self, ids: Sequence[UUID]) -> dict[UUID, SubmissionAttempt]:
        rows = await self.session.scalars(
            select(SubmissionAttempt).where(SubmissionAttempt.id.in_(ids))
        )
        return {a.id: a for a in rows}

    async def graded_evidence(
        self, enrollment_ids: Sequence[UUID], lesson_ids: Sequence[UUID], major: int
    ) -> set[tuple[UUID, UUID]]:
        rows = await self.session.execute(
            select(AssignmentSubmission.enrollment_id, AssignmentSubmission.lesson_id)
            .join(SubmissionAttempt, SubmissionAttempt.id == AssignmentSubmission.active_attempt_id)
            .where(
                AssignmentSubmission.enrollment_id.in_(enrollment_ids),
                AssignmentSubmission.lesson_id.in_(lesson_ids),
                SubmissionAttempt.major_version == major,
                select(AssignmentGrade.id)
                .where(AssignmentGrade.attempt_id == SubmissionAttempt.id)
                .exists(),
            )
        )
        return {(eid, lid) for eid, lid in rows}

    async def get(self, attempt_id: UUID | None) -> SubmissionAttempt | None:
        if attempt_id is None:
            return None
        return await self.session.get(SubmissionAttempt, attempt_id)

    async def create(self, attempt: SubmissionAttempt) -> SubmissionAttempt:
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def history(
        self, submission_id: UUID, after: UUID | None, limit: int
    ) -> list[SubmissionAttempt]:
        stmt = select(SubmissionAttempt).where(SubmissionAttempt.submission_id == submission_id)
        if after:
            stmt = stmt.where(SubmissionAttempt.id < after)
        return list(
            await self.session.scalars(stmt.order_by(SubmissionAttempt.id.desc()).limit(limit))
        )
