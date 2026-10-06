"""Independent raw-SQL aggregate authorization and score-only landing responses."""

from uuid import UUID

from sqlalchemy import text

from app.modules.assessments.tests.runtime_helpers import RuntimeWorld, correct
from tests.course_api import ok
from tests.fixtures import TenantSessionFactory


async def test_dashboard_outcomes_are_org_scoped_without_expanding_staff_access(
    runtime: RuntimeWorld, tenant_session: TenantSessionFactory
) -> None:
    w = runtime
    a = w.authored
    c = a.campus
    first = await w.start()
    ok(
        await w.request(
            "POST",
            f"/quiz-attempts/{first['id']}/submit",
            json={"answers": []},
            headers={"If-Match": "1"},
        )
    )
    second = await w.start(1)
    ok(
        await w.request(
            "POST",
            f"/quiz-attempts/{second['id']}/submit",
            json=correct(second),
            headers={"If-Match": "1"},
        )
    )
    for user, org, expected in (
        (c.c_instructor, c.c, (1, 1)),
        (c.c_admin, c.c, (1, 1)),
        (c.cse, c.c, (0, 0)),
        (c.author, c.p, (0, 0)),
        (c.o_admin, c.o, (0, 0)),
    ):
        # Actual app role; no HTTP, Pydantic, owner role or response filtering.
        async with tenant_session(user=user.id, org=org.id) as session:
            row = (
                await session.execute(
                    text("SELECT passed, failed FROM app.quiz_dashboard_outcomes()")
                )
            ).one()
            assert tuple(row) == expected
            if user.id == c.c_instructor.id:
                assert set(await session.scalars(text("SELECT id FROM quiz_attempts"))) == {
                    UUID(first["id"]),
                    UUID(second["id"]),
                }
                assert (
                    list(await session.scalars(text("SELECT question_id FROM quiz_version_keys")))
                    == []
                )
    summary = ok(await a.api.request("GET", "/dashboards/teach", c.c_instructor, c.c))
    assert (summary["quiz_passes"], summary["quiz_failures"]) == (1, 1)
    results = ok(await w.request("GET", "/dashboards/learn/results"))
    assert {r["title"] for r in results["items"]} == {a.body["title"]}
    assert [r["passed"] for r in results["items"]] == [True, False]
    assert [r["score"] for r in results["items"]] == ["6.00", "0.00"]
    assert all(
        set(r)
        == {
            "id",
            "kind",
            "enrollment_id",
            "course_id",
            "lesson_id",
            "course_title",
            "title",
            "occurred_at",
            "score",
            "max_marks",
            "passed",
        }
        for r in results["items"]
    )
    assert ok(await a.api.request("GET", "/dashboards/learn/results", c.ece, c.c))["items"] == []
