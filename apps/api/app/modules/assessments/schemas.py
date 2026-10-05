"""Separate author, active-question and result contracts with explicit projections."""

import unicodedata
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

QuestionType = Literal["mcq_single", "mcq_multi", "fill_blank"]
RevealMode = Literal["score_only", "correct_answers", "explanations"]
RevealTiming = Literal["immediately", "after_attempts_exhausted"]
Text = Annotated[str, StringConstraints(min_length=1, max_length=20000)]


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuestionOption(SafeModel):
    id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    text: Text


class SavedAnswer(SafeModel):
    option_ids: list[str] = Field(default_factory=list, max_length=100)
    text: str | None = Field(default=None, max_length=20000)


class QuestionPrompt(SafeModel):
    id: UUID
    question_type: QuestionType
    prompt: Text
    options: list[QuestionOption] = Field(default_factory=list, max_length=100)
    skill_ids: list[UUID] = Field(default_factory=list, max_length=100)
    marks: Decimal = Field(gt=0)


class ActiveQuestion(QuestionPrompt):
    saved_answer: SavedAnswer | None = None


class AnswerKey(SafeModel):
    """Private author/grading data. Never an active-question field."""

    correct_option_ids: list[str] = Field(default_factory=list, max_length=100)
    accepted_answers: list[Text] = Field(default_factory=list, max_length=100)
    case_sensitive: bool = False


class AuthorQuestion(QuestionPrompt):
    answer_key: AnswerKey
    explanation: str = Field(default="", max_length=20000)


class ResultContext(SafeModel):
    """Trusted DB facts checked again by result schemas, independently of SQL reveal."""

    state: Literal["submitted"]
    configured_reveal_mode: RevealMode
    reveal_timing: RevealTiming
    attempts_allowed: int = Field(ge=1, le=100)
    attempts_used: int = Field(ge=1)
    has_active_attempt: bool

    def solutions_allowed(self) -> bool:
        return self.configured_reveal_mode != "score_only" and (
            self.reveal_timing == "immediately"
            or (self.attempts_used >= self.attempts_allowed and not self.has_active_attempt)
        )


class ScoreResult(SafeModel):
    attempt_id: UUID
    reveal_mode: Literal["score_only"] = "score_only"
    context: ResultContext
    score: Decimal = Field(ge=0)
    max_marks: Decimal = Field(gt=0)
    pass_marks: Decimal = Field(gt=0)
    passed: bool

    @model_validator(mode="after")
    def _score_bounds(self) -> Self:
        if self.score > self.max_marks or self.pass_marks > self.max_marks:
            raise ValueError("Result marks exceed the maximum")
        if self.passed != (self.score >= self.pass_marks):
            raise ValueError("Result pass state disagrees with the score")
        return self


class CorrectAnswer(SafeModel):
    question_id: UUID
    answer_key: AnswerKey


class ExplainedAnswer(CorrectAnswer):
    explanation: str = Field(max_length=20000)


class AnswersResult(SafeModel):
    attempt_id: UUID
    reveal_mode: Literal["correct_answers"] = "correct_answers"
    context: ResultContext
    score: Decimal = Field(ge=0)
    max_marks: Decimal = Field(gt=0)
    pass_marks: Decimal = Field(gt=0)
    passed: bool
    solutions: list[CorrectAnswer]

    @model_validator(mode="after")
    def _visibility(self) -> Self:
        if not self.context.solutions_allowed():
            raise ValueError("Solutions are not available")
        # The ordinary score model validates total/pass facts without selecting secret fields.
        ScoreResult.model_validate(self.model_dump(exclude={"solutions", "reveal_mode"}))
        return self


class ExplanationsResult(SafeModel):
    attempt_id: UUID
    reveal_mode: Literal["explanations"] = "explanations"
    context: ResultContext
    score: Decimal = Field(ge=0)
    max_marks: Decimal = Field(gt=0)
    pass_marks: Decimal = Field(gt=0)
    passed: bool
    solutions: list[ExplainedAnswer]

    @model_validator(mode="after")
    def _visibility(self) -> Self:
        if (
            not self.context.solutions_allowed()
            or self.context.configured_reveal_mode != "explanations"
        ):
            raise ValueError("Explanations are not available")
        ScoreResult.model_validate(self.model_dump(exclude={"solutions", "reveal_mode"}))
        return self


QuizResult = Annotated[
    ScoreResult | AnswersResult | ExplanationsResult, Field(discriminator="reveal_mode")
]

# Authoring contracts are never reused for student responses.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Marks = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]


class BankCreate(SafeModel):
    name: Name
    description: str = Field(default="", max_length=20000)


class BankPatch(SafeModel):
    name: Name | None = None
    description: str | None = Field(default=None, max_length=20000)


class BankOut(BankCreate):
    id: UUID
    organization_id: UUID
    revision: int
    archived_at: datetime | None
    updated_at: datetime


MIN_OPTIONS = 2


class QuestionBody(SafeModel):
    question_type: QuestionType
    prompt: Text
    options: list[QuestionOption] = Field(default_factory=list, max_length=100)
    answer_key: AnswerKey
    explanation: str = Field(default="", max_length=20000)

    @model_validator(mode="after")
    def _grading_rule(self) -> Self:
        ids = [o.id for o in self.options]
        key = self.answer_key
        if len(ids) != len(set(ids)):
            raise ValueError("Option identities must be unique")
        if self.question_type == "fill_blank":
            if ids or key.correct_option_ids or not key.accepted_answers:
                raise ValueError("A blank needs accepted answers and no options")
            normalized = [unicodedata.normalize("NFC", a).strip() for a in key.accepted_answers]
            comparable = normalized if key.case_sensitive else [a.casefold() for a in normalized]
            if not all(normalized) or len(comparable) != len(set(comparable)):
                raise ValueError("Accepted answers must be nonempty and distinct")
            key.accepted_answers = normalized
        else:
            correct = key.correct_option_ids
            if (
                len(ids) < MIN_OPTIONS
                or not correct
                or len(correct) != len(set(correct))
                or not set(correct) < set(ids)
                or key.accepted_answers
                or key.case_sensitive
                or (self.question_type == "mcq_single" and len(correct) != 1)
            ):
                raise ValueError("Choose valid correct options and at least one incorrect option")
        return self


class QuestionPatch(SafeModel):
    question_type: QuestionType | None = None
    prompt: Text | None = None
    options: list[QuestionOption] | None = Field(default=None, max_length=100)
    answer_key: AnswerKey | None = None
    explanation: str | None = Field(default=None, max_length=20000)


class QuestionOut(QuestionBody):
    id: UUID
    bank_id: UUID
    bank_revision: int
    revision: int
    skill_ids: list[UUID]
    archived_at: datetime | None


class QuestionSkills(SafeModel):
    skill_ids: list[UUID] = Field(max_length=50)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len(self.skill_ids) != len(set(self.skill_ids)):
            raise ValueError("Skill identities must be unique")
        return self


class QuestionMarks(SafeModel):
    question_id: UUID
    marks: Marks


class ManualSelection(SafeModel):
    mode: Literal["manual"] = "manual"
    questions: list[QuestionMarks] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len({q.question_id for q in self.questions}) != len(self.questions):
            raise ValueError("Question identities must be unique")
        return self


class BankSelection(SafeModel):
    mode: Literal["bank"] = "bank"
    bank_id: UUID
    draw_count: int = Field(ge=1, le=100)
    marks_per_question: Marks
    skill_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len(self.skill_ids) != len(set(self.skill_ids)):
            raise ValueError("Skill identities must be unique")
        return self


class QuizBody(SafeModel):
    title: Name
    selection: Annotated[ManualSelection | BankSelection, Field(discriminator="mode")]
    pass_marks: Marks
    time_limit_seconds: int = Field(ge=1, le=86400)
    attempts_allowed: int = Field(ge=1, le=100)
    randomize_order: bool = False
    reveal_mode: RevealMode = "score_only"
    reveal_timing: RevealTiming = "immediately"

    @model_validator(mode="after")
    def _marks(self) -> Self:
        if self.pass_marks > self.maximum():
            raise ValueError("Pass marks exceed the maximum")
        if self.maximum() > Decimal("99999999.99"):
            raise ValueError("Maximum marks exceed the supported range")
        return self

    def maximum(self) -> Decimal:
        if isinstance(self.selection, ManualSelection):
            return sum((q.marks for q in self.selection.questions), Decimal(0))
        return self.selection.draw_count * self.selection.marks_per_question


class QuizOut(QuizBody):
    id: UUID
    course_id: UUID
    lesson_id: UUID
    max_marks: Decimal
    course_revision: int


class PublishedQuiz(SafeModel):
    id: UUID
    quiz_id: UUID
    course_version_id: UUID
    lesson_id: UUID
    title: str
    selection_mode: Literal["manual", "bank"]
    max_marks: Decimal
    pass_marks: Decimal
    time_limit_seconds: int
    attempts_allowed: int
    randomize_order: bool
    reveal_mode: RevealMode
    reveal_timing: RevealTiming
    questions: list[QuestionPrompt]


class AnswerInput(SafeModel):
    question_id: UUID
    answer: SavedAnswer


class AnswerBatch(SafeModel):
    answers: list[AnswerInput] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len({a.question_id for a in self.answers}) != len(self.answers):
            raise ValueError("A question may occur only once in an answer batch")
        return self


class AttemptSummary(SafeModel):
    id: UUID
    quiz_version_id: UUID
    attempt_number: int
    major_version: int
    state: Literal["in_progress", "submitted", "abandoned"]
    revision: int
    started_at: datetime
    expires_at: datetime
    submitted_at: datetime | None
    score: Decimal | None
    max_marks: Decimal
    passed: bool | None


class AttemptDetail(AttemptSummary):
    server_now: datetime
    questions: list[ActiveQuestion]


class StudentQuiz(SafeModel):
    quiz_id: UUID
    quiz_version_id: UUID
    title: str
    time_limit_seconds: int
    max_marks: Decimal
    pass_marks: Decimal
    attempts_allowed: int
    attempts_used: int
    attempts_remaining: int
    revision: int
    active_attempt_id: UUID | None
    reveal_mode: RevealMode
    reveal_timing: RevealTiming
    server_now: datetime
