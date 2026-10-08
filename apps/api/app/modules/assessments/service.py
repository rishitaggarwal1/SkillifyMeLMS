"""Explicit student projections shared by future authoring/runtime endpoints.

SQL reveal and response validation independently enforce answer secrecy. Only
this module's repository touches assessment tables; other modules call services.
"""

import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, UnprocessableError
from app.core.pagination import CursorParams
from app.db.base import new_id
from app.db.outbox import add_outbox_event
from app.db.session import run_after_commit
from app.modules.assessments import dashboard
from app.modules.assessments.models import (
    Question,
    QuestionBank,
    QuestionKey,
    Quiz,
    QuizAttempt,
    QuizVersion,
    QuizVersionKey,
    QuizVersionQuestion,
)
from app.modules.assessments.repository import (
    MAX_PUBLICATION_POOL,
    AuthorRepository,
    QuestionData,
    RuntimeRepository,
    permitted_solutions,
)
from app.modules.assessments.schemas import (
    ActiveQuestion,
    AnswerBatch,
    AnswersResult,
    AttemptDetail,
    AttemptSummary,
    BankCreate,
    BankOut,
    BankPatch,
    BankSelection,
    ExplanationsResult,
    ManualSelection,
    PublishedQuiz,
    QuestionBody,
    QuestionOut,
    QuestionPatch,
    QuestionPrompt,
    QuestionSkills,
    QuizBody,
    QuizOut,
    ResultContext,
    RevealMode,
    RevealTiming,
    SavedAnswer,
    ScoreResult,
    SelectedQuestionSummary,
    StudentQuiz,
)
from app.modules.audit import service as audit
from app.modules.courses import service as courses
from app.modules.courses.schemas import StructuralChange
from app.modules.enrollments import service as enrollments
from app.modules.identity.authz import Permission, require_org_permission
from app.modules.identity.dependencies import RequestContext
from app.modules.skills import service as skills


def active_question(
    question: Mapping[str, Any], *, saved_answer: SavedAnswer | None = None
) -> ActiveQuestion:
    """Allow-list fields, including typed nested options; discard privileged source fields."""
    return ActiveQuestion.model_validate(
        {
            "id": question["id"],
            "question_type": question["question_type"],
            "prompt": question["prompt"],
            "options": question["options"],
            "marks": question["marks"],
            "skill_ids": question.get("skill_ids", []),
            "saved_answer": saved_answer,
        }
    )


def student_result(
    *,
    attempt_id: UUID,
    context: ResultContext,
    score: Decimal,
    max_marks: Decimal,
    pass_marks: Decimal,
    solutions: Sequence[Mapping[str, Any]],
) -> ScoreResult | AnswersResult | ExplanationsResult:
    """Privileged fixtures remain safe even without SQL filtering the solution input."""
    common = {
        "attempt_id": attempt_id,
        "context": context,
        "score": score,
        "max_marks": max_marks,
        "pass_marks": pass_marks,
        "passed": score >= pass_marks,
    }
    if not context.solutions_allowed():
        return ScoreResult.model_validate(common)
    safe = [
        {"question_id": row["question_id"], "answer_key": row["answer_key"]} for row in solutions
    ]
    if context.configured_reveal_mode == "correct_answers":
        return AnswersResult.model_validate({**common, "solutions": safe})
    explained = [
        {**row, "explanation": original["explanation"]}
        for row, original in zip(safe, solutions, strict=True)
    ]
    return ExplanationsResult.model_validate({**common, "solutions": explained})


def _bank_out(row: QuestionBank) -> BankOut:
    return BankOut.model_validate({k: getattr(row, k) for k in BankOut.model_fields})


async def _bank(ctx: RequestContext, bank_id: UUID) -> QuestionBank:
    org = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    row = await AuthorRepository(ctx.session).bank(bank_id)
    if row is None or row.organization_id != org:
        raise NotFoundError()
    return row


async def _bump(ctx: RequestContext, bank: QuestionBank, revision: int) -> QuestionBank:
    row = await AuthorRepository(ctx.session).bump_bank(bank.id, revision)
    if row is None:
        raise ConflictError("The bank changed or is archived.", code="revision_conflict")
    return row


async def _audit(ctx: RequestContext, action: str, target: UUID, data: dict[str, Any]) -> None:
    # No answer keys, explanations or their hashes enter audits/outbox payloads.
    await audit.record(
        ctx.session,
        ctx.actor,
        action=action,
        target_type="assessment",
        target_id=target,
        after=data,
    )


async def list_banks(
    ctx: RequestContext, params: CursorParams, query: str | None
) -> tuple[list[BankOut], str | None]:
    org = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    rows, cursor = await AuthorRepository(ctx.session).banks(org, params, query)
    return [_bank_out(row) for row in rows], cursor


async def get_bank(ctx: RequestContext, bank_id: UUID) -> BankOut:
    return _bank_out(await _bank(ctx, bank_id))


async def create_bank(ctx: RequestContext, body: BankCreate, revision: int) -> BankOut:
    org = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    if revision != 0:
        raise ConflictError("New banks require revision 0.", code="revision_conflict")
    bank = QuestionBank(id=new_id(), organization_id=org, **body.model_dump())
    await AuthorRepository(ctx.session).save(bank)
    await _audit(ctx, "assessment.bank_created", bank.id, {"name": bank.name})
    return _bank_out(bank)


async def update_bank(
    ctx: RequestContext, bank_id: UUID, body: BankPatch, revision: int
) -> BankOut:
    bank = await _bump(ctx, await _bank(ctx, bank_id), revision)
    for name, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            raise UnprocessableError("Bank fields cannot be null.")
        setattr(bank, name, value)
    await AuthorRepository(ctx.session).save(bank)
    await _audit(ctx, "assessment.bank_updated", bank.id, {"revision": bank.revision})
    return _bank_out(bank)


async def archive_bank(ctx: RequestContext, bank_id: UUID, revision: int) -> None:
    bank = await _bump(ctx, await _bank(ctx, bank_id), revision)
    bank.archived_at = datetime.now(UTC)
    await AuthorRepository(ctx.session).save(bank)
    await _audit(ctx, "assessment.bank_archived", bank.id, {"revision": bank.revision})


async def _question(ctx: RequestContext, question_id: UUID) -> tuple[Question, QuestionBank]:
    require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    q = await AuthorRepository(ctx.session).question(question_id)
    if q is None:
        raise NotFoundError()
    return q, await _bank(ctx, q.bank_id)


async def _question_out(
    repo: AuthorRepository, rows: Sequence[Question], bank: QuestionBank
) -> list[QuestionOut]:
    ids = [q.id for q in rows]
    keys, tags = await repo.keys(ids), await repo.tags(ids)
    return [
        QuestionOut.model_validate(
            {
                "id": q.id,
                "bank_id": q.bank_id,
                "bank_revision": bank.revision,
                "revision": q.revision,
                "archived_at": q.archived_at,
                "question_type": q.question_type,
                "prompt": q.prompt,
                "options": q.options,
                "answer_key": keys[q.id].answer_key,
                "explanation": keys[q.id].explanation,
                "skill_ids": tags.get(q.id, []),
            }
        )
        for q in rows
    ]


async def get_question(ctx: RequestContext, question_id: UUID) -> QuestionOut:
    q, bank = await _question(ctx, question_id)
    return (await _question_out(AuthorRepository(ctx.session), [q], bank))[0]


async def list_questions(
    ctx: RequestContext,
    bank_id: UUID,
    params: CursorParams,
    *,
    query: str | None,
    kind: str | None,
    skill_ids: Sequence[UUID],
) -> tuple[list[QuestionOut], str | None]:
    bank = await _bank(ctx, bank_id)
    repo = AuthorRepository(ctx.session)
    rows, cursor = await repo.questions(bank_id, params, query, kind, skill_ids)
    return await _question_out(repo, rows, bank), cursor


async def create_question(
    ctx: RequestContext, bank_id: UUID, body: QuestionBody, revision: int
) -> QuestionOut:
    bank = await _bump(ctx, await _bank(ctx, bank_id), revision)
    repo = AuthorRepository(ctx.session)
    q = Question(
        id=new_id(),
        organization_id=bank.organization_id,
        bank_id=bank.id,
        question_type=body.question_type,
        prompt=body.prompt,
        options=[o.model_dump() for o in body.options],
    )
    await repo.save(q)
    await repo.save(
        QuestionKey(
            question_id=q.id,
            organization_id=q.organization_id,
            answer_key=body.answer_key.model_dump(mode="json"),
            explanation=body.explanation,
        )
    )
    await _audit(
        ctx, "assessment.question_created", q.id, {"bank_id": str(bank.id), "type": q.question_type}
    )
    return (await _question_out(repo, [q], bank))[0]


async def update_question(
    ctx: RequestContext, question_id: UUID, body: QuestionPatch, revision: int
) -> QuestionOut:
    q, bank = await _question(ctx, question_id)
    bank = await _bump(ctx, bank, revision)
    repo = AuthorRepository(ctx.session)
    if q.archived_at is not None:
        raise ConflictError("This question is archived.", code="question_archived")
    key = (await repo.keys([q.id]))[q.id]
    current = {
        "question_type": q.question_type,
        "prompt": q.prompt,
        "options": q.options,
        "answer_key": key.answer_key,
        "explanation": key.explanation,
    }
    try:
        merged = QuestionBody.model_validate({**current, **body.model_dump(exclude_unset=True)})
    except ValidationError as exc:
        raise UnprocessableError("The resulting question has an invalid grading rule.") from exc
    q.question_type, q.prompt = merged.question_type, merged.prompt
    q.options = [o.model_dump() for o in merged.options]
    q.revision += 1
    key.answer_key, key.explanation = merged.answer_key.model_dump(mode="json"), merged.explanation
    await repo.save(q)
    await repo.save(key)
    await _audit(
        ctx,
        "assessment.question_updated",
        q.id,
        {"revision": q.revision, "bank_revision": bank.revision},
    )
    return (await _question_out(repo, [q], bank))[0]


async def archive_question(ctx: RequestContext, question_id: UUID, revision: int) -> None:
    q, bank = await _question(ctx, question_id)
    await _bump(ctx, bank, revision)
    if q.archived_at is not None:
        raise ConflictError("This question is archived.", code="question_archived")
    q.archived_at, q.revision = datetime.now(UTC), q.revision + 1
    await AuthorRepository(ctx.session).save(q)
    await _audit(ctx, "assessment.question_archived", q.id, {"revision": q.revision})


async def set_question_skills(
    ctx: RequestContext, question_id: UUID, body: QuestionSkills, revision: int
) -> QuestionOut:
    q, bank = await _question(ctx, question_id)
    bank = await _bump(ctx, bank, revision)
    if q.archived_at is not None:
        raise ConflictError("This question is archived.", code="question_archived")
    if await skills.existing_skill_ids(ctx.session, body.skill_ids) != set(body.skill_ids):
        raise UnprocessableError("One or more skills do not exist.")
    repo = AuthorRepository(ctx.session)
    await repo.replace_tags(q, body.skill_ids)
    q.revision += 1
    await repo.save(q)
    await _audit(
        ctx,
        "assessment.question_skills_updated",
        q.id,
        {"skill_ids": [str(s) for s in body.skill_ids]},
    )
    return (await _question_out(repo, [q], bank))[0]


async def _quiz_out(session: AsyncSession, q: Quiz, revision: int) -> QuizOut:
    body = QuizBody.model_validate({k: getattr(q, k) for k in QuizBody.model_fields})
    summaries = (
        await AuthorRepository(session).selected_question_summaries(
            [entry.question_id for entry in body.selection.questions]
        )
        if isinstance(body.selection, ManualSelection)
        else []
    )
    return QuizOut(
        id=q.id,
        course_id=q.course_id,
        lesson_id=q.lesson_id,
        course_revision=revision,
        max_marks=q.max_marks,
        question_summaries=[SelectedQuestionSummary.model_validate(row) for row in summaries],
        **{k: getattr(q, k) for k in QuizBody.model_fields},
    )


async def _quiz_lesson(
    ctx: RequestContext, course_id: UUID, lesson_id: UUID
) -> courses.DraftLessonRef:
    ref = await courses.editable_lesson(ctx, course_id, lesson_id)
    if ref.lesson_type.value != "quiz":
        raise UnprocessableError("This is not a quiz lesson.")
    return ref


async def get_quiz(ctx: RequestContext, course_id: UUID, lesson_id: UUID) -> QuizOut:
    ref = await _quiz_lesson(ctx, course_id, lesson_id)
    rows = await AuthorRepository(ctx.session).quizzes([lesson_id])
    if not rows:
        raise NotFoundError("Save the quiz definition first.")
    return await _quiz_out(ctx.session, rows[0], ref.course_revision)


async def put_quiz(
    ctx: RequestContext, course_id: UUID, lesson_id: UUID, body: QuizBody, revision: int
) -> QuizOut:
    ref = await _quiz_lesson(ctx, course_id, lesson_id)
    repo = AuthorRepository(ctx.session)
    # Course before bank locks everywhere, including publication.
    existing = await repo.quizzes([lesson_id])
    quiz_id = existing[0].id if existing else new_id()
    next_revision = await courses.edit_lesson_content(
        ctx, course_id, lesson_id, {"quiz_id": str(quiz_id)}, revision
    )
    await _lock_selection(repo, [body])
    values = body.model_dump(mode="json")
    values.update(selection_mode=body.selection.mode, max_marks=body.maximum())
    q = (
        existing[0]
        if existing
        else Quiz(
            id=quiz_id,
            organization_id=ref.organization_id,
            course_id=course_id,
            lesson_id=lesson_id,
        )
    )
    for name, value in values.items():
        setattr(q, name, value)
    await repo.save(q)
    if lesson_id not in await _prepare(ctx.session, [lesson_id]):
        raise UnprocessableError(
            "The quiz needs accessible, active questions and a sufficient bank pool."
        )
    await _audit(
        ctx,
        "assessment.quiz_updated",
        q.id,
        {"lesson_id": str(lesson_id), "course_revision": next_revision},
    )
    return await _quiz_out(ctx.session, q, next_revision)


async def _lock_selection(repo: AuthorRepository, bodies: Sequence[QuizBody]) -> None:
    manual = [
        q.question_id
        for b in bodies
        if isinstance(b.selection, ManualSelection)
        for q in b.selection.questions
    ]
    banks = [b.selection.bank_id for b in bodies if isinstance(b.selection, BankSelection)]
    banks.extend(await repo.referenced_banks(manual))
    await repo.lock_banks(sorted(set(banks)))


@dataclass(frozen=True)
class PreparedQuiz:
    quiz: Quiz
    questions: list[QuestionData]
    tags: dict[UUID, list[UUID]]
    marks: dict[UUID, Decimal]

    def structure(self) -> dict[str, Any]:
        q = self.quiz
        selection = QuizBody.model_validate(
            {k: getattr(q, k) for k in QuizBody.model_fields}
        ).selection
        return {
            "quiz_id": str(q.id),
            "selection_mode": q.selection_mode,
            "bank_id": str(selection.bank_id) if isinstance(selection, BankSelection) else None,
            "skill_filter": sorted(str(s) for s in selection.skill_ids)
            if isinstance(selection, BankSelection)
            else [],
            "draw_count": selection.draw_count
            if isinstance(selection, BankSelection)
            else len(self.questions),
            "max_marks": str(q.max_marks),
            "pass_marks": str(q.pass_marks),
            "time_limit_seconds": q.time_limit_seconds,
            "attempts_allowed": q.attempts_allowed,
            "questions": sorted(
                [
                    {
                        "id": str(question.id),
                        "type": question.question_type,
                        "option_ids": sorted(o["id"] for o in question.options),
                        "marks": f"{self.marks[question.id]:.2f}",
                    }
                    for question, _key, _bank in self.questions
                ],
                key=lambda v: v["id"],
            ),
        }


async def _prepare(session: AsyncSession, lesson_ids: Sequence[UUID]) -> dict[UUID, PreparedQuiz]:
    repo = AuthorRepository(session)
    quizzes = await repo.quizzes(lesson_ids)
    bodies = {
        q.lesson_id: QuizBody.model_validate({k: getattr(q, k) for k in QuizBody.model_fields})
        for q in quizzes
    }
    manual = [
        q.question_id
        for b in bodies.values()
        if isinstance(b.selection, ManualSelection)
        for q in b.selection.questions
    ]
    bank_filters = [
        (b.selection.bank_id, b.selection.skill_ids)
        for b in bodies.values()
        if isinstance(b.selection, BankSelection)
    ]
    data = {
        q.id: (q, k, bank)
        for q, k, bank in [*await repo.question_data(manual), *await repo.pool_data(bank_filters)]
    }
    tags = await repo.tags(list(data))
    result: dict[UUID, PreparedQuiz] = {}
    for quiz in quizzes:
        selection = bodies[quiz.lesson_id].selection
        if isinstance(selection, ManualSelection):
            ids = [q.question_id for q in selection.questions]
            marks = {q.question_id: q.marks for q in selection.questions}
            if any(qid not in data for qid in ids):
                continue
        else:
            ids = sorted(
                qid
                for qid, (q, _key, _bank) in data.items()
                if q.bank_id == selection.bank_id
                and set(selection.skill_ids) <= set(tags.get(qid, []))
            )
            if not selection.draw_count <= len(ids) <= MAX_PUBLICATION_POOL:
                continue
            marks = dict.fromkeys(ids, selection.marks_per_question)
        if any(data[qid][0].organization_id != quiz.organization_id for qid in ids):
            continue
        # Revalidate legacy/imported grading rules before copying them into a publication.
        try:
            for qid in ids:
                q, key, _bank = data[qid]
                QuestionBody.model_validate(
                    {
                        "question_type": q.question_type,
                        "prompt": q.prompt,
                        "options": q.options,
                        "answer_key": key.answer_key,
                        "explanation": key.explanation,
                    }
                )
        except ValidationError:
            continue
        result[quiz.lesson_id] = PreparedQuiz(quiz, [data[qid] for qid in ids], tags, marks)
    return result


def _canonical_key(raw: dict[str, Any]) -> dict[str, Any]:
    case = raw.get("case_sensitive", False)
    accepted = [unicodedata.normalize("NFC", s).strip() for s in raw.get("accepted_answers", [])]
    return {
        "correct_option_ids": sorted(raw.get("correct_option_ids", [])),
        "accepted_answers": sorted(accepted if case else [s.casefold() for s in accepted]),
        "case_sensitive": case,
    }


class QuizContentSource:
    async def lock(self, session: AsyncSession, lesson_ids: Sequence[UUID]) -> None:
        repo = AuthorRepository(session)
        rows = await repo.quizzes(lesson_ids)
        bodies = [
            QuizBody.model_validate({k: getattr(q, k) for k in QuizBody.model_fields}) for q in rows
        ]
        await _lock_selection(repo, bodies)

    async def published(
        self, session: AsyncSession, lesson_ids: Sequence[UUID]
    ) -> dict[UUID, dict[str, Any]]:
        return {
            lid: {"quiz_id": str(p.quiz.id)}
            for lid, p in (await _prepare(session, lesson_ids)).items()
        }

    async def structural(
        self, session: AsyncSession, lesson_ids: Sequence[UUID]
    ) -> dict[UUID, dict[str, Any]]:
        return {lid: p.structure() for lid, p in (await _prepare(session, lesson_ids)).items()}

    async def changes(
        self, session: AsyncSession, previous_version_id: UUID, lesson_ids: Sequence[UUID]
    ) -> list[StructuralChange]:
        prepared = await _prepare(session, lesson_ids)
        repo = AuthorRepository(session)
        previous = await repo.published_versions(previous_version_id, lesson_ids)
        questions = await repo.published_questions([v.id for v in previous])
        keys = await repo.published_keys([q.id for q in questions])
        old: dict[UUID, dict[UUID, dict[str, Any]]] = {v.lesson_id: {} for v in previous}
        lesson_by_version = {v.id: v.lesson_id for v in previous}
        for q in questions:
            if q.id in keys:
                old[lesson_by_version[q.quiz_version_id]][q.question_id] = _canonical_key(
                    keys[q.id].answer_key
                )
        changed = []
        for lid, p in prepared.items():
            if lid not in old:
                continue
            current = {q.id: _canonical_key(k.answer_key) for q, k, _bank in p.questions}
            if any(old[lid][qid] != current[qid] for qid in old[lid].keys() & current.keys()):
                changed.append(lid)
        return (
            [StructuralChange(code="quiz_grading_changed", lesson_ids=changed)] if changed else []
        )

    async def publish(
        self, session: AsyncSession, version_id: UUID, major: int, lesson_ids: Sequence[UUID]
    ) -> None:
        prepared = await _prepare(session, lesson_ids)
        published_at = await AuthorRepository(session).database_time()
        versions = []
        questions = []
        keys = []
        fields = (
            "selection_mode",
            "selection",
            "max_marks",
            "pass_marks",
            "time_limit_seconds",
            "attempts_allowed",
            "randomize_order",
            "reveal_mode",
            "reveal_timing",
        )
        for p in prepared.values():
            q = p.quiz
            version = QuizVersion(
                id=new_id(),
                organization_id=q.organization_id,
                quiz_id=q.id,
                course_id=q.course_id,
                course_version_id=version_id,
                lesson_id=q.lesson_id,
                major_version=major,
                title=q.title,
                # A STABLE readability helper cannot see this row inside INSERT RETURNING.
                # Supply the DB timestamp to avoid implicit RETURNING; later reads use RLS.
                published_at=published_at,
                **{f: getattr(q, f) for f in fields},
            )
            versions.append(version)
            for position, (question, key, _bank) in enumerate(p.questions):
                published = QuizVersionQuestion(
                    id=new_id(),
                    organization_id=q.organization_id,
                    quiz_version_id=version.id,
                    question_id=question.id,
                    question_type=question.question_type,
                    prompt=question.prompt,
                    options=question.options,
                    skill_ids=p.tags.get(question.id, []),
                    marks=p.marks[question.id],
                    position=position,
                )
                questions.append(published)
                keys.append(
                    QuizVersionKey(
                        question_id=published.id,
                        organization_id=q.organization_id,
                        answer_key=key.answer_key,
                        explanation=key.explanation,
                    )
                )
        # Explicit dependency order, batched INSERTs (no ORM relationships or per-question flush).
        session.add_all(versions)
        await session.flush()
        session.add_all(questions)
        await session.flush()
        session.add_all(keys)
        await session.flush()


async def get_published_quiz(
    ctx: RequestContext, course_id: UUID, version_id: UUID, lesson_id: UUID
) -> PublishedQuiz:
    # This is a staff preview. Students receive selected questions only through attempt APIs.
    require_org_permission(ctx.principal, Permission.COURSE_READ)
    await courses.get_version(ctx, course_id, version_id)
    repo = AuthorRepository(ctx.session)
    rows = await repo.published_versions(version_id, [lesson_id])
    if not rows or rows[0].course_id != course_id:
        raise NotFoundError()
    v = rows[0]
    public = await repo.published_questions([v.id])
    return PublishedQuiz.model_validate(
        {
            "id": v.id,
            "quiz_id": v.quiz_id,
            "course_version_id": v.course_version_id,
            "lesson_id": lesson_id,
            "title": v.title,
            "selection_mode": v.selection_mode,
            "max_marks": v.max_marks,
            "pass_marks": v.pass_marks,
            "time_limit_seconds": v.time_limit_seconds,
            "attempts_allowed": v.attempts_allowed,
            "randomize_order": v.randomize_order,
            "reveal_mode": v.reveal_mode,
            "reveal_timing": v.reveal_timing,
            "questions": [
                QuestionPrompt.model_validate(
                    {
                        "id": q.id,
                        "question_type": q.question_type,
                        "prompt": q.prompt,
                        "options": q.options,
                        "skill_ids": q.skill_ids,
                        "marks": q.marks,
                    }
                )
                for q in public
            ],
        }
    )


# Runtime APIs use only typed public prompts and student-owned work.
def _student(ctx: RequestContext) -> None:
    if "student" not in ctx.principal.roles:
        raise PermissionDeniedError("Quiz execution requires the student role.")


def _runtime_error(result: Mapping[str, Any]) -> None:
    code = result.get("error")
    if not code:
        return
    if code == "not_found":
        raise NotFoundError("Quiz attempt not found.")
    if code in {"invalid_answers", "quiz_not_ready"}:
        raise UnprocessableError("Invalid quiz answer batch or definition.", code=code)
    raise ConflictError("The quiz attempt changed or cannot accept this operation.", code=code)


def _summary(a: QuizAttempt) -> AttemptSummary:
    return AttemptSummary.model_validate({k: getattr(a, k) for k in AttemptSummary.model_fields})


async def _own_attempt(ctx: RequestContext, attempt_id: UUID) -> QuizAttempt:
    a = await RuntimeRepository(ctx.session).attempt(attempt_id)
    if a is None or a.user_id != ctx.principal.user_id:
        raise NotFoundError("Quiz attempt not found.")
    _student(ctx)
    current = await enrollments.student_lesson(ctx, a.enrollment_id, a.lesson_id)
    if current.version.major != a.major_version or current.lesson.lesson_type != "quiz":
        raise NotFoundError("Quiz attempt not found.")
    return a


async def get_student_quiz(
    ctx: RequestContext, enrollment_id: UUID, lesson_id: UUID
) -> StudentQuiz:
    current = await enrollments.student_lesson(ctx, enrollment_id, lesson_id)
    _student(ctx)
    if current.lesson.lesson_type != "quiz":
        raise NotFoundError("Quiz not found.")
    repo = RuntimeRepository(ctx.session)
    q = await repo.version(current.version.id, lesson_id)
    if q is None:
        raise NotFoundError("Quiz not found.")
    used, active = await repo.counts(enrollment_id, lesson_id, current.version.major)
    return StudentQuiz(
        quiz_id=q.quiz_id,
        quiz_version_id=q.id,
        title=q.title,
        time_limit_seconds=q.time_limit_seconds,
        max_marks=q.max_marks,
        pass_marks=q.pass_marks,
        attempts_allowed=q.attempts_allowed,
        attempts_used=used,
        attempts_remaining=max(0, q.attempts_allowed - used),
        revision=used,
        active_attempt_id=active,
        reveal_mode=cast(RevealMode, q.reveal_mode),
        reveal_timing=cast(RevealTiming, q.reveal_timing),
        server_now=await repo.now(),
    )


async def get_attempt(ctx: RequestContext, attempt_id: UUID) -> AttemptDetail:
    a = await _own_attempt(ctx, attempt_id)
    repo = RuntimeRepository(ctx.session)
    answers = await repo.answers(a.id)
    questions = await repo.questions(a)
    return AttemptDetail(
        **_summary(a).model_dump(),
        server_now=await repo.now(),
        questions=[
            active_question(
                {
                    k: getattr(q, k)
                    for k in ("id", "question_type", "prompt", "options", "marks", "skill_ids")
                },
                saved_answer=SavedAnswer.model_validate(answers[q.id]) if q.id in answers else None,
            )
            for q in questions
        ],
    )


async def list_attempts(
    ctx: RequestContext, enrollment_id: UUID, lesson_id: UUID, params: CursorParams
) -> tuple[list[AttemptSummary], str | None]:
    rules = await get_student_quiz(ctx, enrollment_id, lesson_id)
    current = await enrollments.student_lesson(ctx, enrollment_id, lesson_id)
    rows, cursor = await RuntimeRepository(ctx.session).history(
        enrollment_id, lesson_id, current.version.major, params
    )
    del rules
    return [_summary(a) for a in rows], cursor


async def start_attempt(
    ctx: RequestContext, enrollment_id: UUID, lesson_id: UUID, revision: int
) -> AttemptDetail:
    q = await get_student_quiz(ctx, enrollment_id, lesson_id)
    attempt_id = new_id()
    result = await RuntimeRepository(ctx.session).start(
        enrollment_id, lesson_id, q.quiz_version_id, revision, attempt_id
    )
    _runtime_error(result)
    # The immediate expiry task schedules itself for the database deadline. Beat recovers lost jobs.
    org = ctx.principal.organization_id
    run_after_commit(
        ctx.session, lambda: ctx.jobs.send("assessments.expire_attempt", str(attempt_id), str(org))
    )
    return await get_attempt(ctx, attempt_id)


def _answer_payload(body: AnswerBatch) -> str:
    return json.dumps([{**a.model_dump(mode="json"), "id": str(new_id())} for a in body.answers])


async def save_answers(
    ctx: RequestContext, attempt_id: UUID, body: AnswerBatch, revision: int
) -> AttemptDetail:
    await _own_attempt(ctx, attempt_id)
    result = await RuntimeRepository(ctx.session).mutate(
        attempt_id, revision, _answer_payload(body), submit=False
    )
    _runtime_error(result)
    return await get_attempt(ctx, attempt_id)


async def _record_submission(session: AsyncSession, a: QuizAttempt, reason: str) -> None:
    q = await RuntimeRepository(session).rules(a)
    assert a.score is not None  # noqa: S101 - submitted constraint
    add_outbox_event(
        session,
        organization_id=a.organization_id,
        aggregate_type="enrollment",
        aggregate_id=a.enrollment_id,
        event_type="quiz_attempt_submitted",
        payload={
            "attempt_id": str(a.id),
            "quiz_id": str(q.quiz_id),
            "quiz_version_id": str(q.id),
            "enrollment_id": str(a.enrollment_id),
            "user_id": str(a.user_id),
            "course_id": str(a.course_id),
            "lesson_id": str(a.lesson_id),
            "version_id": str(q.course_version_id),
            "attempt_number": a.attempt_number,
            "score": format(a.score, ".2f"),
            "max_marks": format(a.max_marks, ".2f"),
            "pass_marks": format(q.pass_marks, ".2f"),
            "passed": a.passed,
            "reason": reason,
            "started_at": a.started_at.isoformat(),
            "expires_at": a.expires_at.isoformat(),
            "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
        },
        headers={"version": 1},
    )
    if a.passed:
        await enrollments.complete_passed_quiz(
            session, a.enrollment_id, a.lesson_id, a.major_version
        )


async def submit_attempt(
    ctx: RequestContext, attempt_id: UUID, body: AnswerBatch, revision: int
) -> ScoreResult | AnswersResult | ExplanationsResult:
    await _own_attempt(ctx, attempt_id)
    repo = RuntimeRepository(ctx.session)
    result = await repo.mutate(attempt_id, revision, _answer_payload(body), submit=True)
    _runtime_error(result)
    if result["newly"]:
        a = await repo.attempt(attempt_id)
        assert a is not None  # noqa: S101 - function returned a durable attempt id
        await _record_submission(ctx.session, a, str(result["reason"]))
    return await get_result(ctx, attempt_id)


async def get_result(
    ctx: RequestContext, attempt_id: UUID
) -> ScoreResult | AnswersResult | ExplanationsResult:
    a = await _own_attempt(ctx, attempt_id)
    if a.state != "submitted":
        raise ConflictError("The attempt has not been submitted.", code="attempt_not_submitted")
    repo = RuntimeRepository(ctx.session)
    q = await repo.rules(a)
    used, active = await repo.counts(a.enrollment_id, a.lesson_id, a.major_version)
    context = ResultContext(
        state="submitted",
        configured_reveal_mode=cast(RevealMode, q.reveal_mode),
        reveal_timing=cast(RevealTiming, q.reveal_timing),
        attempts_allowed=q.attempts_allowed,
        attempts_used=used,
        has_active_attempt=active is not None,
    )
    solutions = await permitted_solutions(ctx.session, a.id)
    assert a.score is not None  # noqa: S101 - submitted constraint
    return student_result(
        attempt_id=a.id,
        context=context,
        score=a.score,
        max_marks=a.max_marks,
        pass_marks=q.pass_marks,
        solutions=solutions,
    )


async def finalize_due(session: AsyncSession, attempt_id: UUID) -> datetime | None:
    repo = RuntimeRepository(session)
    result = await repo.finalize_due(attempt_id)
    if result.get("error"):
        return None  # stale/revoked/busy is retried by the sweeper, never grants access
    if result["newly"]:
        a = await repo.attempt(attempt_id)
        assert a is not None  # noqa: S101
        await _record_submission(session, a, str(result["reason"]))
    if result.get("expires_at"):
        return datetime.fromisoformat(result["expires_at"])
    return None


class QuizCompletionSource:
    async def evidence(
        self,
        session: AsyncSession,
        enrollment_ids: Sequence[UUID],
        lesson_ids: Sequence[UUID],
        *,
        major: int,
    ) -> set[tuple[UUID, UUID]]:
        return await RuntimeRepository(session).passed_lessons(enrollment_ids, lesson_ids, major)

    async def close_major(
        self, session: AsyncSession, enrollment_ids: Sequence[UUID], major: int
    ) -> None:
        await RuntimeRepository(session).close_major(enrollment_ids, major)


# Public dashboard read interfaces; callers never import the implementation module.
dashboard_outcomes = dashboard.dashboard_outcomes
dashboard_own_results = dashboard.dashboard_own_results
