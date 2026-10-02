"""Demo data for showing the four roles (`make seed-demo`). Idempotent; separate from `make seed`.

    python -m app.cli.seed_demo                     # create whatever is missing
    python -m app.cli.seed_demo --reset             # back to the demo state (local only)
    python -m app.cli.seed_demo --rotate-passwords  # new passwords for every login (local only)

Creates, through the same services the API uses (so every rule and check applies):
- demo logins `demo.<role>@skillifyme.co.in` in Keycloak and the database: a platform admin, the
  publisher's author, Demo College's admin and instructor, and 8 CSE 2026 students;
- "Python Foundations" (SkillifyMe): 2 modules, 6 lessons (two videos, notes with code, a PDF, an
  assignment), published 1.0, granted to Demo College and assigned to CSE 2026;
- varied progress: 0%, partial and complete; three submissions, one graded.

**Passwords** are random (20 characters), generated here and written only to
`DEMO_CREDENTIALS_FILE` (default `.secrets/demo-credentials.txt`, mode 0600, gitignored). They are
never printed or logged. A rerun keeps the passwords in that file; a demo user missing from it gets
a new one. Works the same locally and on a server: everything comes from settings (.env).

`--reset` and `--rotate-passwords` change what people may be using, so they refuse to run unless
ENVIRONMENT is local, or `--i-know-this-is-not-local` is given. Nothing runs in production.
"""

import argparse
import asyncio
import os
import secrets
import string
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli.seed import SeedOrg, _upsert_batch, _upsert_org
from app.core.config import Settings, get_settings
from app.core.pagination import CursorParams
from app.core.redis import create_redis
from app.core.storage import ObjectStorage
from app.db.base import new_id
from app.db.session import create_engine, create_sessionmaker
from app.db.tenancy import set_tenant_context
from app.modules.assignments import service as assignments
from app.modules.assignments.models import AssignmentGrade, AssignmentSubmission
from app.modules.assignments.schemas import AssignmentUpsert, GradeBody, SubmitBody, SubmitText
from app.modules.audit.service import AuditActor
from app.modules.courses import service as courses
from app.modules.courses.models import Course, LessonType, ReleaseType
from app.modules.courses.schemas import AssignmentCreate, CourseCreate, LessonCreate, PublishRequest
from app.modules.enrollments import service as enrollments
from app.modules.enrollments.models import Enrollment, LessonProgress
from app.modules.enrollments.tasks import run_reconcile_course_org
from app.modules.identity.authz import Principal
from app.modules.identity.dependencies import RequestContext
from app.modules.identity.keycloak_admin import KeycloakAdmin, NewUser
from app.modules.identity.models import BatchMember, Membership, Organization, OrgRole, User
from app.modules.media import service as media

DOMAIN = "skillifyme.co.in"
COURSE_SLUG = "python-foundations"
ASSETS = Path(__file__).resolve().parent / "demo_assets"
NOT_LOCAL_FLAG = "--i-know-this-is-not-local"

PUBLISHER = SeedOrg("skillifyme", "SkillifyMe", is_content_publisher=True)
COLLEGE = SeedOrg("demo-college", "Demo College", batches=("CSE 2026",))


@dataclass(frozen=True)
class DemoUser:
    local: str  # demo.<local>@skillifyme.co.in
    full_name: str
    role: str  # shown in the credentials file
    org: str | None = None  # org slug
    org_roles: tuple[str, ...] = ()
    platform_admin: bool = False
    in_cse: bool = False
    lessons: tuple[int, ...] = ()  # lesson indexes completed (5 = the assignment: submitted)
    grade: str | None = None  # graded score for the assignment

    @property
    def email(self) -> str:
        return f"demo.{self.local}@{DOMAIN}"


STUDENT = ("student",)
USERS = (
    DemoUser("platform-admin", "Ritu Verma", "platform admin", platform_admin=True),
    DemoUser(
        "author", "Nikhil Joshi", "author (SkillifyMe instructor)", "skillifyme", ("instructor",)
    ),
    DemoUser(
        "admin", "Lakshmi Menon", "college admin (Demo College)", "demo-college", ("org_admin",)
    ),
    DemoUser(
        "instructor", "Suresh Kumar", "instructor (Demo College)", "demo-college", ("instructor",)
    ),
    # Students: lessons are 0 Why Python, 1 Variables, 2 Cheat sheet, 3 Loops, 4 Functions,
    # 5 FizzBuzz (assignment: submitted; graded when `grade` is set).
    DemoUser(
        "student",
        "Priya Sharma",
        "student",
        "demo-college",
        STUDENT,
        in_cse=True,
        lessons=(0, 1, 2, 3, 4, 5),
    ),
    DemoUser(
        "student2",
        "Aarav Patel",
        "student",
        "demo-college",
        STUDENT,
        in_cse=True,
        lessons=(0, 1, 2, 3, 4, 5),
        grade="9",
    ),
    DemoUser(
        "student3",
        "Ananya Iyer",
        "student",
        "demo-college",
        STUDENT,
        in_cse=True,
        lessons=(0, 1, 2, 5),
    ),
    DemoUser(
        "student4", "Rohan Gupta", "student", "demo-college", STUDENT, in_cse=True, lessons=(0, 1)
    ),
    DemoUser(
        "student5",
        "Sneha Reddy",
        "student",
        "demo-college",
        STUDENT,
        in_cse=True,
        lessons=(0, 1, 2, 3),
    ),
    DemoUser(
        "student6", "Vikram Singh", "student", "demo-college", STUDENT, in_cse=True, lessons=(1,)
    ),
    DemoUser("student7", "Kavya Nair", "student", "demo-college", STUDENT, in_cse=True),
    DemoUser("student8", "Arjun Mehta", "student", "demo-college", STUDENT, in_cse=True),
)


def _doc(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "content": list(blocks)}


def _p(text_: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": text_}]}


def _h(text_: str) -> dict[str, Any]:
    return {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": text_}]}


def _code(source: str) -> dict[str, Any]:
    return {
        "type": "codeBlock",
        "attrs": {"language": "python"},
        "content": [{"type": "text", "text": source}],
    }


VARIABLES = _doc(
    _h("Variables and types"),
    _p("A variable is a name for a value. Python works out the type from the value."),
    _code('name = "Priya"\nmarks = 87\naverage = marks / 100\nprint(type(name), type(marks))'),
    _p("Convert between types with int(), float() and str()."),
    _code('age = int("19")\nprint(f"Next year you will be {age + 1}")'),
)
LOOPS = _doc(
    _h("Loops"),
    _p("A for loop repeats code for every item of a sequence; range() makes number sequences."),
    _code("for i in range(1, 6):\n    print(i, i * i)"),
    _p("A while loop repeats while a condition holds."),
    _code("n = 10\nwhile n > 0:\n    n -= 3\nprint(n)"),
)
FIZZBUZZ = _doc(
    _p(
        "Print the numbers from 1 to 100. For multiples of 3 print Fizz, for multiples of 5 "
        "print Buzz, and for multiples of both print FizzBuzz."
    ),
    _p("Submit your program as text, or upload a PDF or screenshot of it."),
)
LESSONS: tuple[tuple[int, str, LessonType], ...] = (
    (0, "Why Python", LessonType.VIDEO),
    (0, "Variables and types", LessonType.NOTES),
    (0, "Cheat sheet", LessonType.PDF),
    (1, "Loops", LessonType.NOTES),
    (1, "Functions", LessonType.VIDEO),
    (1, "Mini project: FizzBuzz", LessonType.ASSIGNMENT),
)
ANSWERS = {
    "student": "for i in range(1, 101):\n    print(i)",
    "student2": (
        "for i in range(1, 101):\n"
        "    if i % 15 == 0:\n        print('FizzBuzz')\n"
        "    elif i % 3 == 0:\n        print('Fizz')\n"
        "    elif i % 5 == 0:\n        print('Buzz')\n"
        "    else:\n        print(i)"
    ),
    "student3": "for i in range(1, 101):\n    print('Fizz' if i % 3 == 0 else i)",
}


# ============================================================================ credentials


def credentials_path(settings: Settings) -> Path:
    return Path(os.environ.get("DEMO_CREDENTIALS_FILE") or ".secrets/demo-credentials.txt")


def generate_password() -> str:
    """20 random characters with upper, lower, digit and symbol (secrets module)."""
    alphabet = string.ascii_letters + string.digits + "!@#%^*-_=+"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(20))
        if (
            any(c.islower() for c in candidate)
            and any(c.isupper() for c in candidate)
            and any(c.isdigit() for c in candidate)
            and any(not c.isalnum() for c in candidate)
        ):
            return candidate


def read_credentials(path: Path) -> dict[str, str]:
    """email -> password from an earlier run's file (missing file: none)."""
    if not path.exists():
        return {}
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and not line.startswith("#") and "@" in parts[1]:  # noqa: PLR2004
            found[parts[1]] = parts[2]
    return found


def write_credentials(path: Path, passwords: dict[str, str], web_origin: str | None) -> None:
    """Owner-only file (0600), replaced atomically. Contains the only copy of the passwords."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# SkillifyMe demo logins, written by `make seed-demo`. Private: never commit or share",
        "# publicly. Rotate with `make seed-demo args=--rotate-passwords`.",
        f"# Sign in at {web_origin or 'the web app'}",
        "# role\temail\tpassword",
        *(f"{u.role}\t{u.email}\t{passwords[u.email]}" for u in USERS),
    ]
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    tmp.replace(path)
    path.chmod(0o600)  # also when the file already existed (no effect on Windows)


# ============================================================================ acting as a user


class InlineJobs:
    """Background jobs a service enqueues, run after the transaction commits (like the worker)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, tuple[str, ...]]] = []

    def send(self, task: str, *args: str) -> None:
        self.sent.append((task, args))


@dataclass
class Seeder:
    settings: Settings
    owner: async_sessionmaker[AsyncSession]  # operator writes (orgs, users, memberships, reset)
    app: async_sessionmaker[AsyncSession]  # the API's role: RLS applies, as for real requests
    redis: Redis
    storage: ObjectStorage
    keycloak: KeycloakAdmin
    users: dict[str, User] = field(default_factory=dict)  # email -> row
    orgs: dict[str, UUID] = field(default_factory=dict)  # slug -> id

    @asynccontextmanager
    async def acting(self, demo: DemoUser) -> AsyncIterator[RequestContext]:
        """A request context for `demo` in their org (one committed transaction)."""
        user = self.users[demo.email]
        org_id = self.orgs[demo.org] if demo.org else None
        roles = frozenset(OrgRole(r) for r in demo.org_roles)
        principal = Principal(
            user_id=user.id,
            keycloak_sub=user.keycloak_sub,
            email=user.email,
            full_name=user.full_name,
            is_platform_admin=demo.platform_admin,
            organization_id=org_id,
            roles=roles,
            memberships={org_id: roles} if org_id else {},
        )
        jobs = InlineJobs()
        async with self.app() as session, session.begin():
            await set_tenant_context(
                session,
                organization_id=org_id,
                user_id=user.id,
                is_platform_admin=demo.platform_admin,
            )
            actor = AuditActor(user.id, org_id, demo.platform_admin, None, "seed-demo")
            yield RequestContext(session, principal, actor, jobs, self.redis)


def by_local(local: str) -> DemoUser:
    return next(u for u in USERS if u.local == local)


# ============================================================================ steps


async def ensure_accounts(s: Seeder, *, rotate: bool, log: Callable[[str], None]) -> int:
    """Keycloak accounts with passwords, and their users, memberships and batch places."""
    path = credentials_path(s.settings)
    known = {} if rotate else read_credentials(path)
    ids = await s.keycloak.ensure_users([NewUser(u.email, u.full_name) for u in USERS])
    passwords: dict[str, str] = {}
    changed = 0
    for u in USERS:
        password = known.get(u.email)
        new = password is None
        if new:
            password = generate_password()
            changed += 1
        passwords[u.email] = password  # type: ignore[assignment]
        await s.keycloak.prepare_login(
            ids[u.email], full_name=u.full_name, password=password if new else None
        )
        if u.platform_admin:
            await s.keycloak.grant_realm_role(ids[u.email], "platform_admin")
    write_credentials(path, passwords, s.settings.web_origin)
    log(f"Demo logins: {len(USERS)} ({changed} new passwords) -> {path} (mode 0600)")

    async with s.owner() as session, session.begin():
        for spec in (PUBLISHER, COLLEGE):
            org = await _upsert_org(session, spec)
            s.orgs[spec.slug] = org.id
        cse = await _upsert_batch(session, await _org(session, COLLEGE.slug), "CSE 2026")
        for u in USERS:
            sub = ids[u.email]
            await session.execute(
                pg_insert(User)
                .values(
                    id=new_id(),
                    keycloak_sub=sub,
                    email=u.email,
                    full_name=u.full_name,
                    status="active",
                )
                .on_conflict_do_update(
                    constraint="uq_users_keycloak_sub",
                    set_={"email": u.email, "full_name": u.full_name, "status": "active"},
                )
            )
            row = (await session.scalars(select(User).where(User.keycloak_sub == sub))).one()
            s.users[u.email] = row
            for role in u.org_roles:
                await session.execute(
                    pg_insert(Membership)
                    .values(
                        id=new_id(), user_id=row.id, organization_id=s.orgs[u.org or ""], role=role
                    )
                    .on_conflict_do_nothing(constraint="uq_memberships_user_org_role")
                )
            if u.in_cse:
                await session.execute(
                    pg_insert(BatchMember)
                    .values(
                        id=new_id(),
                        batch_id=cse.id,
                        organization_id=cse.organization_id,
                        user_id=row.id,
                    )
                    .on_conflict_do_nothing(constraint="uq_batch_members_batch_user")
                )
        s.orgs["cse"] = cse.id
    return changed


async def _org(session: AsyncSession, slug: str) -> Organization:
    return (await session.scalars(select(Organization).where(Organization.slug == slug))).one()


async def ensure_course(s: Seeder, log: Callable[[str], None]) -> tuple[UUID, list[UUID]]:
    """The published course (built once), granted to Demo College and assigned to CSE 2026."""
    author, admin = by_local("author"), by_local("admin")
    async with s.owner() as session:
        course_id = await session.scalar(
            select(Course.id).where(
                Course.organization_id == s.orgs["skillifyme"], Course.slug == COURSE_SLUG
            )
        )
    if course_id is None:
        course_id = await build_course(s)
        log("Built and published Python Foundations 1.0")
    async with s.acting(author) as ctx:
        version = await courses.resolve_version(ctx.session, course_id, 1)
        assert version is not None  # noqa: S101 - built and published above
        lesson_ids = [UUID(lesson["id"]) for _m, lesson in courses.outline_lessons(version)]
        await courses.create_assignments(
            ctx, course_id, AssignmentCreate(organization_id=s.orgs["demo-college"])
        )
    async with s.acting(admin) as ctx:
        await courses.create_assignments(
            ctx, course_id, AssignmentCreate(batch_ids=[s.orgs["cse"]])
        )
    # Enroll the batch (what the worker does after an assignment); safe to repeat.
    await run_reconcile_course_org(s.app, course_id, s.orgs["demo-college"])
    return course_id, lesson_ids


async def build_course(s: Seeder) -> UUID:
    author = by_local("author")
    video_bytes = (ASSETS / "intro.mp4").read_bytes()
    pdf_bytes = (ASSETS / "cheat-sheet.pdf").read_bytes()
    async with s.acting(author) as ctx:
        course = await courses.create_course(
            ctx,
            CourseCreate(
                title="Python Foundations",
                slug=COURSE_SLUG,
                description="Start programming in Python: values, loops and functions, with a "
                "small project at the end.",
            ),
        )
        modules = [
            (await courses.create_module(ctx, course.id, title, None)).id
            for title in ("Getting started", "Control flow")
        ]
        lesson_ids: list[UUID] = []
        for module_index, title, lesson_type in LESSONS:
            content: dict[str, Any] = {}
            if lesson_type == LessonType.VIDEO:
                video = await media.import_local_video(
                    ctx, s.storage, s.settings, title=title, data=video_bytes
                )
                content = {"video_asset_id": str(video.id)}
            elif lesson_type == LessonType.PDF:
                file = await media.import_file(
                    ctx,
                    s.storage,
                    s.settings,
                    kind="pdf",
                    file_name="python-cheat-sheet.pdf",
                    content_type="application/pdf",
                    data=pdf_bytes,
                )
                content = {"file_id": str(file.id)}
            elif lesson_type == LessonType.NOTES:
                content = {"doc": VARIABLES if title.startswith("Variables") else LOOPS}
            lesson = await courses.create_lesson(
                ctx,
                course.id,
                modules[module_index],
                LessonCreate(title=title, lesson_type=lesson_type, content=content),
                None,
            )
            lesson_ids.append(lesson.id)
            if lesson_type == LessonType.ASSIGNMENT:
                draft = await courses.editable_lesson(ctx, course.id, lesson.id)
                await assignments.put_draft(
                    ctx,
                    course.id,
                    lesson.id,
                    AssignmentUpsert(
                        title="FizzBuzz",
                        instructions=FIZZBUZZ,
                        max_marks=10,
                        submission_kinds=["text", "file"],
                    ),
                    draft.course_revision,
                )
        await courses.publish(ctx, course.id, PublishRequest(release_type=ReleaseType.MAJOR))
    return course.id


async def ensure_progress(
    s: Seeder, course_id: UUID, lesson_ids: list[UUID], log: Callable[[str], None]
) -> None:
    """Each student's planned progress; steps already done are skipped (idempotent)."""
    pdf_ttl = s.settings.file_download_ttl_seconds
    submitted: dict[str, UUID] = {}
    for u in USERS:
        if not u.in_cse:
            continue
        async with s.acting(u) as ctx:
            mine, _ = await enrollments.list_my_enrollments(
                ctx, CursorParams(limit=1), course_id=course_id
            )
            if not mine:
                log(f"  {u.full_name}: not enrolled yet (skipped)")
                continue
            eid = mine[0].id
            done = {
                p.lesson_id
                for p in (await enrollments.get_enrollment(ctx, eid)).progress
                if p.status == "completed"
            }
            await enrollments.visit_lesson(ctx, eid, lesson_ids[0])  # "active today"
            for index in u.lessons:
                lesson_id = lesson_ids[index]
                lesson_type = LESSONS[index][2]
                if lesson_type == LessonType.ASSIGNMENT:
                    state = await assignments.get_student_assignment(
                        ctx, s.storage, s.settings, eid, lesson_id
                    )
                    if state.submission is None:
                        await assignments.submit(
                            ctx,
                            s.storage,
                            s.settings,
                            enrollment_id=eid,
                            lesson_id=lesson_id,
                            body=SubmitBody(
                                submission=SubmitText(kind="text", text=ANSWERS[u.local])
                            ),
                            if_match=0,
                        )
                    sub = await assignments.get_student_assignment(
                        ctx, s.storage, s.settings, eid, lesson_id
                    )
                    if sub.submission and sub.submission.status == "submitted" and u.grade:
                        submitted[u.local] = sub.submission.id
                    continue
                if lesson_id in done:
                    continue
                await enrollments.visit_lesson(ctx, eid, lesson_id)
                if lesson_type == LessonType.VIDEO:
                    await enrollments.mark_video_watched(ctx.session, eid, lesson_id)
                elif lesson_type == LessonType.PDF:
                    await enrollments.open_pdf(ctx, s.storage, eid, lesson_id, pdf_ttl)
                    await enrollments.complete_lesson(ctx, eid, lesson_id)
                else:
                    await enrollments.complete_lesson(ctx, eid, lesson_id)
    instructor = by_local("instructor")
    for local, submission_id in submitted.items():
        async with s.acting(instructor) as ctx:
            detail = await assignments.get_submission(ctx, s.storage, s.settings, submission_id)
            await assignments.grade(
                ctx,
                s.storage,
                s.settings,
                submission_id=submission_id,
                body=GradeBody(
                    score=by_local(local).grade or "0",  # type: ignore[arg-type]
                    feedback="Correct and tidy. Try a version with a function next.",
                ),
                if_match=detail.submission.revision,
            )
    log("Progress: 8 CSE students (0%, partial, complete); 3 submissions, 1 graded")


async def reset_progress(s: Seeder, course_id: UUID) -> None:
    """Operator reset: the demo students' progress and submissions in the demo course."""
    student_ids = [s.users[u.email].id for u in USERS if u.in_cse]
    async with s.owner() as session, session.begin():
        enrollment_ids = list(
            await session.scalars(
                select(Enrollment.id).where(
                    Enrollment.course_id == course_id, Enrollment.user_id.in_(student_ids)
                )
            )
        )
        submission_ids = select(AssignmentSubmission.id).where(
            AssignmentSubmission.enrollment_id.in_(enrollment_ids)
        )
        await session.execute(
            delete(AssignmentGrade).where(AssignmentGrade.submission_id.in_(submission_ids))
        )
        await session.execute(
            delete(AssignmentSubmission).where(
                AssignmentSubmission.enrollment_id.in_(enrollment_ids)
            )
        )
        await session.execute(
            delete(LessonProgress).where(LessonProgress.enrollment_id.in_(enrollment_ids))
        )
        await session.execute(
            update(Enrollment)
            .where(Enrollment.id.in_(enrollment_ids))
            .values(
                progress_percent=0,
                completed_at=None,
                last_lesson_id=None,
                last_accessed_at=None,
                updated_at=func.now(),
            )
        )


# ============================================================================ entry point


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the demo data (idempotent).")
    parser.add_argument("--reset", action="store_true", help="restore the demo progress (local)")
    parser.add_argument(
        "--rotate-passwords", action="store_true", help="new passwords for every demo login"
    )
    parser.add_argument(
        NOT_LOCAL_FLAG,
        dest="not_local_ok",
        action="store_true",
        help="allow --reset / --rotate-passwords outside ENVIRONMENT=local",
    )
    return parser.parse_args(argv)


def refusal(settings: Settings, args: argparse.Namespace) -> str | None:
    """Why this run must not proceed, or None."""
    if settings.environment == "production":
        return "Refusing to seed demo data in production."
    risky = args.reset or args.rotate_passwords
    if risky and settings.environment != "local" and not args.not_local_ok:
        return (
            f"--reset and --rotate-passwords change data people may be using; ENVIRONMENT is "
            f"{settings.environment!r}, not 'local'. Add {NOT_LOCAL_FLAG} if you mean it."
        )
    return None


async def run(settings: Settings, args: argparse.Namespace, log: Callable[[str], None]) -> None:
    owner_engine = create_async_engine(settings.migration_database_url.get_secret_value())
    app_engine = create_engine(settings)
    redis = create_redis(settings)
    try:
        async with httpx.AsyncClient() as http:
            seeder = Seeder(
                settings=settings,
                owner=async_sessionmaker(owner_engine, expire_on_commit=False),
                app=create_sessionmaker(app_engine),
                redis=redis,
                storage=ObjectStorage(settings),
                keycloak=KeycloakAdmin(http, settings),
            )
            async with seeder.owner() as session:
                await session.execute(text("SELECT 1"))
            await ensure_accounts(seeder, rotate=args.rotate_passwords, log=log)
            course_id, lesson_ids = await ensure_course(seeder, log)
            if args.reset:
                await reset_progress(seeder, course_id)
                log("Reset the demo students' progress")
            await ensure_progress(seeder, course_id, lesson_ids, log)
    finally:
        await redis.aclose()
        await app_engine.dispose()
        await owner_engine.dispose()


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if reason := refusal(settings, args):
        sys.exit(reason)
    asyncio.run(run(settings, args, print))


if __name__ == "__main__":
    main()
