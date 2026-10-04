"""Direct runtime-role SQL, independent of HTTP and Pydantic response filtering."""

from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.db.outbox import OutboxEvent, add_outbox_event
from app.db.rls import APP_ROLE, RELAY_ROLE
from app.modules.assessments.models import (
    Question,
    QuestionKey,
    QuizAnswer,
    QuizAttempt,
    QuizVersion,
    QuizVersionKey,
)
from app.modules.assessments.tests.conftest import AssessmentWorld, save_attempt
from app.modules.identity.models import BatchMember
from tests.factories import Factory
from tests.fixtures import TenantSessionFactory

HELPERS = [
    "app.learning_event_owned(uuid,uuid)",
    "app.quiz_version_readable(uuid)",
    "app.quiz_attempt_readable(uuid)",
    "app.quiz_question_readable(uuid)",
    "app.quiz_reveal_allowed(uuid)",
    "app.quiz_attempt_solutions(uuid)",
]


@pytest.mark.parametrize("signature", HELPERS)
async def test_definers_have_fixed_path_and_no_public_execution(
    db_session: AsyncSession, signature: str
) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT prosecdef, proconfig, "
                "has_function_privilege(:app, oid, 'EXECUTE') app_exec, "
                "has_function_privilege(:relay, oid, 'EXECUTE') relay_exec "
                "FROM pg_proc WHERE oid = CAST(:signature AS regprocedure)"
            ),
            {"app": APP_ROLE, "relay": RELAY_ROLE, "signature": signature},
        )
    ).one()
    assert row.prosecdef
    assert "search_path=pg_catalog, public" in row.proconfig
    assert row.app_exec
    assert not row.relay_exec


@pytest.mark.parametrize("submitted", [False, True])
async def test_student_never_directly_reads_draft_or_published_keys(
    assessment_world: AssessmentWorld,
    factory: Factory,
    tenant_session: TenantSessionFactory,
    submitted: bool,
) -> None:
    w = assessment_world
    if submitted:
        async with factory.sessionmaker() as s, s.begin():
            await s.execute(
                update(QuizAttempt)
                .where(QuizAttempt.id == w.attempt.id)
                .values(
                    state="submitted",
                    submitted_at=w.attempt.started_at,
                    score=Decimal("1"),
                    passed=True,
                )
            )
    async with tenant_session(org=w.campus.c.id, user=w.campus.cse.id) as s:
        for table in ("question_keys", "quiz_version_keys", "questions", "question_skills"):
            assert (await s.execute(text(f"SELECT * FROM {table}"))).all() == []
        assert (
            await s.execute(
                text(
                    "SELECT k.* FROM quiz_version_keys k "
                    "JOIN quiz_version_questions q ON q.id=k.question_id"
                )
            )
        ).all() == []
    async with tenant_session(org=w.campus.p.id, user=w.campus.author.id) as s:
        assert (
            len(
                (
                    await s.scalars(
                        select(QuestionKey).where(QuestionKey.organization_id == w.campus.p.id)
                    )
                ).all()
            )
            == 3
        )
        assert (
            len(
                (
                    await s.scalars(
                        select(QuizVersionKey).where(
                            QuizVersionKey.organization_id == w.campus.p.id
                        )
                    )
                ).all()
            )
            == 3
        )
    async with tenant_session(org=w.campus.c.id, user=w.campus.c_instructor.id) as s:
        assert (await s.scalars(select(QuizVersionKey))).all() == []


async def test_only_selected_prompts_and_own_work_are_visible(
    assessment_world: AssessmentWorld,
    tenant_session: TenantSessionFactory,
) -> None:
    w = assessment_world
    async with tenant_session(org=w.campus.c.id, user=w.campus.cse.id) as s:
        assert set((await s.scalars(text("SELECT id FROM quiz_version_questions"))).all()) == set(
            w.attempt.question_ids
        )
        assert (await s.scalars(select(QuizAttempt.id))).all() == [w.attempt.id]
        assert (await s.scalars(select(QuizAnswer.attempt_id))).all() == [w.attempt.id]
        assert (
            await s.execute(
                text("SELECT * FROM app.quiz_attempt_solutions(:id)"), {"id": w.attempt.id}
            )
        ).all() == []
        assert (
            await s.execute(
                text("SELECT * FROM app.quiz_attempt_solutions(:id)"), {"id": w.other_attempt.id}
            )
        ).all() == []
    async with tenant_session(org=w.campus.c.id, user=w.campus.c_instructor.id) as s:
        assert set((await s.scalars(select(QuizAttempt.id))).all()) == {
            w.attempt.id,
            w.other_attempt.id,
        }
        assert len((await s.scalars(text("SELECT id FROM quiz_version_questions"))).all()) == 3
    for org, user in ((w.campus.p, w.campus.author), (w.campus.o, w.campus.o_admin)):
        async with tenant_session(org=org.id, user=user.id) as s:
            assert (await s.scalars(select(QuizAttempt.id))).all() == []
            assert (await s.scalars(select(QuizAnswer.id))).all() == []
    async with tenant_session(org=w.campus.p.id, user=w.campus.cse.id) as s:
        assert (await s.execute(text("SELECT * FROM quiz_version_keys"))).all() == []
        assert (await s.scalars(select(QuizAttempt.id))).all() == []


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE quiz_attempts SET state='submitted', submitted_at=now(), "
        "score=1, passed=true WHERE id=:id",
        "UPDATE quiz_attempts SET expires_at=now()+interval '1 day' WHERE id=:id",
        "UPDATE quiz_answers SET awarded_marks=1 WHERE attempt_id=:id",
        "DELETE FROM quiz_attempts WHERE id=:id",
        "INSERT INTO quiz_attempts SELECT * FROM quiz_attempts WHERE id=:id",
    ],
)
async def test_direct_runtime_writes_cannot_manufacture_or_alter_a_result(
    assessment_world: AssessmentWorld,
    tenant_session: TenantSessionFactory,
    sql: str,
) -> None:
    w = assessment_world
    async with tenant_session(org=w.campus.c.id, user=w.campus.cse.id) as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text(sql), {"id": w.attempt.id})


@pytest.mark.parametrize("table", ["quiz_versions", "quiz_version_questions", "quiz_version_keys"])
async def test_published_rows_are_immutable_even_for_editors(
    assessment_world: AssessmentWorld,
    tenant_session: TenantSessionFactory,
    table: str,
) -> None:
    w = assessment_world
    async with tenant_session(org=w.campus.p.id, user=w.campus.author.id) as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(
                text(f"DELETE FROM {table} WHERE organization_id=:org"), {"org": w.campus.p.id}
            )


@pytest.mark.parametrize("mode", ["score_only", "correct_answers", "explanations"])
@pytest.mark.parametrize("timing", ["immediately", "after_attempts_exhausted"])
async def test_sql_reveal_enforces_mode_and_timing(
    assessment_world: AssessmentWorld,
    factory: Factory,
    tenant_session: TenantSessionFactory,
    mode: str,
    timing: str,
) -> None:
    w = assessment_world
    async with factory.sessionmaker() as s, s.begin():
        await s.execute(
            update(QuizVersion)
            .where(QuizVersion.id == w.version.id)
            .values(reveal_mode=mode, reveal_timing=timing)
        )
        await s.execute(
            update(QuizAttempt)
            .where(QuizAttempt.id == w.attempt.id)
            .values(
                state="submitted",
                submitted_at=w.attempt.started_at,
                score=Decimal("1"),
                passed=True,
            )
        )

    async def read_expected(permitted: bool) -> None:
        async with tenant_session(org=w.campus.c.id, user=w.campus.cse.id) as s:
            rows = (
                await s.execute(
                    text("SELECT * FROM app.quiz_attempt_solutions(:id)"), {"id": w.attempt.id}
                )
            ).all()
            assert len(rows) == (2 if permitted else 0)
            if permitted:
                assert rows[0].answer_key == {"correct_option_ids": ["a"]}
                assert rows[0].explanation == (
                    "PRIVATE EXPLANATION" if mode == "explanations" else None
                )

    await read_expected(mode != "score_only" and timing == "immediately")
    second = await save_attempt(factory, w.version, w.enrollment, w.questions, number=2)
    # Quota has been used, but an active last attempt must still keep delayed keys hidden.
    await read_expected(mode != "score_only" and timing == "immediately")
    async with factory.sessionmaker() as s, s.begin():
        await s.execute(
            update(QuizAttempt).where(QuizAttempt.id == second.id).values(state="abandoned")
        )
    await read_expected(mode != "score_only")


async def test_revocation_removes_attempt_and_solution_access(
    assessment_world: AssessmentWorld,
    factory: Factory,
    tenant_session: TenantSessionFactory,
) -> None:
    w = assessment_world
    async with factory.sessionmaker() as s, s.begin():
        await s.execute(
            delete(BatchMember).where(
                BatchMember.batch_id == w.campus.cse_batch.id,
                BatchMember.user_id == w.campus.cse.id,
            )
        )
    async with tenant_session(org=w.campus.c.id, user=w.campus.cse.id) as s:
        assert (await s.scalars(select(QuizAttempt.id))).all() == []
        assert (
            await s.execute(
                text("SELECT * FROM app.quiz_attempt_solutions(:id)"), {"id": w.attempt.id}
            )
        ).all() == []


async def test_published_keys_survive_deleting_draft_source(
    assessment_world: AssessmentWorld,
    factory: Factory,
    tenant_session: TenantSessionFactory,
) -> None:
    w = assessment_world
    async with factory.sessionmaker() as s, s.begin():
        await s.execute(delete(Question).where(Question.bank_id == w.bank.id))
    async with tenant_session(org=w.campus.p.id, user=w.campus.author.id) as s:
        assert (
            len(
                (
                    await s.scalars(
                        select(QuizVersionKey).where(
                            QuizVersionKey.organization_id == w.campus.p.id
                        )
                    )
                ).all()
            )
            == 3
        )


async def test_one_active_attempt_is_enforced_by_database(
    assessment_world: AssessmentWorld,
    factory: Factory,
) -> None:
    w = assessment_world
    with pytest.raises(IntegrityError, match="uq_quiz_attempts_active"):
        await save_attempt(factory, w.version, w.enrollment, w.questions, number=2)


@pytest.mark.parametrize(
    ("aggregate", "event_type", "version"),
    [
        ("enrollment", "assignment_graded", 1),
        ("enrollment", "assignment_graded", 2),
        ("enrollment", "quiz_attempt_submitted", 1),
        ("video_progress", "video_progress", 1),
        ("enrollment", "lesson_completed", 1),
        ("other", "assignment_graded", 1),
    ],
)
async def test_existing_and_new_learning_outbox_payloads_are_student_isolated(
    assessment_world: AssessmentWorld,
    factory: Factory,
    tenant_session: TenantSessionFactory,
    aggregate: str,
    event_type: str,
    version: int,
) -> None:
    w = assessment_world
    event = OutboxEvent(
        id=new_id(),
        organization_id=w.campus.c.id,
        aggregate_type=aggregate,
        aggregate_id=w.enrollment.id,
        event_type=event_type,
        payload={"score": "1.00", "user_id": str(w.campus.cse.id)},
        headers={"version": version},
    )
    await factory._save(event)
    for org, user, visible in (
        (w.campus.c, w.campus.cse, True),
        (w.campus.c, w.campus.ece, False),
        (w.campus.c, w.campus.c_instructor, True),
        (w.campus.c, w.campus.c_admin, True),
        (w.campus.p, w.campus.author, False),
        (w.campus.o, w.campus.o_admin, False),
    ):
        async with tenant_session(org=org.id, user=user.id) as s:
            assert (await s.get(OutboxEvent, event.id) is not None) == visible
    async with tenant_session(
        org=w.campus.c.id, user=w.campus.platform_admin.id, platform_admin=True
    ) as s:
        assert await s.get(OutboxEvent, event.id) is not None


async def test_legitimate_student_learning_event_insert_returning_still_works(
    assessment_world: AssessmentWorld,
    tenant_session: TenantSessionFactory,
) -> None:
    w = assessment_world
    async with tenant_session(org=w.campus.c.id, user=w.campus.cse.id) as s:
        event = add_outbox_event(
            s,
            aggregate_type="enrollment",
            aggregate_id=w.enrollment.id,
            event_type="assignment_submitted",
            organization_id=w.campus.c.id,
            payload={"enrollment_id": str(w.enrollment.id)},
        )
        await s.flush()
        assert event.occurred_at is not None
        assert await s.get(OutboxEvent, event.id) is event
