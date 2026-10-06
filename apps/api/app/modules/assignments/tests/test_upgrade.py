"""An actual Phase 2.5 database retains IDs/content/grades on upgrade and round trip."""

import asyncio
from typing import Any

import pytest
from alembic import command
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.base import new_id
from app.modules.assessments.tests.test_security_upgrade import _legacy_data
from tests.fixtures import alembic_config, recreate_database


async def complete_legacy_work(url: str, kind: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            if kind == "file":
                fid = new_id()
                await conn.execute(
                    text(
                        "INSERT INTO files (id,organization_id,kind,storage_key,file_name,"
                        "content_type,size_bytes,status,created_by) SELECT :id,organization_id,"
                        "'submission',:key,'legacy.pdf','application/pdf',100,'ready',user_id "
                        "FROM assignment_submissions"
                    ),
                    {"id": fid, "key": f"legacy/{fid}.pdf"},
                )
                await conn.execute(
                    text(
                        "UPDATE assignment_submissions SET kind='file',text_body=NULL,file_id=:id"
                    ),
                    {"id": fid},
                )
            await conn.execute(text("UPDATE assignment_submissions SET status='graded'"))
            await conn.execute(
                text(
                    "INSERT INTO lesson_progress (enrollment_id,lesson_id,organization_id,"
                    "user_id,status,completed_at) SELECT enrollment_id,lesson_id,organization_id,"
                    "user_id,'completed',now() FROM assignment_submissions"
                )
            )
            await conn.execute(text("UPDATE enrollments SET progress_percent=100"))
    finally:
        await engine.dispose()


async def legacy_rows(url: str) -> list[Any]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as reader:
            return list(
                (
                    await reader.execute(
                        text(
                            "SELECT s.id,s.version_id,s.submitted_at,s.kind,s.text_body,s.file_id,"
                            "s.revision,s.status,g.id,g.score,g.graded_at,to_jsonb(f),"
                            "lp.completed_at,lp.status,e.progress_percent "
                            "FROM assignment_submissions s "
                            "JOIN assignment_grades g ON g.submission_id=s.id "
                            "JOIN enrollments e ON e.id=s.enrollment_id "
                            "JOIN lesson_progress lp ON lp.enrollment_id=s.enrollment_id "
                            "AND lp.lesson_id=s.lesson_id LEFT JOIN files f ON f.id=s.file_id"
                        )
                    )
                ).one()
            )
    finally:
        await engine.dispose()


async def assert_backfill(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as reader:
            row = (
                await reader.execute(
                    text(
                        "SELECT a.id=s.id AS kept_id,a.attempt_number,a.major_version,a.late_data,"
                        "a.assignment_rules,g.attempt_id=a.id AS linked,g.grade_sequence,"
                        "a.kind=s.kind AS kind_kept, "
                        "a.file_id IS NOT DISTINCT FROM s.file_id AS file_kept, "
                        "a.text_body IS NOT DISTINCT FROM s.text_body AS text_kept,g.raw_score,"
                        "g.score,g.penalty_marks FROM assignment_submissions s JOIN "
                        "submission_attempts a ON a.id=s.active_attempt_id "
                        "JOIN assignment_grades g ON g.attempt_id=a.id"
                    )
                )
            ).one()
            assert row.kept_id
            assert row.linked
            assert row.kind_kept
            assert row.file_kept
            assert row.text_kept
            assert row.attempt_number == 1
            assert row.grade_sequence == 1
            assert row.major_version == 1
            assert row.late_data is None
            assert row.assignment_rules["rubric"] is None
            assert row.assignment_rules["late_policy"]["mode"] == "accept"
            assert row.raw_score == row.score == 8
            assert row.penalty_marks == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize("kind", ["text", "file"])
def test_legacy_backfill_preserves_work_and_grade_on_round_trip(
    settings: Settings, migrated_database: None, kind: str
) -> None:
    url = make_url(settings.migration_database_url.get_secret_value())
    scratch = url.set(database=f"{url.database}_assignment_upgrade").render_as_string(
        hide_password=False
    )
    asyncio.run(recreate_database(scratch))
    cfg = alembic_config(scratch)
    command.upgrade(cfg, "0011")
    asyncio.run(_legacy_data(scratch))
    asyncio.run(complete_legacy_work(scratch, kind))
    before = asyncio.run(legacy_rows(scratch))
    command.upgrade(cfg, "head")
    assert asyncio.run(legacy_rows(scratch)) == before
    asyncio.run(assert_backfill(scratch))
    command.downgrade(cfg, "0014")
    assert asyncio.run(legacy_rows(scratch)) == before
    command.upgrade(cfg, "head")
    asyncio.run(assert_backfill(scratch))
    assert asyncio.run(legacy_rows(scratch)) == before
