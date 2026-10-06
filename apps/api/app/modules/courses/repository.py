"""Data access for the courses module's own tables.

As in every module, RLS scopes each query to what the caller may see or change (migration 0004):
a course the caller can't read is simply absent, and writes by non-editors affect no rows. Explicit
organization filters below shape queries for their indexes; they are not the access control.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, Uuid, column, delete, func, or_, select, text, update, values
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorParams, paginate_by_id
from app.db.base import new_id
from app.modules.courses.models import (
    PLACEHOLDER_LESSON_TYPES,
    CatalogEntry,
    Course,
    CourseAssignment,
    CourseModule,
    CourseVersion,
    CourseVersionLesson,
    Lesson,
    LessonSkill,
)


def _positions(ids: Sequence[UUID]) -> Any:
    """A VALUES list of (id, position), positions starting at 1."""
    return values(column("id", Uuid), column("pos", Integer), name="new_positions").data(
        [(item_id, index) for index, item_id in enumerate(ids, start=1)]
    )


class CourseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, course_id: UUID) -> Course | None:
        return await self.session.get(Course, course_id, populate_existing=True)

    async def get_many(self, ids: Sequence[UUID]) -> list[Course]:
        if not ids:
            return []
        return list(await self.session.scalars(select(Course).where(Course.id.in_(ids))))

    async def list_for_org(
        self, organization_id: UUID, params: CursorParams, *, owned: bool | None
    ) -> tuple[list[Course], str | None]:
        """Courses the org owns, and published courses assigned to it."""
        assigned = (
            select(CourseAssignment.course_id)
            .where(CourseAssignment.organization_id == organization_id)
            .scalar_subquery()
        )
        is_owned = Course.organization_id == organization_id
        is_assigned = Course.id.in_(assigned) & Course.current_version_id.is_not(None)
        if owned is True:
            condition = is_owned
        elif owned is False:
            condition = ~is_owned & is_assigned
        else:
            condition = or_(is_owned, is_assigned)
        return await paginate_by_id(
            self.session, select(Course).where(condition), Course.id, params
        )

    async def list_all(
        self, params: CursorParams, *, organization_id: UUID | None, status: str | None
    ) -> tuple[list[Course], str | None]:
        """Courses of every organization, newest first (platform admins: RLS shows all)."""
        stmt = select(Course)
        if organization_id is not None:
            stmt = stmt.where(Course.organization_id == organization_id)
        if status is not None:
            stmt = stmt.where(Course.status == status)
        return await paginate_by_id(self.session, stmt, Course.id, params)

    async def count_by_status(self) -> dict[str, int]:
        """Courses by status, plus "published" (has a current version) among active ones."""
        rows = await self.session.execute(
            select(Course.status, func.count(), func.count(Course.current_version_id)).group_by(
                Course.status
            )
        )
        counts: dict[str, int] = {"published": 0}
        for status, total, published in rows:
            counts[status] = int(total)
            if status == "active":
                counts["published"] = int(published)
        return counts

    async def create(self, **values_: Any) -> Course:
        course = Course(id=new_id(), **values_)
        self.session.add(course)
        await self.session.flush()
        await self.session.refresh(course)
        return course

    async def update(self, course_id: UUID, values_: dict[str, Any]) -> None:
        if values_:
            await self.session.execute(
                update(Course).where(Course.id == course_id).values(**values_)
            )

    async def lock(self, course_id: UUID) -> Course | None:
        return await self.session.scalar(
            select(Course)
            .where(Course.id == course_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def bump_revision(self, course_id: UUID, expected: int | None) -> int | None:
        """Increment the draft revision (row-locking the course, which serializes concurrent edits
        of one course). None if the course isn't editable or `expected` is stale."""
        stmt = (
            update(Course)
            .where(Course.id == course_id)
            .values(revision=Course.revision + 1, updated_at=func.now())
            .returning(Course.revision)
        )
        if expected is not None:
            stmt = stmt.where(Course.revision == expected)
        return await self.session.scalar(stmt)


class DraftRepository:
    """Modules, lessons and lesson skill tags of a course's editable draft."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def modules(self, course_id: UUID) -> list[CourseModule]:
        return list(
            await self.session.scalars(
                select(CourseModule)
                .where(CourseModule.course_id == course_id)
                .order_by(CourseModule.position)
            )
        )

    async def lessons(self, course_id: UUID) -> list[Lesson]:
        return list(
            await self.session.scalars(
                select(Lesson)
                .where(Lesson.course_id == course_id)
                .order_by(Lesson.module_id, Lesson.position)
            )
        )

    async def lesson_ids(self, module_id: UUID) -> list[UUID]:
        return list(
            await self.session.scalars(
                select(Lesson.id).where(Lesson.module_id == module_id).order_by(Lesson.position)
            )
        )

    async def skill_ids(self, lesson_ids: Sequence[UUID]) -> dict[UUID, list[UUID]]:
        result: dict[UUID, list[UUID]] = {lid: [] for lid in lesson_ids}
        if lesson_ids:
            rows = await self.session.execute(
                select(LessonSkill.lesson_id, LessonSkill.skill_id)
                .where(LessonSkill.lesson_id.in_(lesson_ids))
                .order_by(LessonSkill.skill_id)
            )
            for lesson_id, skill_id in rows:
                result[lesson_id].append(skill_id)
        return result

    async def get_module(self, course_id: UUID, module_id: UUID) -> CourseModule | None:
        return await self.session.scalar(
            select(CourseModule).where(
                CourseModule.id == module_id, CourseModule.course_id == course_id
            )
        )

    async def get_lesson(self, course_id: UUID, lesson_id: UUID) -> Lesson | None:
        return await self.session.scalar(
            select(Lesson)
            .where(Lesson.id == lesson_id, Lesson.course_id == course_id)
            .execution_options(populate_existing=True)
        )

    async def create_module(self, course: Course, title: str) -> CourseModule:
        position = await self.session.scalar(
            select(func.coalesce(func.max(CourseModule.position), 0) + 1).where(
                CourseModule.course_id == course.id
            )
        )
        module = CourseModule(
            id=new_id(),
            course_id=course.id,
            organization_id=course.organization_id,
            title=title,
            position=position,
        )
        self.session.add(module)
        await self.session.flush()
        return module

    async def update_module(self, module_id: UUID, title: str) -> None:
        await self.session.execute(
            update(CourseModule)
            .where(CourseModule.id == module_id)
            .values(title=title, updated_at=func.now())
        )

    async def delete_module(self, module_id: UUID) -> None:
        await self.session.execute(delete(CourseModule).where(CourseModule.id == module_id))

    async def set_module_order(self, ids: Sequence[UUID]) -> None:
        new = _positions(ids)
        await self.session.execute(
            update(CourseModule).where(CourseModule.id == new.c.id).values(position=new.c.pos)
        )

    async def create_lesson(self, module: CourseModule, **values_: Any) -> Lesson:
        position = await self.session.scalar(
            select(func.coalesce(func.max(Lesson.position), 0) + 1).where(
                Lesson.module_id == module.id
            )
        )
        lesson = Lesson(
            id=new_id(),
            course_id=module.course_id,
            module_id=module.id,
            organization_id=module.organization_id,
            position=position,
            **values_,
        )
        self.session.add(lesson)
        await self.session.flush()
        await self.session.refresh(lesson)
        return lesson

    async def update_lesson(self, lesson_id: UUID, values_: dict[str, Any]) -> None:
        if values_:
            await self.session.execute(
                update(Lesson)
                .where(Lesson.id == lesson_id)
                .values(**values_, updated_at=func.now())
            )

    async def delete_lesson(self, lesson_id: UUID) -> None:
        await self.session.execute(delete(Lesson).where(Lesson.id == lesson_id))

    async def place_lessons(self, module_id: UUID, ids: Sequence[UUID]) -> None:
        """Make `ids` the module's lessons in this order (moving lessons in from other modules of
        the same course keeps their ids). Position uniqueness is checked at commit (deferred)."""
        new = _positions(ids)
        await self.session.execute(
            update(Lesson)
            .where(Lesson.id == new.c.id)
            .values(module_id=module_id, position=new.c.pos, updated_at=func.now())
        )

    async def renumber_lessons(self, module_id: UUID) -> None:
        ids = await self.lesson_ids(module_id)
        if ids:
            new = _positions(ids)
            await self.session.execute(
                update(Lesson).where(Lesson.id == new.c.id).values(position=new.c.pos)
            )

    async def renumber_modules(self, course_id: UUID) -> None:
        ids = [m.id for m in await self.modules(course_id)]
        if ids:
            await self.set_module_order(ids)

    async def replace_skills(
        self, lesson_id: UUID, organization_id: UUID, skill_ids: Sequence[UUID]
    ) -> None:
        await self.session.execute(delete(LessonSkill).where(LessonSkill.lesson_id == lesson_id))
        if skill_ids:
            await self.session.execute(
                pg_insert(LessonSkill).values(
                    [
                        {
                            "lesson_id": lesson_id,
                            "skill_id": sid,
                            "organization_id": organization_id,
                        }
                        for sid in skill_ids
                    ]
                )
            )


class VersionRepository:
    async def lessons_many(self, version_ids: Sequence[UUID]) -> list[CourseVersionLesson]:
        return list(
            await self.session.scalars(
                select(CourseVersionLesson).where(CourseVersionLesson.version_id.in_(version_ids))
            )
        )

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, version_id: UUID) -> CourseVersion | None:
        return await self.session.get(CourseVersion, version_id)

    async def get_many(self, ids: Sequence[UUID]) -> list[CourseVersion]:
        if not ids:
            return []
        return list(
            await self.session.scalars(select(CourseVersion).where(CourseVersion.id.in_(ids)))
        )

    async def latest(self, course_id: UUID, *, major: int | None = None) -> CourseVersion | None:
        """The newest version of a course, or of one major version (its latest minor)."""
        stmt = select(CourseVersion).where(CourseVersion.course_id == course_id)
        if major is not None:
            stmt = stmt.where(CourseVersion.major == major)
        return await self.session.scalar(
            stmt.order_by(CourseVersion.major.desc(), CourseVersion.minor.desc()).limit(1)
        )

    async def latest_majors(self, course_ids: Sequence[UUID]) -> dict[UUID, int]:
        if not course_ids:
            return {}
        rows = await self.session.execute(
            select(CourseVersion.course_id, func.max(CourseVersion.major))
            .where(CourseVersion.course_id.in_(course_ids))
            .group_by(CourseVersion.course_id)
        )
        return {course_id: int(major) for course_id, major in rows}

    async def latest_for_majors(
        self, pairs: Sequence[tuple[UUID, int]]
    ) -> dict[tuple[UUID, int], CourseVersion]:
        """The latest minor for each (course_id, major) pair, in one query."""
        if not pairs:
            return {}
        wanted = values(column("course_id", Uuid), column("major", Integer), name="wanted").data(
            list(set(pairs))
        )
        rows = await self.session.scalars(
            select(CourseVersion)
            .join(
                wanted,
                (CourseVersion.course_id == wanted.c.course_id)
                & (CourseVersion.major == wanted.c.major),
            )
            .ext(distinct_on(CourseVersion.course_id, CourseVersion.major))
            .order_by(CourseVersion.course_id, CourseVersion.major, CourseVersion.minor.desc())
        )
        return {(v.course_id, v.major): v for v in rows}

    async def list_page(
        self, course_id: UUID, params: CursorParams
    ) -> tuple[list[CourseVersion], str | None]:
        stmt = select(CourseVersion).where(CourseVersion.course_id == course_id)
        return await paginate_by_id(self.session, stmt, CourseVersion.id, params)

    async def create(
        self, version: CourseVersion, lessons: Sequence[CourseVersionLesson]
    ) -> CourseVersion:
        self.session.add(version)
        await self.session.flush()
        self.session.add_all(lessons)
        await self.session.flush()
        await self.session.refresh(version)
        return version

    async def lessons(self, version_id: UUID) -> list[CourseVersionLesson]:
        return list(
            await self.session.scalars(
                select(CourseVersionLesson)
                .where(CourseVersionLesson.version_id == version_id)
                .order_by(CourseVersionLesson.module_position, CourseVersionLesson.position)
            )
        )

    async def lesson(self, version_id: UUID, lesson_id: UUID) -> CourseVersionLesson | None:
        return await self.session.get(CourseVersionLesson, (version_id, lesson_id))

    async def required_lesson_ids(self, version_id: UUID) -> list[UUID]:
        """Lessons that count toward course progress (placeholders never do)."""
        return list(
            await self.session.scalars(
                select(CourseVersionLesson.lesson_id).where(
                    CourseVersionLesson.version_id == version_id,
                    CourseVersionLesson.is_required.is_(True),
                    CourseVersionLesson.lesson_type.not_in(PLACEHOLDER_LESSON_TYPES),
                )
            )
        )


class AssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, assignment_id: UUID) -> CourseAssignment | None:
        return await self.session.get(CourseAssignment, assignment_id)

    async def list_page(
        self, course_id: UUID, params: CursorParams
    ) -> tuple[list[CourseAssignment], str | None]:
        stmt = select(CourseAssignment).where(CourseAssignment.course_id == course_id)
        return await paginate_by_id(self.session, stmt, CourseAssignment.id, params)

    async def counts_by_course(self, course_ids: Sequence[UUID]) -> dict[UUID, tuple[int, int]]:
        """(org grants, batch assignments) per course, in one query."""
        counts = dict.fromkeys(course_ids, (0, 0))
        if course_ids:
            rows = await self.session.execute(
                select(
                    CourseAssignment.course_id,
                    func.count().filter(CourseAssignment.batch_id.is_(None)),
                    func.count(CourseAssignment.batch_id),
                )
                .where(CourseAssignment.course_id.in_(course_ids))
                .group_by(CourseAssignment.course_id)
            )
            counts.update({cid: (int(grants), int(batches)) for cid, grants, batches in rows})
        return counts

    async def org_grant(self, course_id: UUID, organization_id: UUID) -> CourseAssignment | None:
        return await self.session.scalar(
            select(CourseAssignment).where(
                CourseAssignment.course_id == course_id,
                CourseAssignment.organization_id == organization_id,
                CourseAssignment.batch_id.is_(None),
            )
        )

    async def insert_many(self, rows: Sequence[dict[str, Any]]) -> list[CourseAssignment]:
        """Insert assignments, skipping ones that already exist (idempotent)."""
        if not rows:
            return []
        ids = await self.session.scalars(
            pg_insert(CourseAssignment)
            .values([{"id": new_id(), **row} for row in rows])
            .on_conflict_do_nothing(constraint="uq_course_assignments_course_org_batch")
            .returning(CourseAssignment.id)
        )
        created = list(ids)
        if not created:
            return []
        return list(
            await self.session.scalars(
                select(CourseAssignment)
                .where(CourseAssignment.id.in_(created))
                .order_by(CourseAssignment.id)
            )
        )

    async def delete(self, assignment_id: UUID) -> bool:
        deleted = await self.session.scalar(
            delete(CourseAssignment)
            .where(CourseAssignment.id == assignment_id)
            .returning(CourseAssignment.id)
        )
        return deleted is not None

    async def batch_assignments(self, course_id: UUID, organization_id: UUID) -> dict[UUID, UUID]:
        """batch_id -> assignment_id for a course's batch rows in one org."""
        rows = await self.session.execute(
            select(CourseAssignment.batch_id, CourseAssignment.id).where(
                CourseAssignment.course_id == course_id,
                CourseAssignment.organization_id == organization_id,
                CourseAssignment.batch_id.is_not(None),
            )
        )
        return {batch_id: assignment_id for batch_id, assignment_id in rows if batch_id}

    async def courses_for_batches(
        self, organization_id: UUID, batch_ids: Sequence[UUID]
    ) -> dict[UUID, UUID]:
        """course_id -> an assignment id, for courses assigned to any of the batches."""
        if not batch_ids:
            return {}
        rows = await self.session.execute(
            select(CourseAssignment.course_id, CourseAssignment.id)
            .where(
                CourseAssignment.organization_id == organization_id,
                CourseAssignment.batch_id.in_(batch_ids),
            )
            .ext(distinct_on(CourseAssignment.course_id))
            .order_by(CourseAssignment.course_id, CourseAssignment.id)
        )
        return dict(rows.all())


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, **values_: Any) -> None:
        stmt = pg_insert(CatalogEntry).values(**values_)
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[CatalogEntry.course_id],
                set_={
                    key: stmt.excluded[key]
                    for key in values_
                    if key not in {"course_id", "organization_id"}
                }
                | {"updated_at": func.now()},
            )
        )

    async def delete(self, course_id: UUID) -> None:
        await self.session.execute(delete(CatalogEntry).where(CatalogEntry.course_id == course_id))

    async def list_page(self, params: CursorParams) -> tuple[list[CatalogEntry], str | None]:
        return await paginate_by_id(
            self.session, select(CatalogEntry), CatalogEntry.course_id, params
        )

    async def by_slug(self, slug: str) -> CatalogEntry | None:
        return await self.session.scalar(select(CatalogEntry).where(CatalogEntry.slug == slug))


async def student_course_assigned(
    session: AsyncSession, student: UUID, org: UUID, course: UUID
) -> bool:
    return bool(
        await session.scalar(
            text("SELECT app.student_course_assigned(:student,:org,:course)"),
            {"student": student, "org": org, "course": course},
        )
    )
