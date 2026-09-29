"""Courses API models: the draft builder, publishing, versions and assignments."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.courses import notes
from app.modules.courses.models import LessonType, ReleaseType

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Slug = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=120
    ),
]
Threshold = Annotated[Decimal, Field(gt=0, le=1, max_digits=3, decimal_places=2)]
IdList = Annotated[list[UUID], Field(min_length=1, max_length=500)]


def _unique_ids(ids: list[UUID]) -> list[UUID]:
    if len(set(ids)) != len(ids):
        msg = "ids must be unique"
        raise ValueError(msg)
    return ids


# ============================================================================ lesson content
# `lessons.content` is JSONB; each lesson type has its own shape. Notes documents are checked
# against the Tiptap allow-list in `notes.py` (images are then checked to be the org's files).

MAX_NOTES_BYTES = 200_000


class VideoContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_asset_id: UUID | None = None


class NotesContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc: dict[str, Any] | None = Field(
        default=None,
        description="Tiptap JSON document, limited to the notes allow-list (see notes.py). "
        "Images reference uploaded image files: {type: image, attrs: {file_id, alt}}.",
    )

    @field_validator("doc")
    @classmethod
    def _allowed(cls, doc: dict[str, Any] | None) -> dict[str, Any] | None:
        if doc is not None:
            notes.validate_doc(doc)  # NotesValidationError is a ValueError -> 422 with its path
        return doc


class PdfContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: UUID | None = None


class PlaceholderContent(BaseModel):
    """Quiz, lab and assignment lessons: their content arrives in later phases."""

    model_config = ConfigDict(extra="forbid")


CONTENT_MODELS: dict[LessonType, type[BaseModel]] = {
    LessonType.VIDEO: VideoContent,
    LessonType.NOTES: NotesContent,
    LessonType.PDF: PdfContent,
    LessonType.QUIZ: PlaceholderContent,
    LessonType.LAB: PlaceholderContent,
    LessonType.ASSIGNMENT: PlaceholderContent,
}


# ============================================================================ courses


class CourseCreate(BaseModel):
    title: Title
    slug: Slug | None = Field(default=None, description="Derived from the title when omitted.")
    description: str = Field(default="", max_length=5000)
    is_public_catalog: bool = False


class CourseUpdate(BaseModel):
    title: Title | None = None
    slug: Slug | None = None
    description: str | None = Field(default=None, max_length=5000)
    is_public_catalog: bool | None = None


class VersionSummary(BaseModel):
    id: UUID
    major: int
    minor: int
    version: str = Field(description='"major.minor", e.g. "1.2"')
    release_type: ReleaseType
    published_at: datetime


class CourseOut(BaseModel):
    id: UUID
    organization_id: UUID = Field(description="The owner organization")
    title: str
    slug: str
    description: str
    status: Literal["active", "archived"]
    is_public_catalog: bool
    revision: int
    is_owner: bool = Field(description="Whether the active organization owns (and can edit) it")
    current_version: VersionSummary | None
    created_at: datetime
    updated_at: datetime


# ============================================================================ draft tree


class ModuleCreate(BaseModel):
    title: Title


class ModuleUpdate(BaseModel):
    title: Title


class LessonCreate(BaseModel):
    title: Title
    lesson_type: LessonType
    is_required: bool = True
    completion_threshold: Threshold | None = Field(
        default=None, description="Video: fraction that must be watched (default 0.9)"
    )
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    content: dict[str, Any] = Field(default_factory=dict)


class LessonUpdate(BaseModel):
    """The lesson type can't change: delete the lesson and add a new one instead."""

    title: Title | None = None
    is_required: bool | None = None
    completion_threshold: Threshold | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    content: dict[str, Any] | None = None


class OrderUpdate(BaseModel):
    ids: IdList

    _unique = field_validator("ids")(_unique_ids)


class LessonSkillsUpdate(BaseModel):
    skill_ids: Annotated[list[UUID], Field(max_length=50)]

    _unique = field_validator("skill_ids")(_unique_ids)


class LessonSummary(BaseModel):
    id: UUID
    module_id: UUID
    title: str
    lesson_type: LessonType
    position: int
    is_required: bool
    completion_threshold: Decimal | None
    estimated_minutes: int | None
    skill_ids: list[UUID]


class LessonOut(LessonSummary):
    content: dict[str, Any]
    course_revision: int


class NotesPreviewOut(BaseModel):
    html: str = Field(description="Sanitized HTML; images are `<img data-file-id>` placeholders")
    image_urls: dict[UUID, str] = Field(description="Signed URLs for the images, by file id")
    expires_at: datetime | None


class ModuleOut(BaseModel):
    id: UUID
    title: str
    position: int
    course_revision: int


class DraftModule(BaseModel):
    id: UUID
    title: str
    position: int
    lessons: list[LessonSummary]


class DraftOut(BaseModel):
    course: CourseOut
    modules: list[DraftModule]


class RevisionOut(BaseModel):
    course_revision: int


# ============================================================================ publishing


class StructuralChange(BaseModel):
    code: Literal[
        "modules_changed",
        "lessons_added",
        "lessons_removed",
        "lessons_reordered",
        "lesson_settings_changed",
    ]
    lesson_ids: list[UUID] = Field(default_factory=list)


class PublishBlocker(BaseModel):
    code: Literal["empty_course", "video_not_ready", "pdf_not_ready", "course_archived"]
    lesson_ids: list[UUID] = Field(default_factory=list)


class PublishPreview(BaseModel):
    is_first_release: bool
    next_major: str
    next_minor: str | None
    minor_allowed: bool
    structural_changes: list[StructuralChange]
    blockers: list[PublishBlocker]


class PublishRequest(BaseModel):
    release_type: ReleaseType
    release_notes: str = Field(default="", max_length=2000)


class VersionOut(VersionSummary):
    course_id: UUID
    title: str
    release_notes: str
    published_by: UUID | None


class VersionDetail(VersionOut):
    snapshot: dict[str, Any]


# ============================================================================ assignments


class AssignmentCreate(BaseModel):
    organization_id: UUID | None = Field(
        default=None, description="Receiving organization (default: the active organization)"
    )
    batch_ids: Annotated[list[UUID], Field(max_length=100)] = Field(
        default_factory=list,
        description="Batches to assign. Empty = an org grant (content publishers only).",
    )

    _unique = field_validator("batch_ids")(_unique_ids)


class AssignmentOut(BaseModel):
    id: UUID
    course_id: UUID
    organization_id: UUID = Field(description="The receiving organization")
    batch_id: UUID | None
    kind: Literal["org_grant", "batch"]
    assigned_by_org_id: UUID
    parent_assignment_id: UUID | None
    created_at: datetime
