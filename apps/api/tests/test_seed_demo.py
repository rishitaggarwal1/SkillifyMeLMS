"""`make seed-demo` (app/cli/seed_demo.py): the guards, the credentials file, and a full run
against the test database and the real dev Keycloak, twice (idempotent), plus --reset.

The full run uses its own email domain, so it never touches the demo logins of `make seed-demo`
on this machine (their passwords live in .secrets/demo-credentials.txt)."""

import argparse
import asyncio
import stat
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from app.cli import seed, seed_demo
from app.core.config import Settings
from app.core.pagination import CursorParams
from app.core.redis import create_redis
from app.core.storage import ObjectStorage
from app.db.session import create_engine, create_sessionmaker
from app.modules.assessments import service as assessments
from app.modules.assessments.schemas import AnswerBatch, AnswerInput, SavedAnswer
from app.modules.assignments import service as assignments
from app.modules.assignments.schemas import SubmitBody, SubmitText
from app.modules.courses import service as courses
from app.modules.courses.schemas import AssignmentCreate, LessonUpdate
from app.modules.enrollments import service as enrollments
from app.modules.enrollments.tasks import run_reconcile_course_org
from app.modules.identity.keycloak_admin import KeycloakAdmin
from app.modules.identity.models import Batch, Organization
from tests.factories import Factory


def _args(*flags: str) -> argparse.Namespace:
    return seed_demo.parse_args(list(flags))


@pytest.mark.parametrize(
    ("environment", "flags", "refused"),
    [
        ("local", (), False),
        ("local", ("--reset",), False),
        ("local", ("--rotate-passwords",), False),
        ("local", ("--upgrade-course",), False),
        ("staging", (), False),  # plain "ensure" runs on a server
        ("staging", ("--reset",), True),
        ("staging", ("--rotate-passwords",), True),
        ("staging", ("--upgrade-course",), True),
        ("staging", ("--upgrade-course", "--i-know-this-is-not-local"), False),
        ("staging", ("--reset", "--i-know-this-is-not-local"), False),
        ("test", ("--rotate-passwords",), True),
        ("production", (), True),
        ("production", ("--i-know-this-is-not-local",), True),
        ("production", ("--upgrade-course", "--i-know-this-is-not-local"), True),
    ],
)
def test_refusals(
    settings: Settings, environment: str, flags: tuple[str, ...], refused: bool
) -> None:
    configured = settings.model_copy(update={"environment": environment})
    assert (seed_demo.refusal(configured, _args(*flags)) is not None) is refused


def test_generated_passwords_are_strong_and_distinct() -> None:
    passwords = {seed_demo.generate_password() for _ in range(50)}
    assert len(passwords) == 50
    for p in passwords:
        assert len(p) == 20
        assert any(c.islower() for c in p)
        assert any(c.isupper() for c in p)
        assert any(c.isdigit() for c in p)
        assert any(not c.isalnum() for c in p)


def test_credentials_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "demo-credentials.txt"
    passwords = {u.email: f"pw-{i}" for i, u in enumerate(seed_demo.USERS)}
    seed_demo.write_credentials(path, passwords, "https://portal.example.test")
    assert seed_demo.read_credentials(path) == passwords
    content = path.read_text(encoding="utf-8")
    assert "https://portal.example.test" in content
    assert content.startswith("# SkillifyMe demo logins")
    if sys.platform != "win32":  # Windows has no POSIX permission bits
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert seed_demo.read_credentials(tmp_path / "missing.txt") == {}


@pytest.fixture
async def demo_domain(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[Path]:
    """Demo users on a throwaway domain, a temporary credentials file; Keycloak users removed
    afterwards."""
    domain = f"seed-{uuid7().hex[-8:]}.test"
    monkeypatch.setattr(seed_demo, "DOMAIN", domain)
    monkeypatch.setattr(
        seed_demo, "COURSE_SLUG", "python-foundations-" + domain.removesuffix(".test")
    )
    path = tmp_path / "demo-credentials.txt"
    monkeypatch.setenv("DEMO_CREDENTIALS_FILE", str(path))
    yield path
    async with httpx.AsyncClient() as http:
        admin = KeycloakAdmin(http, settings)
        headers = await admin._auth_header()
        for user in seed_demo.USERS:
            found = await admin.find_user_by_email(user.email)
            if found:
                await http.delete(f"{admin.base}/users/{found['id']}", headers=headers)


@pytest.fixture
async def redis_markers(settings: Settings) -> AsyncIterator[list[str]]:
    """Remove only this test's synthetic dirty buffers, even if an assertion fails."""
    keys: list[str] = []
    redis = create_redis(settings)
    try:
        yield keys
    finally:
        if keys:
            await redis.delete(*keys)
            await redis.srem("progress:dirty", *keys)
        await redis.aclose()


async def _progress(owner: async_sessionmaker[AsyncSession], domain: str) -> dict[str, int]:
    async with owner() as s:
        rows = await s.execute(
            text(
                "SELECT u.full_name, e.progress_percent FROM enrollments e "
                "JOIN users u ON u.id = e.user_id JOIN courses c ON c.id = e.course_id "
                "WHERE c.slug = :slug AND u.email LIKE :pattern"
            ),
            {"pattern": f"%@{domain}", "slug": seed_demo.COURSE_SLUG},
        )
        return {name: int(pct) for name, pct in rows}


async def _submissions(owner: async_sessionmaker[AsyncSession], domain: str) -> tuple[int, int]:
    async with owner() as s:
        row = (
            await s.execute(
                text(
                    "SELECT count(*), count(g.id) FROM assignment_submissions sub "
                    "JOIN users u ON u.id = sub.user_id "
                    "LEFT JOIN assignment_grades g ON g.submission_id = sub.id "
                    "WHERE u.email LIKE :pattern"
                ),
                {"pattern": f"%@{domain}"},
            )
        ).one()
        return int(row[0]), int(row[1])


async def _assessment_state(
    owner: async_sessionmaker[AsyncSession], domain: str
) -> dict[str, object]:
    """Read with an independent owner connection after the CLI's committed writes."""
    async with owner() as session:
        attempts = (
            await session.execute(
                text(
                    "SELECT u.full_name, a.attempt_number, a.state, a.score::text, a.passed "
                    "FROM quiz_attempts a JOIN users u ON u.id = a.user_id "
                    "WHERE u.email LIKE :pattern ORDER BY u.full_name, a.attempt_number"
                ),
                {"pattern": f"%@{domain}"},
            )
        ).all()
        grade = (
            await session.execute(
                text(
                    "SELECT g.raw_score::text, g.score::text, g.penalty_percent::text, "
                    "g.rubric_breakdown FROM assignment_grades g JOIN users u ON u.id = g.user_id "
                    "WHERE u.email LIKE :pattern ORDER BY g.id"
                ),
                {"pattern": f"%@{domain}"},
            )
        ).all()
        due = await session.scalar(
            text(
                "SELECT a.due_at FROM assignments a JOIN courses c ON c.id = a.course_id "
                "WHERE c.slug = :slug AND a.title = 'FizzBuzz'"
            ),
            {"slug": seed_demo.COURSE_SLUG},
        )
        return {
            "attempts": [tuple(a) for a in attempts],
            "grades": [tuple(g) for g in grade],
            "due": due,
        }


EXPECTED = {
    "Aarav Patel": 100, "Priya Sharma": 85, "Sneha Reddy": 57, "Ananya Iyer": 42,
    "Rohan Gupta": 28, "Vikram Singh": 14, "Kavya Nair": 0, "Arjun Mehta": 0,
}  # fmt: skip


async def test_full_run_is_idempotent_and_reset_restores(
    settings: Settings,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
    demo_domain: Path,
) -> None:
    lines: list[str] = []
    await seed_demo.run(settings, _args(), lines.append)
    domain = seed_demo.DOMAIN
    assert await _progress(owner_sessionmaker, domain) == EXPECTED
    assert await _submissions(owner_sessionmaker, domain) == (3, 1)
    state = await _assessment_state(owner_sessionmaker, domain)
    assert state["attempts"] == [
        ("Aarav Patel", 1, "submitted", "6.00", True),
        ("Ananya Iyer", 1, "submitted", "1.00", False),
        ("Priya Sharma", 1, "submitted", "1.00", False),
        ("Priya Sharma", 2, "submitted", "6.00", True),
        ("Rohan Gupta", 1, "submitted", "0.00", False),
    ]
    assert state["grades"] == [
        (
            "9.00",
            "8.10",
            "10.00",
            [
                {"criterion_id": "correctness", "score": "6.00"},
                {"criterion_id": "readability", "score": "3.00"},
            ],
        )
    ]
    assert state["due"] is not None
    first = await asyncio.to_thread(demo_domain.read_text, encoding="utf-8")
    passwords = seed_demo.read_credentials(demo_domain)
    assert len(passwords) == 12
    assert len(set(passwords.values())) == 12
    # Passwords never reach the output.
    assert not any(p in line for line in lines for p in passwords.values())

    await seed_demo.run(settings, _args(), lines.append)  # nothing changes on a rerun
    assert await asyncio.to_thread(demo_domain.read_text, encoding="utf-8") == first
    assert await _progress(owner_sessionmaker, domain) == EXPECTED
    assert await _submissions(owner_sessionmaker, domain) == (3, 1)
    assert await _assessment_state(owner_sessionmaker, domain) == state
    async with owner_sessionmaker() as s:
        courses = await s.scalar(
            text("SELECT count(*) FROM courses WHERE slug = :slug"),
            {"slug": seed_demo.COURSE_SLUG},
        )
    assert courses == 1

    # Someone used the demo: a student completed more. --reset puts the plan back.
    async with owner_sessionmaker() as s, s.begin():
        await s.execute(
            text(
                "UPDATE enrollments SET progress_percent = 100 FROM users u "
                "WHERE u.id = enrollments.user_id AND u.email = :email"
            ),
            {"email": f"demo.student8@{domain}"},
        )
    await seed_demo.run(settings, _args("--reset"), lines.append)
    assert await _progress(owner_sessionmaker, domain) == EXPECTED
    assert seed_demo.read_credentials(demo_domain) == passwords  # reset keeps the logins
    restored = await _assessment_state(owner_sessionmaker, domain)
    assert restored["attempts"] == state["attempts"]
    assert restored["due"] == state["due"]

    await seed_demo.run(settings, _args("--rotate-passwords"), lines.append)
    rotated = seed_demo.read_credentials(demo_domain)
    assert set(rotated) == set(passwords)
    assert not set(rotated.values()) & set(passwords.values())


@asynccontextmanager
async def _seeder(
    settings: Settings, owner: async_sessionmaker[AsyncSession]
) -> AsyncIterator[seed_demo.Seeder]:
    engine = create_engine(settings)
    redis = create_redis(settings)
    try:
        async with httpx.AsyncClient() as http:
            s = seed_demo.Seeder(
                settings=settings,
                owner=owner,
                app=create_sessionmaker(engine),
                redis=redis,
                storage=ObjectStorage(settings),
                keycloak=KeycloakAdmin(http, settings),
            )
            await seed_demo.ensure_accounts(s, rotate=False, log=lambda _: None)
            yield s
    finally:
        await redis.aclose()
        await engine.dispose()


async def _old_demo(
    s: seed_demo.Seeder, monkeypatch: pytest.MonkeyPatch
) -> tuple[UUID, list[UUID]]:
    # Build the real prior release through services, omitting only the new extension.
    async def no_extension(*_args: object) -> None:
        pass

    with monkeypatch.context() as legacy:
        legacy.setattr(seed_demo, "extend_assessments", no_extension)
        cid = await seed_demo.build_course(s)
    async with s.acting(seed_demo.by_local("author")) as ctx:
        version = await courses.resolve_version(ctx.session, cid, 1)
        assert version is not None
        ids = [UUID(x["id"]) for _m, x in courses.outline_lessons(version)]
        await courses.create_assignments(
            ctx, cid, AssignmentCreate(organization_id=s.orgs["demo-college"])
        )
    async with s.acting(seed_demo.by_local("admin")) as ctx:
        await courses.create_assignments(ctx, cid, AssignmentCreate(batch_ids=[s.orgs["cse"]]))
    await run_reconcile_course_org(s.app, cid, s.orgs["demo-college"])
    with monkeypatch.context() as legacy:
        legacy.setattr(seed_demo, "ensure_quiz_progress", no_extension)
        await seed_demo.ensure_progress(s, cid, ids, lambda _: None)
    return cid, ids


async def test_legacy_upgrade_is_major_batch_scoped_and_keeps_work(
    settings: Settings,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
    demo_domain: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Factory,
) -> None:
    async with _seeder(settings, owner_sessionmaker) as s:
        cid, ids = await _old_demo(s, monkeypatch)
        async with s.owner() as session:
            college = await session.get(Organization, s.orgs["demo-college"])
            cse = await session.get(Batch, s.orgs["cse"])
        assert college is not None
        assert cse is not None
        # A human CSE member outside USERS must move too; another batch must stay.
        peer = await factory.member(college, "student")
        outsider = await factory.member(college, "student")
        async with s.owner() as session, session.begin():
            other = await seed._upsert_batch(session, college, "ECE 2026")
        await factory.add_to_batch(cse, peer)
        await factory.add_to_batch(other, outsider)
        async with s.acting(seed_demo.by_local("admin")) as ctx:
            await courses.create_assignments(ctx, cid, AssignmentCreate(batch_ids=[other.id]))
        await run_reconcile_course_org(s.app, cid, college.id)
    passwords = seed_demo.read_credentials(demo_domain)
    original = await _assessment_state(owner_sessionmaker, seed_demo.DOMAIN)
    old_progress = await _progress(owner_sessionmaker, seed_demo.DOMAIN)
    lines: list[str] = []
    await seed_demo.run(settings, _args(), lines.append)
    assert any("--upgrade-course" in line for line in lines)
    assert await _progress(owner_sessionmaker, seed_demo.DOMAIN) == old_progress
    assert await _assessment_state(owner_sessionmaker, seed_demo.DOMAIN) == original
    await seed_demo.run(settings, _args("--upgrade-course"), lines.append)
    assert seed_demo.read_credentials(demo_domain) == passwords
    assert await _progress(owner_sessionmaker, seed_demo.DOMAIN) == EXPECTED
    assert await _submissions(owner_sessionmaker, seed_demo.DOMAIN) == (3, 1)
    async with owner_sessionmaker() as session:
        majors: dict[UUID, int] = dict(
            (
                await session.execute(
                    text(
                        "SELECT user_id, major_version FROM enrollments WHERE course_id = :course "
                        "AND user_id IN (:peer, :outsider)"
                    ),
                    {"course": cid, "peer": peer.id, "outsider": outsider.id},
                )
            ).all()
        )
    assert majors == {peer.id: 2, outsider.id: 1}
    async with (
        _seeder(settings, owner_sessionmaker) as s,
        s.acting(seed_demo.by_local("author")) as ctx,
    ):
        course = await courses.get_course(ctx, cid)
        assert course.current_version is not None
        assert course.current_version.version == "2.0"
        version = await courses.resolve_version(ctx.session, cid, 2)
        assert version is not None
        assert [UUID(x["id"]) for _m, x in courses.outline_lessons(version)][:6] == ids
    upgraded = await _assessment_state(owner_sessionmaker, seed_demo.DOMAIN)
    assert upgraded["grades"] == original["grades"]  # Historical grade and rules remain untouched.
    await seed_demo.run(settings, _args(), lines.append)
    await seed_demo.run(settings, _args(), lines.append)
    assert await _assessment_state(owner_sessionmaker, seed_demo.DOMAIN) == upgraded
    assert seed_demo.read_credentials(demo_domain) == passwords


@pytest.mark.parametrize(
    "edit",
    [LessonUpdate(title="Human correction"), LessonUpdate(completion_threshold=Decimal("0.5"))],
)
async def test_upgrade_refuses_edited_legacy_definition_without_publishing(
    settings: Settings,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
    demo_domain: Path,
    monkeypatch: pytest.MonkeyPatch,
    edit: LessonUpdate,
) -> None:
    async with _seeder(settings, owner_sessionmaker) as s:
        cid, ids = await _old_demo(s, monkeypatch)
        async with s.acting(seed_demo.by_local("author")) as ctx:
            await courses.update_lesson(
                ctx,
                cid,
                ids[0],
                edit,
                (await courses.get_course(ctx, cid)).revision,
            )
    before = await _progress(owner_sessionmaker, seed_demo.DOMAIN)
    with pytest.raises(RuntimeError, match="edited"):
        await seed_demo.run(settings, _args("--upgrade-course"), lambda _: None)
    assert await _progress(owner_sessionmaker, seed_demo.DOMAIN) == before
    async with owner_sessionmaker() as session:
        assert (
            await session.scalar(
                text("SELECT max(major) FROM course_versions WHERE course_id = :id"), {"id": cid}
            )
            == 1
        )


async def test_rerun_preserves_human_work_and_reset_is_scoped(  # noqa: PLR0915 - full lifecycle acceptance
    settings: Settings,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
    demo_domain: Path,
    redis_markers: list[str],
) -> None:
    await seed_demo.run(settings, _args(), lambda _: None)
    passwords = seed_demo.read_credentials(demo_domain)
    async with owner_sessionmaker() as session:
        cid = await session.scalar(
            text("SELECT id FROM courses WHERE slug = :slug"), {"slug": seed_demo.COURSE_SLUG}
        )
    async with _seeder(settings, owner_sessionmaker) as s:
        async with s.acting(seed_demo.by_local("student7")) as ctx:
            rows, _ = await enrollments.list_my_enrollments(
                ctx, CursorParams(limit=1), course_id=cid
            )
            eid = rows[0].id
            detail = await enrollments.get_enrollment(ctx, eid)
            quiz_lesson = UUID(detail.outline["modules"][1]["lessons"][-1]["id"])
            assignment_lesson = UUID(detail.outline["modules"][1]["lessons"][2]["id"])
            rules = await assessments.get_student_quiz(ctx, eid, quiz_lesson)
            attempt = await assessments.start_attempt(ctx, eid, quiz_lesson, rules.revision)
            blank = next(q for q in attempt.questions if q.question_type == "fill_blank")
            await assessments.save_answers(
                ctx,
                attempt.id,
                AnswerBatch(
                    answers=[
                        AnswerInput(
                            question_id=blank.id, answer=SavedAnswer(text="Human unfinished answer")
                        )
                    ]
                ),
                attempt.revision,
            )
            await enrollments.visit_lesson(ctx, eid, quiz_lesson)
        async with s.acting(seed_demo.by_local("student3")) as ctx:
            rows, _ = await enrollments.list_my_enrollments(
                ctx, CursorParams(limit=1), course_id=cid
            )
            student_eid = rows[0].id
            current = await assignments.get_student_assignment(
                ctx, s.storage, settings, student_eid, assignment_lesson
            )
            assert current.submission is not None
            submission = await assignments.submit(
                ctx,
                s.storage,
                settings,
                enrollment_id=student_eid,
                lesson_id=assignment_lesson,
                body=SubmitBody(submission=SubmitText(kind="text", text="Human resubmission")),
                if_match=current.submission.revision,
            )
        buffer = f"progress:video:{eid}:demo:asset"
        unrelated = "progress:video:another-enrollment:lesson:asset"
        redis_markers.extend([buffer, unrelated])
        await s.redis.set(buffer, "demo buffer")
        await s.redis.sadd("progress:dirty", buffer, unrelated)
        await s.redis.set(unrelated, "keep")
    state = await _assessment_state(owner_sessionmaker, seed_demo.DOMAIN)
    await seed_demo.run(settings, _args(), lambda _: None)
    await seed_demo.run(settings, _args("--upgrade-course"), lambda _: None)  # fresh 1.0: no-op
    assert await _assessment_state(owner_sessionmaker, seed_demo.DOMAIN) == state
    async with _seeder(settings, owner_sessionmaker) as s:
        async with s.acting(seed_demo.by_local("student7")) as ctx:
            resumed = await assessments.get_attempt(ctx, attempt.id)
            assert resumed.expires_at == attempt.expires_at
            answer = next(q for q in resumed.questions if q.id == blank.id).saved_answer
            assert answer == SavedAnswer(text="Human unfinished answer")
            assert (
                await enrollments.get_enrollment(ctx, eid)
            ).enrollment.last_lesson_id == quiz_lesson
        async with s.acting(seed_demo.by_local("student3")) as ctx:
            history, _ = await assignments.student_attempt_history(
                ctx,
                s.storage,
                settings,
                student_eid,
                assignment_lesson,
                params=CursorParams(limit=100),
            )
            assert len(history) == 2
            assert history[0].text_body == "Human resubmission"
    await seed_demo.run(settings, _args("--reset"), lambda _: None)
    assert await _progress(owner_sessionmaker, seed_demo.DOMAIN) == EXPECTED
    assert seed_demo.read_credentials(demo_domain) == passwords
    async with owner_sessionmaker() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM quiz_answers WHERE attempt_id = :id"), {"id": attempt.id}
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM submission_attempts WHERE submission_id = :id"),
                {"id": submission.id},
            )
            == 0
        )
    async with _seeder(settings, owner_sessionmaker) as s:
        assert await s.redis.get(buffer) is None
        assert not await s.redis.sismember("progress:dirty", buffer)
        assert await s.redis.get(unrelated) == "keep"
        assert await s.redis.sismember("progress:dirty", unrelated)


async def _can_sign_in(settings: Settings, email: str, password: str) -> bool:
    assert settings.kc_test_client_secret is not None
    async with httpx.AsyncClient() as http:
        response = await http.post(
            settings.oidc_token_url,
            data={
                "grant_type": "password",
                "client_id": "skillifyme-test",
                "client_secret": settings.kc_test_client_secret.get_secret_value(),
                "username": email,
                "password": password,
            },
        )
    return response.status_code == httpx.codes.OK


async def test_an_account_keycloak_lost_gets_its_password_back(
    settings: Settings, demo_domain: Path
) -> None:
    lines: list[str] = []
    await seed_demo.run(settings, _args(), lines.append)
    passwords = seed_demo.read_credentials(demo_domain)
    email = f"demo.admin@{seed_demo.DOMAIN}"
    # Keycloak loses the account (e.g. its local dev store was recreated); the file still has it.
    async with httpx.AsyncClient() as http:
        admin = KeycloakAdmin(http, settings)
        found = await admin.find_user_by_email(email)
        assert found is not None
        deleted = await http.delete(
            f"{admin.base}/users/{found['id']}", headers=await admin._auth_header()
        )
        assert deleted.status_code == httpx.codes.NO_CONTENT
    await seed_demo.run(settings, _args(), lines.append)
    assert seed_demo.read_credentials(demo_domain) == passwords  # same logins
    assert await _can_sign_in(settings, email, passwords[email])
