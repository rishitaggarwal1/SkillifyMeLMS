"""Trusted owner-role setup only; all security assertions use the runtime role."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.base import new_id
from app.modules.assessments.models import (
    Question,
    QuestionBank,
    QuestionKey,
    QuestionSkill,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizVersion,
    QuizVersionKey,
    QuizVersionQuestion,
)
from app.modules.assessments.tests.authoring_helpers import AuthoredQuiz, build_quiz
from app.modules.enrollments.models import Enrollment
from tests.course_api import Campus, CourseApi
from tests.factories import Factory


@pytest.fixture
async def authored(api: CourseApi, campus: Campus) -> AuthoredQuiz:
    return await build_quiz(api, campus)


@dataclass
class AssessmentWorld:
    campus: Campus
    bank: QuestionBank
    version: QuizVersion
    questions: list[QuizVersionQuestion]
    enrollment: Enrollment
    other_enrollment: Enrollment
    attempt: QuizAttempt
    other_attempt: QuizAttempt


async def save_attempt(
    factory: Factory,
    version: QuizVersion,
    enrollment: Enrollment,
    questions: list[QuizVersionQuestion],
    *,
    number: int = 1,
    state: str = "in_progress",
) -> QuizAttempt:
    now = datetime.now(UTC)
    attempt = QuizAttempt(
        id=new_id(),
        organization_id=enrollment.organization_id,
        user_id=enrollment.user_id,
        enrollment_id=enrollment.id,
        course_id=version.course_id,
        lesson_id=version.lesson_id,
        quiz_version_id=version.id,
        major_version=version.major_version,
        attempt_number=number,
        question_ids=[q.id for q in questions[:2]],
        started_at=now,
        expires_at=now + timedelta(seconds=60),
        max_marks=Decimal("2"),
        state=state,
        submitted_at=now if state == "submitted" else None,
        score=Decimal("1") if state == "submitted" else None,
        passed=True if state == "submitted" else None,
    )
    await factory._save(attempt)
    return attempt


@pytest.fixture
async def assessment_world(factory: Factory, campus: Campus) -> AssessmentWorld:
    course = await factory.course(campus.p)
    module = await factory.module(course)
    lesson = await factory.lesson(module, lesson_type="quiz")
    course_version = await factory.version(course, lesson)
    grant = await factory.assignment(course, campus.c)
    await factory.assignment(
        course, campus.c, batch=campus.cse_batch, parent=grant, by_receiver=True
    )
    await factory.add_to_batch(campus.cse_batch, campus.ece)
    enrollment = await factory.enrollment(course, campus.cse, campus.c)
    other_enrollment = await factory.enrollment(course, campus.ece, campus.c)
    bank = QuestionBank(id=new_id(), organization_id=campus.p.id, name="Python")
    await factory._save(bank)
    source = [
        Question(
            id=new_id(),
            organization_id=campus.p.id,
            bank_id=bank.id,
            question_type="mcq_single",
            prompt=f"Question {i}",
            options=[{"id": "a", "text": "Choice A"}, {"id": "b", "text": "Choice B"}],
        )
        for i in range(3)
    ]
    await factory._save(*source)
    await factory._save(
        *[
            QuestionKey(
                question_id=q.id,
                organization_id=campus.p.id,
                answer_key={"correct_option_ids": ["a"]},
                explanation="PRIVATE EXPLANATION",
            )
            for q in source
        ]
    )
    skill = await factory.skill()
    await factory._save(
        QuestionSkill(question_id=source[0].id, skill_id=skill.id, organization_id=campus.p.id)
    )
    rules = {
        "selection_mode": "bank",
        "selection": {"bank_id": str(bank.id), "count": 2},
        "max_marks": Decimal("2"),
        "pass_marks": Decimal("1"),
        "time_limit_seconds": 60,
        "attempts_allowed": 2,
        "reveal_mode": "explanations",
        "reveal_timing": "immediately",
    }
    quiz = Quiz(
        id=new_id(),
        organization_id=campus.p.id,
        course_id=course.id,
        lesson_id=lesson.id,
        title="Quiz",
        **rules,
    )
    version = QuizVersion(
        id=new_id(),
        organization_id=campus.p.id,
        course_id=course.id,
        course_version_id=course_version.id,
        quiz_id=quiz.id,
        lesson_id=lesson.id,
        major_version=1,
        title="Quiz",
        **rules,
    )
    await factory._save(quiz, version)
    questions = [
        QuizVersionQuestion(
            id=new_id(),
            organization_id=campus.p.id,
            quiz_version_id=version.id,
            question_id=q.id,
            question_type=q.question_type,
            prompt=q.prompt,
            options=q.options,
            marks=Decimal("1"),
            skill_ids=[skill.id],
            position=i,
        )
        for i, q in enumerate(source)
    ]
    await factory._save(*questions)
    await factory._save(
        *[
            QuizVersionKey(
                question_id=q.id,
                organization_id=campus.p.id,
                answer_key={"correct_option_ids": ["a"]},
                explanation="PRIVATE EXPLANATION",
            )
            for q in questions
        ]
    )
    attempt = await save_attempt(factory, version, enrollment, questions)
    other_attempt = await save_attempt(factory, version, other_enrollment, questions)
    await factory._save(
        *[
            QuizAnswer(
                id=new_id(),
                organization_id=campus.c.id,
                attempt_id=a.id,
                question_id=questions[0].id,
                answer={"option_ids": ["b"]},
            )
            for a in (attempt, other_attempt)
        ]
    )
    return AssessmentWorld(
        campus, bank, version, questions, enrollment, other_enrollment, attempt, other_attempt
    )
