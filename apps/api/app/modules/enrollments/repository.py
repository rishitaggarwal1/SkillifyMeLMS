"""Data access for enrollments and lesson progress (the student's org; RLS-scoped)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Integer,
    Uuid,
    bindparam,
    case,
    column,
    func,
    literal_column,
    select,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorParams, paginate_by_id
from app.db.base import new_id
from app.modules.enrollments.models import (
    Enrollment,
    EnrollmentStatus,
    LessonProgress,
    LessonProgressStatus,
)


@dataclass(frozen=True, slots=True)
class UpsertedEnrollment:
    id: UUID
    user_id: UUID
    course_id: UUID
    major_version: int
    created: bool  # False: a revoked enrollment was reactivated


@dataclass(frozen=True, slots=True)
class UpgradeCandidate:
    id: UUID
    user_id: UUID
    major_version: int


class EnrollmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, enrollment_id: UUID) -> Enrollment | None:
        return await self.session.get(Enrollment, enrollment_id, populate_existing=True)

    async def list_for_user(
        self, user_id: UUID, organization_id: UUID, params: CursorParams
    ) -> tuple[list[Enrollment], str | None]:
        stmt = select(Enrollment).where(
            Enrollment.user_id == user_id,
            Enrollment.organization_id == organization_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
        return await paginate_by_id(self.session, stmt, Enrollment.id, params)

    async def upsert_active(self, rows: Sequence[Mapping[str, Any]]) -> list[UpsertedEnrollment]:
        """Enroll (or reactivate revoked enrollments, keeping their progress). Already-active
        enrollments are untouched and not returned, so this is safe to re-run."""
        if not rows:
            return []
        insert = pg_insert(Enrollment).values([{"id": new_id(), **row} for row in rows])
        stmt = insert.on_conflict_do_update(
            constraint="uq_enrollments_user_course",
            set_={
                "status": EnrollmentStatus.ACTIVE,
                "source_assignment_id": insert.excluded.source_assignment_id,
                "updated_at": func.now(),
            },
            where=(Enrollment.status == EnrollmentStatus.REVOKED)
            & (Enrollment.organization_id == insert.excluded.organization_id),
        ).returning(
            Enrollment.id,
            Enrollment.user_id,
            Enrollment.course_id,
            Enrollment.major_version,
            literal_column("xmax = 0", Boolean),
        )
        result = await self.session.execute(stmt)
        return [UpsertedEnrollment(*row) for row in result]

    async def revoke(
        self,
        organization_id: UUID,
        *,
        course_id: UUID | None = None,
        user_id: UUID | None = None,
        keep_user_ids: Sequence[UUID] = (),
        keep_course_ids: Sequence[UUID] = (),
    ) -> list[tuple[UUID, UUID, UUID]]:
        """Revoke active enrollments in an org (for one course or one user), except the kept ones.
        Returns (enrollment_id, user_id, course_id)."""
        stmt = update(Enrollment).where(
            Enrollment.organization_id == organization_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
        if course_id is not None:
            stmt = stmt.where(Enrollment.course_id == course_id)
        if user_id is not None:
            stmt = stmt.where(Enrollment.user_id == user_id)
        if keep_user_ids:
            stmt = stmt.where(Enrollment.user_id.not_in(_uuid_values(keep_user_ids, "keep_users")))
        if keep_course_ids:
            stmt = stmt.where(
                Enrollment.course_id.not_in(_uuid_values(keep_course_ids, "keep_courses"))
            )
        result = await self.session.execute(
            stmt.values(status=EnrollmentStatus.REVOKED, updated_at=func.now()).returning(
                Enrollment.id, Enrollment.user_id, Enrollment.course_id
            )
        )
        return [(i, u, c) for i, u, c in result]

    async def touch(self, enrollment_id: UUID, lesson_id: UUID) -> None:
        await self.session.execute(
            update(Enrollment)
            .where(Enrollment.id == enrollment_id)
            .values(last_lesson_id=lesson_id, last_accessed_at=func.now(), updated_at=func.now())
        )

    async def set_progress(self, progress: Mapping[UUID, int]) -> None:
        """Store progress percentages; completed_at is set on reaching 100 and cleared below it."""
        if not progress:
            return
        new = values(column("id", Uuid), column("pct", Integer), name="new_progress").data(
            list(progress.items())
        )
        await self.session.execute(
            update(Enrollment)
            .where(Enrollment.id == new.c.id)
            .values(
                progress_percent=new.c.pct,
                completed_at=case(
                    (new.c.pct < 100, None),  # noqa: PLR2004
                    else_=func.coalesce(Enrollment.completed_at, func.now()),
                ),
                updated_at=func.now(),
            )
        )

    async def upgrade_candidates(
        self,
        course_id: UUID,
        organization_id: UUID,
        *,
        below_major: int,
        user_ids: Sequence[UUID] | None,
        after: UUID | None,
        limit: int,
    ) -> list[UpgradeCandidate]:
        stmt = select(Enrollment.id, Enrollment.user_id, Enrollment.major_version).where(
            Enrollment.course_id == course_id,
            Enrollment.organization_id == organization_id,
            Enrollment.major_version < below_major,
        )
        if user_ids is not None:
            stmt = stmt.where(Enrollment.user_id.in_(_uuid_values(user_ids, "batch_users")))
        if after is not None:
            stmt = stmt.where(Enrollment.id > after)
        rows = await self.session.execute(stmt.order_by(Enrollment.id).limit(limit))
        return [UpgradeCandidate(*row) for row in rows]

    async def set_major(self, enrollment_ids: Sequence[UUID], major: int) -> None:
        await self.session.execute(
            update(Enrollment)
            .where(Enrollment.id.in_(enrollment_ids))
            .values(major_version=major, updated_at=func.now())
        )


class LessonProgressRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def for_enrollment(self, enrollment_id: UUID) -> list[LessonProgress]:
        return list(
            await self.session.scalars(
                select(LessonProgress).where(LessonProgress.enrollment_id == enrollment_id)
            )
        )

    async def get(self, enrollment_id: UUID, lesson_id: UUID) -> LessonProgress | None:
        return await self.session.get(
            LessonProgress, (enrollment_id, lesson_id), populate_existing=True
        )

    async def start(self, enrollment: Enrollment, lesson_id: UUID) -> None:
        """Record that the student opened a lesson (no-op if there's already progress)."""
        await self.session.execute(
            pg_insert(LessonProgress)
            .values(
                enrollment_id=enrollment.id,
                lesson_id=lesson_id,
                organization_id=enrollment.organization_id,
                user_id=enrollment.user_id,
                status=LessonProgressStatus.IN_PROGRESS,
            )
            .on_conflict_do_nothing()
        )

    async def complete(self, enrollment: Enrollment, lesson_id: UUID, at: datetime) -> bool:
        """Mark a lesson completed; False if it already was (so events fire once)."""
        stmt = pg_insert(LessonProgress).values(
            enrollment_id=enrollment.id,
            lesson_id=lesson_id,
            organization_id=enrollment.organization_id,
            user_id=enrollment.user_id,
            status=LessonProgressStatus.COMPLETED,
            completed_at=at,
        )
        changed = await self.session.scalar(
            stmt.on_conflict_do_update(
                index_elements=[LessonProgress.enrollment_id, LessonProgress.lesson_id],
                set_={"status": LessonProgressStatus.COMPLETED, "completed_at": at,
                      "updated_at": func.now()},
                where=LessonProgress.status != LessonProgressStatus.COMPLETED,
            ).returning(LessonProgress.lesson_id)
        )  # fmt: skip
        return changed is not None

    async def completed_counts(
        self, enrollment_ids: Sequence[UUID], lesson_ids: Sequence[UUID]
    ) -> dict[UUID, int]:
        """Completed lessons among `lesson_ids`, per enrollment (one query)."""
        counts: dict[UUID, int] = dict.fromkeys(enrollment_ids, 0)
        if enrollment_ids and lesson_ids:
            rows = await self.session.execute(
                select(LessonProgress.enrollment_id, func.count())
                .where(
                    LessonProgress.enrollment_id.in_(enrollment_ids),
                    LessonProgress.lesson_id.in_(_uuid_values(lesson_ids, "required")),
                    LessonProgress.status == LessonProgressStatus.COMPLETED,
                )
                .group_by(LessonProgress.enrollment_id)
            )
            counts.update({eid: int(n) for eid, n in rows})
        return counts


def _uuid_values(ids: Sequence[UUID], name: str) -> Any:
    """`SELECT unnest(:ids)` for IN / NOT IN: one array parameter however many ids there are
    (asyncpg allows at most 32767 bind parameters per statement)."""
    return select(func.unnest(bindparam(name, list(dict.fromkeys(ids)), type_=ARRAY(Uuid))))
