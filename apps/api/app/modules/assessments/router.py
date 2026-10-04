"""Assessment authoring. Private author contracts never serve student attempts."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from app.core.errors import PreconditionRequiredError, UnprocessableError
from app.core.pagination import CursorPage, PageParams
from app.modules.assessments import service
from app.modules.assessments.schemas import (
    BankCreate,
    BankOut,
    BankPatch,
    PublishedQuiz,
    QuestionBody,
    QuestionOut,
    QuestionPatch,
    QuestionSkills,
    QuestionType,
    QuizBody,
    QuizOut,
)
from app.modules.identity.dependencies import RequestCtx

router = APIRouter(tags=["assessments"])


def _revision(if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> int:
    if if_match is None:
        raise PreconditionRequiredError(
            "Send If-Match with the bank or course revision (0 for a new bank)."
        )
    value = if_match.strip().removeprefix("W/").strip('"')
    if not value.isdigit():
        raise UnprocessableError("If-Match must be a revision number.", code="bad_if_match")
    return int(value)


Revision = Annotated[int, Depends(_revision)]
Search = Annotated[str | None, Query(max_length=200)]


@router.get("/question-banks", operation_id="list_question_banks")
async def list_banks(ctx: RequestCtx, params: PageParams, q: Search = None) -> CursorPage[BankOut]:
    rows, cursor = await service.list_banks(ctx, params, q)
    return CursorPage(items=rows, next_cursor=cursor)


@router.post("/question-banks", status_code=201, operation_id="create_question_bank")
async def create_bank(ctx: RequestCtx, body: BankCreate, revision: Revision) -> BankOut:
    return await service.create_bank(ctx, body, revision)


@router.get("/question-banks/{bank_id}", operation_id="get_question_bank")
async def get_bank(ctx: RequestCtx, bank_id: UUID) -> BankOut:
    return await service.get_bank(ctx, bank_id)


@router.patch("/question-banks/{bank_id}", operation_id="update_question_bank")
async def update_bank(
    ctx: RequestCtx, bank_id: UUID, body: BankPatch, revision: Revision
) -> BankOut:
    return await service.update_bank(ctx, bank_id, body, revision)


@router.delete("/question-banks/{bank_id}", status_code=204, operation_id="archive_question_bank")
async def archive_bank(ctx: RequestCtx, bank_id: UUID, revision: Revision) -> Response:
    await service.archive_bank(ctx, bank_id, revision)
    return Response(status_code=204)


@router.get("/question-banks/{bank_id}/questions", operation_id="list_bank_questions")
async def list_questions(
    *,
    ctx: RequestCtx,
    bank_id: UUID,
    params: PageParams,
    q: Search = None,
    question_type: Annotated[QuestionType | None, Query()] = None,
    skill_ids: Annotated[list[UUID] | None, Query(max_length=50)] = None,
) -> CursorPage[QuestionOut]:
    rows, cursor = await service.list_questions(
        ctx, bank_id, params, query=q, kind=question_type, skill_ids=skill_ids or []
    )
    return CursorPage(items=rows, next_cursor=cursor)


@router.post(
    "/question-banks/{bank_id}/questions", status_code=201, operation_id="create_bank_question"
)
async def create_question(
    ctx: RequestCtx, bank_id: UUID, body: QuestionBody, revision: Revision
) -> QuestionOut:
    return await service.create_question(ctx, bank_id, body, revision)


@router.get("/questions/{question_id}", operation_id="get_author_question")
async def get_question(ctx: RequestCtx, question_id: UUID) -> QuestionOut:
    return await service.get_question(ctx, question_id)


@router.patch("/questions/{question_id}", operation_id="update_author_question")
async def update_question(
    ctx: RequestCtx, question_id: UUID, body: QuestionPatch, revision: Revision
) -> QuestionOut:
    return await service.update_question(ctx, question_id, body, revision)


@router.delete("/questions/{question_id}", status_code=204, operation_id="archive_author_question")
async def archive_question(ctx: RequestCtx, question_id: UUID, revision: Revision) -> Response:
    await service.archive_question(ctx, question_id, revision)
    return Response(status_code=204)


@router.put("/questions/{question_id}/skills", operation_id="replace_question_skills")
async def set_skills(
    ctx: RequestCtx, question_id: UUID, body: QuestionSkills, revision: Revision
) -> QuestionOut:
    return await service.set_question_skills(ctx, question_id, body, revision)


@router.get("/courses/{course_id}/lessons/{lesson_id}/quiz", operation_id="get_draft_quiz")
async def get_quiz(ctx: RequestCtx, course_id: UUID, lesson_id: UUID) -> QuizOut:
    return await service.get_quiz(ctx, course_id, lesson_id)


@router.put("/courses/{course_id}/lessons/{lesson_id}/quiz", operation_id="put_draft_quiz")
async def put_quiz(
    ctx: RequestCtx, course_id: UUID, lesson_id: UUID, body: QuizBody, revision: Revision
) -> QuizOut:
    return await service.put_quiz(ctx, course_id, lesson_id, body, revision)


@router.get(
    "/courses/{course_id}/versions/{version_id}/lessons/{lesson_id}/quiz",
    operation_id="preview_published_quiz",
)
async def published_quiz(
    ctx: RequestCtx, course_id: UUID, version_id: UUID, lesson_id: UUID
) -> PublishedQuiz:
    return await service.get_published_quiz(ctx, course_id, version_id, lesson_id)
