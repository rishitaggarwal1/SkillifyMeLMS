"""Test-data factories. They write with the owner role (bypassing RLS) and commit, so the data is
visible to the runtime-role sessions under test. Every call uses unique names, so tests never
depend on each other's data and the database needs no cleanup between tests."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import cast, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from app.db.base import new_id
from app.db.types import LTree
from app.modules.audit.models import AuditLog
from app.modules.courses.models import (
    Course,
    CourseAssignment,
    CourseModule,
    CourseVersion,
    CourseVersionLesson,
    Lesson,
    LessonType,
)
from app.modules.enrollments.models import Enrollment
from app.modules.identity.models import (
    Batch,
    BatchMember,
    ImportJob,
    ImportJobError,
    Invitation,
    Membership,
    Organization,
    User,
)
from app.modules.media.models import StoredFile, VideoAsset
from app.modules.skills.models import Skill


def _suffix() -> str:
    return uuid7().hex[-12:]


@dataclass
class Factory:
    sessionmaker: async_sessionmaker[AsyncSession]

    async def _save(self, *objs: Any) -> None:
        async with self.sessionmaker() as session, session.begin():
            session.add_all(objs)

    async def org(
        self, *, name: str | None = None, publisher: bool = False, status: str = "active"
    ) -> Organization:
        sfx = _suffix()
        org = Organization(
            id=new_id(),
            name=name or f"Org {sfx}",
            slug=f"org-{sfx}",
            is_content_publisher=publisher,
            status=status,
        )
        await self._save(org)
        return org

    async def user(
        self, *, email: str | None = None, full_name: str | None = None, status: str = "active"
    ) -> User:
        sfx = _suffix()
        user = User(
            id=new_id(),
            keycloak_sub=f"kc-{sfx}",
            email=email or f"user-{sfx}@example.test",
            full_name=full_name or f"User {sfx}",
            status=status,
        )
        await self._save(user)
        return user

    async def member(self, org: Organization, *roles: str, user: User | None = None) -> User:
        """Create (or reuse) a user and give them `roles` in `org`."""
        user = user or await self.user()
        await self._save(
            *(
                Membership(id=new_id(), user_id=user.id, organization_id=org.id, role=role)
                for role in roles
            )
        )
        return user

    async def batch(self, org: Organization, *, name: str | None = None) -> Batch:
        batch = Batch(id=new_id(), organization_id=org.id, name=name or f"Batch {_suffix()}")
        await self._save(batch)
        return batch

    async def add_to_batch(self, batch: Batch, *users: User) -> None:
        await self._save(
            *(
                BatchMember(
                    id=new_id(),
                    batch_id=batch.id,
                    organization_id=batch.organization_id,
                    user_id=u.id,
                )
                for u in users
            )
        )

    async def invitation(
        self, org: Organization, *, email: str | None = None, user: User | None = None
    ) -> Invitation:
        inv = Invitation(
            id=new_id(),
            organization_id=org.id,
            user_id=user.id if user else None,
            email=email or (user.email if user else f"invitee-{_suffix()}@example.test"),
            roles=["student"],
            batch_ids=[],
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        await self._save(inv)
        return inv

    async def import_job(self, org: Organization, *, batch: Batch | None = None) -> ImportJob:
        job = ImportJob(
            id=new_id(),
            organization_id=org.id,
            batch_id=batch.id if batch else None,
            file_key=f"imports/{_suffix()}.csv",
            file_name="students.csv",
        )
        err = ImportJobError(
            id=new_id(),
            import_job_id=job.id,
            organization_id=org.id,
            row_number=2,
            raw={"email": "bad"},
            code="invalid_email",
            message="Invalid email",
        )
        await self._save(job)
        await self._save(err)
        return job

    async def audit(
        self, org: Organization | None, actor: User, action: str = "test.action"
    ) -> UUID:
        entry = AuditLog(
            id=new_id(),
            organization_id=org.id if org else None,
            actor_user_id=actor.id,
            action=action,
            target_type="test",
            target_id="x",
        )
        await self._save(entry)
        return entry.id

    # ------------------------------------------------------------------ courses (Phase 2)

    async def course(self, owner: Organization, *, title: str | None = None) -> "Course":
        sfx = _suffix()
        course = Course(
            id=new_id(), organization_id=owner.id, title=title or f"Course {sfx}", slug=f"c-{sfx}"
        )
        await self._save(course)
        return course

    async def module(self, course: "Course", *, position: int = 1) -> "CourseModule":
        module = CourseModule(
            id=new_id(),
            course_id=course.id,
            organization_id=course.organization_id,
            title=f"Module {position}",
            position=position,
        )
        await self._save(module)
        return module

    async def lesson(
        self,
        module: "CourseModule",
        *,
        lesson_type: "LessonType | str" = "notes",
        position: int = 1,
    ) -> "Lesson":
        lesson = Lesson(
            id=new_id(),
            course_id=module.course_id,
            module_id=module.id,
            organization_id=module.organization_id,
            lesson_type=lesson_type,
            title=f"Lesson {position}",
            position=position,
        )
        await self._save(lesson)
        return lesson

    async def version(
        self, course: "Course", *lessons: "Lesson", major: int = 1, minor: int = 0
    ) -> "CourseVersion":
        version = CourseVersion(
            id=new_id(),
            course_id=course.id,
            organization_id=course.organization_id,
            major=major,
            minor=minor,
            release_type="major" if minor == 0 else "minor",
            title=course.title,
            snapshot={"lessons": [str(lesson.id) for lesson in lessons]},
        )
        await self._save(version)
        await self._save(
            *(
                CourseVersionLesson(
                    version_id=version.id,
                    lesson_id=lesson.id,
                    course_id=course.id,
                    organization_id=course.organization_id,
                    module_id=lesson.module_id,
                    module_position=1,
                    position=lesson.position,
                    lesson_type=lesson.lesson_type,
                    is_required=True,
                )
                for lesson in lessons
            )
        )
        async with self.sessionmaker() as session, session.begin():
            await session.execute(
                update(Course).where(Course.id == course.id).values(current_version_id=version.id)
            )
        course.current_version_id = version.id
        return version

    async def skill(self, *, parent_path: str = "dsa") -> "Skill":
        sfx = _suffix()
        async with self.sessionmaker() as session, session.begin():
            parent_id = await session.scalar(
                select(Skill.id).where(Skill.path == cast(parent_path, LTree))
            )
            skill = Skill(
                id=new_id(), parent_id=parent_id, name=f"Skill {sfx}", slug=f"s_{sfx}",
                path=f"{parent_path}.s_{sfx}",
            )  # fmt: skip
            session.add(skill)
        return skill

    async def assignment(
        self,
        course: "Course",
        org: Organization,
        *,
        batch: Batch | None = None,
        by_receiver: bool = False,
        parent: "CourseAssignment | None" = None,
    ) -> "CourseAssignment":
        assignment = CourseAssignment(
            id=new_id(),
            course_id=course.id,
            owner_organization_id=course.organization_id,
            organization_id=org.id,
            batch_id=batch.id if batch else None,
            assigned_by_org_id=org.id if by_receiver else course.organization_id,
            parent_assignment_id=parent.id if parent else None,
        )
        await self._save(assignment)
        return assignment

    async def enrollment(self, course: "Course", student: User, org: Organization) -> "Enrollment":
        enrollment = Enrollment(
            id=new_id(), organization_id=org.id, user_id=student.id, course_id=course.id,
            major_version=1,
        )  # fmt: skip
        await self._save(enrollment)
        return enrollment

    async def video(
        self, org: Organization, *, status: str = "created", duration: int | None = None
    ) -> "VideoAsset":
        video = VideoAsset(
            id=new_id(), organization_id=org.id, provider="local",
            provider_video_id=f"v-{_suffix()}", title="Video", status=status,
            duration_seconds=duration,
        )  # fmt: skip
        await self._save(video)
        return video

    async def pdf(self, org: Organization, *, status: str = "ready") -> "StoredFile":
        file = StoredFile(
            id=new_id(), organization_id=org.id, kind="pdf", storage_key=f"pdf/{_suffix()}.pdf",
            file_name="notes.pdf", content_type="application/pdf", size_bytes=1024, status=status,
        )  # fmt: skip
        await self._save(file)
        return file
