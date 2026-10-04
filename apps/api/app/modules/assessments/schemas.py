"""Separate author, active-question and result contracts with explicit projections."""

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
