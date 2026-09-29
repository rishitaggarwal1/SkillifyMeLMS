import asyncio
import json
from uuid import UUID

from alembic import command
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from uuid_utils.compat import uuid7

from app.core.config import Settings
from tests.fixtures import alembic_config, recreate_database


def test_migrations_upgrade_downgrade_upgrade(settings: Settings, migrated_database: None) -> None:
    # A scratch database, so the round trip cannot disturb the shared test database or its pool.
    url = make_url(settings.migration_database_url.get_secret_value())
    scratch = url.set(database=f"{url.database}_migrations").render_as_string(hide_password=False)
    asyncio.run(recreate_database(scratch))
    cfg = alembic_config(scratch)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


# ------------------------------------------------------------------ 0007 backfill of file_ids

_ORG, _COURSE = uuid7(), uuid7()
_V1, _V2 = uuid7(), uuid7()  # 1.0 and 1.1 of the same course
_PDF_A, _PDF_B, _PDF_EMPTY, _NOTES, _VIDEO = (uuid7() for _ in range(5))
_FILE_A, _FILE_B, _FILE_A2 = uuid7(), uuid7(), uuid7()


def _lesson(lesson_id: UUID, lesson_type: str, content: dict[str, object]) -> dict[str, object]:
    return {"id": str(lesson_id), "lesson_type": lesson_type, "content": content}


def _snapshot(pdf_a_file: UUID) -> dict[str, object]:
    return {
        "schema": 1,
        "modules": [
            {
                "lessons": [
                    _lesson(_PDF_A, "pdf", {"file_id": str(pdf_a_file)}),
                    _lesson(_PDF_EMPTY, "pdf", {}),  # a pdf lesson with no file yet
                    _lesson(_NOTES, "notes", {"doc": {"type": "doc", "content": []}}),
                    _lesson(_VIDEO, "video", {"video_asset_id": str(uuid7())}),
                ]
            },
            {"lessons": [_lesson(_PDF_B, "pdf", {"file_id": str(_FILE_B)})]},  # second module
        ],
    }


async def _seed_at_0006(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Org', 'org')"),
                {"id": _ORG},
            )
            await conn.execute(
                text(
                    "INSERT INTO courses (id, organization_id, title, slug) "
                    "VALUES (:id, :org, 'Course', 'course')"
                ),
                {"id": _COURSE, "org": _ORG},
            )
            for version, minor, pdf_a_file in [(_V1, 0, _FILE_A), (_V2, 1, _FILE_A2)]:
                await conn.execute(
                    text(
                        "INSERT INTO course_versions (id, course_id, organization_id, major, "
                        "minor, release_type, title, snapshot) VALUES (:id, :course, :org, 1, "
                        ":minor, :rt, 'Course', CAST(:snapshot AS jsonb))"
                    ),
                    {
                        "id": version, "course": _COURSE, "org": _ORG, "minor": minor,
                        "rt": "major" if minor == 0 else "minor",
                        "snapshot": json.dumps(_snapshot(pdf_a_file)),
                    },
                )  # fmt: skip
                for position, (lesson, lesson_type) in enumerate(
                    [(_PDF_A, "pdf"), (_PDF_EMPTY, "pdf"), (_NOTES, "notes"), (_VIDEO, "video"),
                     (_PDF_B, "pdf")],
                    start=1,
                ):  # fmt: skip
                    await conn.execute(
                        text(
                            "INSERT INTO course_version_lessons (version_id, lesson_id, course_id, "
                            "organization_id, module_id, module_position, position, lesson_type, "
                            "is_required) VALUES (:v, :l, :course, :org, :m, 1, :p, "
                            "CAST(:t AS lesson_type), true)"
                        ),
                        {"v": version, "l": lesson, "course": _COURSE, "org": _ORG,
                         "m": uuid7(), "p": position, "t": lesson_type},
                    )  # fmt: skip
    finally:
        await engine.dispose()


async def _file_ids(url: str) -> dict[tuple[UUID, UUID], list[UUID]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT version_id, lesson_id, file_ids FROM course_version_lessons")
            )
            return {(r.version_id, r.lesson_id): list(r.file_ids) for r in rows}
    finally:
        await engine.dispose()


def test_0007_backfills_pdf_lesson_files_from_snapshots(
    settings: Settings, migrated_database: None
) -> None:
    url = make_url(settings.migration_database_url.get_secret_value())
    scratch = url.set(database=f"{url.database}_backfill").render_as_string(hide_password=False)
    asyncio.run(recreate_database(scratch))
    cfg = alembic_config(scratch)
    command.upgrade(cfg, "0006")
    asyncio.run(_seed_at_0006(scratch))

    command.upgrade(cfg, "0007")
    found = asyncio.run(_file_ids(scratch))

    assert found == {
        # Each version's own snapshot decides: 1.1 replaced the first PDF.
        (_V1, _PDF_A): [_FILE_A],
        (_V2, _PDF_A): [_FILE_A2],
        # Lessons in any module are found.
        (_V1, _PDF_B): [_FILE_B],
        (_V2, _PDF_B): [_FILE_B],
        # Nothing to backfill: a pdf lesson without a file, and non-pdf lessons (notes images
        # only exist from 0007 on).
        **{(v, lesson): [] for v in (_V1, _V2) for lesson in (_PDF_EMPTY, _NOTES, _VIDEO)},
    }
    command.upgrade(cfg, "head")  # later migrations apply on top of backfilled data
