"""Assignments HTTP API.

Mutations take `If-Match` (428 when missing, 409 when stale):
- `PUT .../lessons/{lesson_id}/assignment`: the **course** revision, like every outline edit
- `PUT .../submission`: the student's **submission** revision (`0` for the first submission)
- `PUT /assignment-submissions/{id}/grade`: the **submission** revision the grader opened
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status

from app.core.errors import PreconditionRequiredError, UnprocessableError
from app.core.pagination import CursorPage, PageParams
from app.modules.assignments import service
from app.modules.assignments.schemas import (
    AssignmentDraftOut,
    AssignmentUpsert,
    FileUploadOut,
    GradeBody,
    GraderSubmissionDetail,
    GraderSubmissionRow,
    StudentAssignmentOut,
    SubmissionOut,
    SubmissionUploadCreate,
    SubmitBody,
)
from app.modules.identity.dependencies import RequestCtx

router = APIRouter(tags=["assignments"])


def _required_if_match(
    if_match: Annotated[
        str | None,
        Header(alias="If-Match", description="The revision this change is based on"),
    ] = None,
) -> int:
    # Optional at the header level so a missing header is 428, not FastAPI's 422.
    if if_match is None:
        raise PreconditionRequiredError(
            "Send If-Match with the revision this change is based on.",
            code="precondition_required",
        )
    value = if_match.strip().removeprefix("W/").strip('"')
    if not value.isdigit():
        raise UnprocessableError("If-Match must be a revision number.", code="bad_if_match")
    return int(value)


IfMatch = Annotated[int, Depends(_required_if_match)]


# ============================================================================ authoring


@router.get(
    "/courses/{course_id}/lessons/{lesson_id}/assignment", operation_id="get_lesson_assignment"
)
async def get_lesson_assignment(
    ctx: RequestCtx, course_id: UUID, lesson_id: UUID
) -> AssignmentDraftOut | None:
    """The assignment lesson's definition (owner-org editors); null until first saved."""
    return await service.get_draft(ctx, course_id, lesson_id)


@router.put(
    "/courses/{course_id}/lessons/{lesson_id}/assignment", operation_id="put_lesson_assignment"
)
async def put_lesson_assignment(
    ctx: RequestCtx, course_id: UUID, lesson_id: UUID, body: AssignmentUpsert, if_match: IfMatch
) -> AssignmentDraftOut:
    """Create or update the definition. It is published with the next course version."""
    return await service.put_draft(ctx, course_id, lesson_id, body, if_match)


# ============================================================================ students


@router.get(
    "/enrollments/{enrollment_id}/lessons/{lesson_id}/assignment",
    operation_id="get_my_assignment",
)
async def get_my_assignment(
    ctx: RequestCtx, request: Request, enrollment_id: UUID, lesson_id: UUID
) -> StudentAssignmentOut:
    """The published assignment, the student's submission and, once graded, the grade."""
    state = request.app.state
    return await service.get_student_assignment(
        ctx, state.storage, state.settings, enrollment_id, lesson_id
    )


@router.post(
    "/enrollments/{enrollment_id}/lessons/{lesson_id}/submission-upload",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_submission_upload",
)
async def create_submission_upload(
    ctx: RequestCtx,
    request: Request,
    enrollment_id: UUID,
    lesson_id: UUID,
    body: SubmissionUploadCreate,
) -> FileUploadOut:
    """A presigned upload for a file submission (PDF, PNG or JPEG). Upload, then submit its id."""
    state = request.app.state
    return await service.create_submission_upload(
        ctx, state.storage, state.settings, enrollment_id=enrollment_id, lesson_id=lesson_id,
        body=body,
    )  # fmt: skip


@router.put(
    "/enrollments/{enrollment_id}/lessons/{lesson_id}/submission", operation_id="submit_assignment"
)
async def submit_assignment(
    ctx: RequestCtx,
    request: Request,
    *,
    enrollment_id: UUID,
    lesson_id: UUID,
    body: SubmitBody,
    if_match: IfMatch,
) -> SubmissionOut:
    """Submit, or replace the submission until it is graded."""
    state = request.app.state
    return await service.submit(
        ctx, state.storage, state.settings, enrollment_id=enrollment_id, lesson_id=lesson_id,
        body=body, if_match=if_match,
    )  # fmt: skip


# ============================================================================ graders


@router.get("/courses/{course_id}/lessons/{lesson_id}/submissions", operation_id="list_submissions")
async def list_submissions(
    ctx: RequestCtx,
    *,
    course_id: UUID,
    lesson_id: UUID,
    page: PageParams,
    status_: Annotated[Literal["submitted", "graded"] | None, Query(alias="status")] = None,
    batch_id: UUID | None = None,
) -> CursorPage[GraderSubmissionRow]:
    """The active org's submissions for an assignment lesson, ungraded first (graders:
    instructors and org admins of the students' org)."""
    items, cursor = await service.list_submissions(
        ctx, course_id, lesson_id, page, status=status_, batch_id=batch_id
    )
    return CursorPage(items=items, next_cursor=cursor)


submissions = APIRouter(prefix="/assignment-submissions", tags=["assignments"])


@submissions.get("/{submission_id}", operation_id="get_submission")
async def get_submission(
    ctx: RequestCtx, request: Request, submission_id: UUID
) -> GraderSubmissionDetail:
    state = request.app.state
    return await service.get_submission(ctx, state.storage, state.settings, submission_id)


@submissions.put("/{submission_id}/grade", operation_id="grade_submission")
async def grade_submission(
    ctx: RequestCtx, request: Request, submission_id: UUID, body: GradeBody, if_match: IfMatch
) -> GraderSubmissionDetail:
    """Record or correct the grade. The lesson completes and course progress is recomputed in
    the same transaction; the action is audited."""
    state = request.app.state
    return await service.grade(
        ctx, state.storage, state.settings, submission_id=submission_id, body=body,
        if_match=if_match,
    )  # fmt: skip


router.include_router(submissions)
