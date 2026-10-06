"""A real pre-0012 grade event: repair existing data, not only new v1-shaped fixtures."""

import asyncio
from decimal import Decimal
from uuid import UUID

from alembic import command
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.base import new_id
from app.db.outbox import OutboxEvent
from app.db.tenancy import set_tenant_context
from app.modules.assignments.models import AssignmentGrade
from tests.factories import Factory
from tests.fixtures import alembic_config, recreate_database


async def _legacy_data(url: str) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    engine = create_async_engine(url)
    try:
        factory = Factory(async_sessionmaker(engine, expire_on_commit=False))
        org = await factory.org()
        student = await factory.member(org, "student")
        other = await factory.member(org, "student")
        grader = await factory.member(org, "instructor")
        course = await factory.course(org)
        module = await factory.module(course)
        lesson = await factory.lesson(module, lesson_type="assignment")
        version, assignment_id = await factory.homework_version(course, lesson)
        enrollment = await factory.enrollment(course, student, org)
        # Frozen pre-0012 SQL setup must not use today's ORM columns.
        submission_id = new_id()
        async with factory.sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO assignment_submissions "
                    "(id,organization_id,assignment_id,course_id,lesson_id,version_id,"
                    "enrollment_id,user_id,kind,text_body) "
                    "VALUES(:id,:org,:assignment,:course,:lesson,:version,:enrollment,:user,'text','legacy')"
                ),
                {
                    "id": submission_id,
                    "org": org.id,
                    "assignment": assignment_id,
                    "course": course.id,
                    "lesson": lesson.id,
                    "version": version.id,
                    "enrollment": enrollment.id,
                    "user": student.id,
                },
            )
        grade = AssignmentGrade(
            id=new_id(),
            organization_id=org.id,
            submission_id=submission_id,
            user_id=student.id,
            score=Decimal("8"),
            max_marks=10,
            graded_by=grader.id,
        )
        event = OutboxEvent(
            id=new_id(),
            organization_id=org.id,
            aggregate_type="enrollment",
            aggregate_id=enrollment.id,
            event_type="assignment_graded",
            payload={"user_id": str(student.id), "score": "8.00"},
            headers={"version": 1},
        )
        async with factory.sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO assignment_grades "
                    "(id,organization_id,submission_id,user_id,score,max_marks,graded_by) "
                    "VALUES(:id,:org,:sub,:user,8,10,:grader)"
                ),
                {
                    "id": grade.id,
                    "org": org.id,
                    "sub": submission_id,
                    "user": student.id,
                    "grader": grader.id,
                },
            )
            session.add(event)
        return org.id, student.id, other.id, event.id, grade.id
    finally:
        await engine.dispose()


async def _read_payload(url: str, org: UUID, user: UUID, event: UUID) -> list[object]:
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE skillify_app"))
            assert await session.scalar(text("SELECT current_user")) == "skillify_app"
            await set_tenant_context(
                session, organization_id=org, user_id=user, is_platform_admin=False
            )
            return list(
                (
                    await session.scalars(
                        text("SELECT payload FROM outbox_events WHERE id=:id"), {"id": event}
                    )
                ).all()
            )
    finally:
        await engine.dispose()


async def _assert_preserved(url: str, event: UUID, grade: UUID) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            assert await conn.scalar(
                text("SELECT headers FROM outbox_events WHERE id=:id"), {"id": event}
            ) == {"version": 1}
            assert await conn.scalar(
                text("SELECT score FROM assignment_grades WHERE id=:id"), {"id": grade}
            ) == Decimal("8")
    finally:
        await engine.dispose()


def test_preexisting_v1_grade_payload_is_hidden_after_0012_upgrade(
    settings: Settings,
    migrated_database: None,
) -> None:
    url = make_url(settings.migration_database_url.get_secret_value())
    scratch = url.set(database=f"{url.database}_assessment_upgrade").render_as_string(
        hide_password=False
    )
    asyncio.run(recreate_database(scratch))
    config = alembic_config(scratch)
    command.upgrade(config, "0011")
    org, student, other, event, grade = asyncio.run(_legacy_data(scratch))
    # Reproduce the old org-wide SELECT leak using only the runtime SQL role.
    assert asyncio.run(_read_payload(scratch, org, other, event)) == [
        {"user_id": str(student), "score": "8.00"}
    ]
    command.upgrade(config, "head")
    assert asyncio.run(_read_payload(scratch, org, other, event)) == []
    assert asyncio.run(_read_payload(scratch, org, student, event)) == [
        {"user_id": str(student), "score": "8.00"}
    ]
    asyncio.run(_assert_preserved(scratch, event, grade))
