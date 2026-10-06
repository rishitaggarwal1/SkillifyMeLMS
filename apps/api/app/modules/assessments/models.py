"""Assessment storage. Public prompts and private solutions are separate tables.

Published IDs survive deletion of their draft sources. Student work belongs to
the student's org, which may differ from the publisher's org.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OwnerMixin:
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )


class QuizRulesMixin:
    selection_mode: Mapped[str] = mapped_column(String(10))
    # Ordered question-id/marks pairs, or frozen bank filters and draw count.
    selection: Mapped[dict[str, Any]] = mapped_column(JSONB)
    max_marks: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    pass_marks: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    time_limit_seconds: Mapped[int] = mapped_column(Integer)
    attempts_allowed: Mapped[int] = mapped_column(Integer)
    randomize_order: Mapped[bool] = mapped_column(Boolean, server_default=false())
    reveal_mode: Mapped[str] = mapped_column(String(20), server_default="score_only")
    reveal_timing: Mapped[str] = mapped_column(String(30), server_default="immediately")


def _rules_constraints() -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint("selection_mode IN ('manual', 'bank')", name="selection_mode"),
        CheckConstraint(
            "max_marks > 0 AND pass_marks > 0 AND pass_marks <= max_marks", name="marks"
        ),
        CheckConstraint("time_limit_seconds BETWEEN 1 AND 86400", name="time_limit"),
        CheckConstraint("attempts_allowed BETWEEN 1 AND 100", name="attempts_allowed"),
        CheckConstraint(
            "reveal_mode IN ('score_only', 'correct_answers', 'explanations')", name="reveal_mode"
        ),
        CheckConstraint(
            "reveal_timing IN ('immediately', 'after_attempts_exhausted')", name="reveal_timing"
        ),
    )


class QuestionBank(UUIDPrimaryKeyMixin, TimestampMixin, OwnerMixin, Base):
    __tablename__ = "question_banks"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        CheckConstraint("revision >= 1", name="revision"),
        Index("ix_question_banks_org_archived_id", "organization_id", "archived_at", "id"),
    )

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, server_default="")
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    archived_at: Mapped[datetime | None]


class Question(UUIDPrimaryKeyMixin, TimestampMixin, OwnerMixin, Base):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["question_banks.id", "question_banks.organization_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("question_type IN ('mcq_single', 'mcq_multi', 'fill_blank')", name="type"),
        CheckConstraint("revision >= 1", name="revision"),
        Index("ix_questions_bank_org", "bank_id", "organization_id"),
        Index("ix_questions_bank_type_id", "bank_id", "question_type", "id"),
        Index("ix_questions_bank_archived_id", "bank_id", "archived_at", "id"),
    )

    bank_id: Mapped[UUID] = mapped_column(Uuid)
    question_type: Mapped[str] = mapped_column(String(20))
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[list[dict[str, str]]] = mapped_column(JSONB, server_default="[]")
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    archived_at: Mapped[datetime | None]


class QuestionSkill(OwnerMixin, Base):
    __tablename__ = "question_skills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "organization_id"],
            ["questions.id", "questions.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_question_skills_question_org", "question_id", "organization_id"),
    )

    question_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    skill_id: Mapped[UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), primary_key=True, index=True
    )


class QuestionKey(OwnerMixin, Base):
    __tablename__ = "question_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "organization_id"],
            ["questions.id", "questions.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_question_keys_question_org", "question_id", "organization_id"),
    )

    question_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    answer_key: Mapped[dict[str, Any]] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text, server_default="")


class Quiz(UUIDPrimaryKeyMixin, TimestampMixin, OwnerMixin, QuizRulesMixin, Base):
    __tablename__ = "quizzes"
    __table_args__ = (
        UniqueConstraint("lesson_id"),
        ForeignKeyConstraint(
            ["course_id", "organization_id"],
            ["courses.id", "courses.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_quizzes_course_org", "course_id", "organization_id"),
        *_rules_constraints(),
    )

    course_id: Mapped[UUID] = mapped_column(Uuid)
    lesson_id: Mapped[UUID] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))


class QuizVersion(UUIDPrimaryKeyMixin, OwnerMixin, QuizRulesMixin, Base):
    __tablename__ = "quiz_versions"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("course_version_id", "lesson_id"),
        ForeignKeyConstraint(
            ["course_id", "organization_id"],
            ["courses.id", "courses.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_quiz_versions_course_org", "course_id", "organization_id"),
        Index("ix_quiz_versions_quiz_id", "quiz_id"),
        Index("ix_quiz_versions_lesson_id", "lesson_id"),
        CheckConstraint("major_version >= 1", name="major_version"),
        *_rules_constraints(),
    )

    quiz_id: Mapped[UUID] = mapped_column(Uuid)  # logical draft reference; never a cascading FK
    course_id: Mapped[UUID] = mapped_column(Uuid)
    course_version_id: Mapped[UUID] = mapped_column(ForeignKey("course_versions.id"))
    lesson_id: Mapped[UUID] = mapped_column(Uuid)
    major_version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    published_at: Mapped[datetime] = mapped_column(server_default=func.now())


class QuizVersionQuestion(UUIDPrimaryKeyMixin, OwnerMixin, Base):
    __tablename__ = "quiz_version_questions"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("quiz_version_id", "question_id"),
        ForeignKeyConstraint(
            ["quiz_version_id", "organization_id"],
            ["quiz_versions.id", "quiz_versions.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_quiz_version_questions_version_org", "quiz_version_id", "organization_id"),
        CheckConstraint("marks > 0", name="marks"),
        CheckConstraint("question_type IN ('mcq_single', 'mcq_multi', 'fill_blank')", name="type"),
    )

    quiz_version_id: Mapped[UUID] = mapped_column(Uuid)
    question_id: Mapped[UUID] = mapped_column(Uuid)  # survives deleting the source question
    question_type: Mapped[str] = mapped_column(String(20))
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    skill_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    marks: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    position: Mapped[int] = mapped_column(Integer)


class QuizVersionKey(OwnerMixin, Base):
    __tablename__ = "quiz_version_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "organization_id"],
            ["quiz_version_questions.id", "quiz_version_questions.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_quiz_version_keys_question_org", "question_id", "organization_id"),
    )

    question_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    answer_key: Mapped[dict[str, Any]] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text, server_default="")


class QuizAttempt(UUIDPrimaryKeyMixin, TimestampMixin, OwnerMixin, Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = (
        Index(
            "ix_quiz_attempts_org_state_recent", "organization_id", "state", "submitted_at", "id"
        ),
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("enrollment_id", "lesson_id", "major_version", "attempt_number"),
        Index(
            "uq_quiz_attempts_active",
            "enrollment_id",
            "lesson_id",
            "major_version",
            unique=True,
            postgresql_where=text("state = 'in_progress'"),
        ),
        Index(
            "ix_quiz_attempts_expiry", "expires_at", postgresql_where=text("state = 'in_progress'")
        ),
        Index("ix_quiz_attempts_org_user_id", "organization_id", "user_id", "id"),
        CheckConstraint("state IN ('in_progress', 'submitted', 'abandoned')", name="state"),
        CheckConstraint(
            "revision >= 1 AND attempt_number >= 1 AND major_version >= 1", name="numbers"
        ),
        CheckConstraint("expires_at > started_at AND max_marks > 0", name="limits"),
        CheckConstraint(
            "(state = 'submitted' AND submitted_at IS NOT NULL AND score IS NOT NULL "
            "AND passed IS NOT NULL AND score BETWEEN 0 AND max_marks) OR "
            "(state IN ('in_progress', 'abandoned') AND score IS NULL "
            "AND passed IS NULL AND submitted_at IS NULL)",
            name="result_state",
        ),
        CheckConstraint("cardinality(question_ids) >= 1", name="questions"),
    )

    enrollment_id: Mapped[UUID] = mapped_column(
        ForeignKey("enrollments.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    lesson_id: Mapped[UUID] = mapped_column(Uuid)
    quiz_version_id: Mapped[UUID] = mapped_column(ForeignKey("quiz_versions.id"), index=True)
    major_version: Mapped[int] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer)
    question_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid))
    state: Mapped[str] = mapped_column(String(20), server_default="in_progress")
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    submitted_at: Mapped[datetime | None]
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    max_marks: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    passed: Mapped[bool | None] = mapped_column(Boolean)


class QuizAnswer(UUIDPrimaryKeyMixin, TimestampMixin, OwnerMixin, Base):
    __tablename__ = "quiz_answers"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_id"),
        ForeignKeyConstraint(
            ["attempt_id", "organization_id"],
            ["quiz_attempts.id", "quiz_attempts.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_quiz_answers_attempt_org", "attempt_id", "organization_id"),
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint("awarded_marks IS NULL OR awarded_marks >= 0", name="marks"),
    )

    attempt_id: Mapped[UUID] = mapped_column(Uuid)
    question_id: Mapped[UUID] = mapped_column(ForeignKey("quiz_version_questions.id"), index=True)
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    awarded_marks: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))


# Case-insensitive substring searches use the pg_trgm extension installed by 0002.
Index(
    "ix_question_banks_name_search",
    func.lower(QuestionBank.name).label("name_search"),
    postgresql_using="gin",
    postgresql_ops={"name_search": "gin_trgm_ops"},
)
Index(
    "ix_questions_prompt_search",
    func.lower(Question.prompt).label("prompt_search"),
    postgresql_using="gin",
    postgresql_ops={"prompt_search": "gin_trgm_ops"},
)
