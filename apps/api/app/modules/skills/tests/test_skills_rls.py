"""Global skills taxonomy: everyone reads; only platform admins and staff of a content-publisher org
write. Plus the seeded starter tree."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from tests.content_world import ContentWorld
from tests.fixtures import TenantSessionFactory

INSERT = (
    "INSERT INTO skills (id, parent_id, name, slug, path) "
    "VALUES (:id, (SELECT id FROM skills WHERE path = 'dsa'), :name, :slug, CAST(:path AS ltree))"
)


def _skill(tag: str) -> dict[str, Any]:
    slug = f"t_{tag}_{new_id().hex[:8]}"
    return {"id": new_id(), "name": f"Test {tag}", "slug": slug, "path": f"dsa.{slug}"}


async def _can_write(s: AsyncSession) -> bool:
    try:
        async with s.begin_nested():
            await s.execute(text(INSERT), _skill("w"))
    except DBAPIError:
        return False
    return True


async def test_everyone_reads_the_taxonomy(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    for user, org in ((cw.o_student, cw.o), (cw.cse, cw.c), (cw.p_author, cw.p)):
        async with tenant_session(org=org.id, user=user.id) as s:
            assert (await s.scalar(text("SELECT count(*) FROM skills WHERE path <@ 'dsa'"))) >= 9


@pytest.mark.parametrize(
    ("who", "allowed"),
    [
        ("platform_admin", True),
        ("p_author", True),  # instructor of a content publisher
        ("p_admin", True),
        ("p_lab", True),
        ("p_student", False),
        ("c_admin", False),  # admin of a college that is not a content publisher
        ("c_instructor", False),
        ("o_student", False),
    ],
)
async def test_who_can_create_skills(
    cw: ContentWorld, tenant_session: TenantSessionFactory, who: str, allowed: bool
) -> None:
    user = getattr(cw, who)
    if who == "platform_admin":
        ctx = tenant_session(org=None, user=user.id, platform_admin=True)
    else:
        org = {"p": cw.p, "c": cw.c, "o": cw.o}[who.split("_", maxsplit=1)[0]]
        ctx = tenant_session(org=org.id, user=user.id)
    async with ctx as s:
        assert await _can_write(s) is allowed
        updated = await s.execute(
            text("UPDATE skills SET description = 'x' WHERE path = 'dsa.arrays'")
        )
        assert (updated.rowcount == 1) is allowed  # type: ignore[attr-defined]


async def test_starter_tree_is_seeded(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT path::text, name FROM skills"))
    rows: dict[str, str] = dict(result.tuples().all())
    expected = {
        "aptitude": "Aptitude",
        "aptitude.quantitative": "Quantitative",
        "aptitude.logical_reasoning": "Logical Reasoning",
        "aptitude.verbal": "Verbal",
        "programming_fundamentals": "Programming Fundamentals",
        "oop": "Object-Oriented Programming",
        "dsa": "Data Structures & Algorithms",
        "dsa.arrays": "Arrays",
        "dsa.strings": "Strings",
        "dsa.linked_lists": "Linked Lists",
        "dsa.stacks_queues": "Stacks & Queues",
        "dsa.trees": "Trees",
        "dsa.graphs": "Graphs",
        "dsa.dynamic_programming": "Dynamic Programming",
        "dsa.sorting_searching": "Sorting & Searching",
        "core_cs": "Core CS",
        "core_cs.operating_systems": "Operating Systems",
        "core_cs.dbms": "DBMS",
        "core_cs.computer_networks": "Computer Networks",
    }
    assert {k: v for k, v in rows.items() if k in expected} == expected
    mismatched = await db_session.scalar(
        text(
            "SELECT count(*) FROM skills c JOIN skills p ON p.id = c.parent_id "
            "WHERE subpath(c.path, 0, nlevel(c.path) - 1) <> p.path"
        )
    )
    assert mismatched == 0  # parent links agree with the paths


async def test_lesson_type_enum_includes_placeholders(db_session: AsyncSession) -> None:
    values = list(
        await db_session.scalars(text("SELECT unnest(enum_range(NULL::lesson_type))::text"))
    )
    assert values == ["video", "notes", "pdf", "quiz", "lab", "assignment"]
