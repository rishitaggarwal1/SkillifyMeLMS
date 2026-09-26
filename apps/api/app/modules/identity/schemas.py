from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

from app.modules.identity.models import OrgRole

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
BatchName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Slug = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v),
    StringConstraints(min_length=2, max_length=80, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$"),
]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


def _unique_roles(roles: list[OrgRole]) -> list[OrgRole]:
    if len(set(roles)) != len(roles):
        msg = "roles must be unique"
        raise ValueError(msg)
    return sorted(roles)


# ---------------------------------------------------------------------------- me


class OrganizationSummary(BaseModel):
    id: UUID
    name: str
    slug: str
    is_content_publisher: bool


class MembershipOut(BaseModel):
    organization: OrganizationSummary
    roles: list[OrgRole]


class MeUser(BaseModel):
    id: UUID
    email: str
    full_name: str


class MeResponse(BaseModel):
    user: MeUser
    is_platform_admin: bool
    active_organization_id: UUID | None
    active_roles: list[OrgRole]
    permissions: list[str]
    memberships: list[MembershipOut]


# ---------------------------------------------------------------------------- organizations


class OrganizationCreate(BaseModel):
    name: Name
    slug: Slug
    is_content_publisher: bool = False


class OrganizationUpdate(BaseModel):
    name: Name | None = None
    is_content_publisher: bool | None = None
    status: Literal["active", "archived"] | None = None


class OrganizationOut(BaseModel):
    id: UUID
    name: str
    slug: str
    is_content_publisher: bool
    status: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------- batches


class BatchCreate(BaseModel):
    name: BatchName
    description: Description = ""


class BatchUpdate(BaseModel):
    name: BatchName | None = None
    description: Description | None = None
    status: Literal["active", "archived"] | None = None


class BatchOut(BaseModel):
    id: UUID
    name: str
    description: str
    status: str
    member_count: int
    created_at: datetime
    updated_at: datetime


class BatchMembersAdd(BaseModel):
    user_ids: list[UUID] = Field(min_length=1, max_length=500)


class BatchMembersAddResult(BaseModel):
    added: list[UUID]
    already_members: list[UUID]


# ---------------------------------------------------------------------------- members


class UserOut(BaseModel):
    id: UUID
    email: str
    full_name: str
    status: str


class MemberOut(BaseModel):
    user: UserOut
    roles: list[OrgRole]
    batch_ids: list[UUID]


class MemberUpdate(BaseModel):
    roles: list[OrgRole] = Field(min_length=1, max_length=4)

    _unique = field_validator("roles")(_unique_roles)


# ---------------------------------------------------------------------------- invitations


class InvitationCreate(BaseModel):
    email: EmailStr
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    roles: list[OrgRole] = Field(min_length=1, max_length=4)
    batch_ids: list[UUID] = Field(default_factory=list, max_length=50)

    _unique = field_validator("roles")(_unique_roles)


class InvitationOut(BaseModel):
    id: UUID
    email: str
    roles: list[OrgRole]
    batch_ids: list[UUID]
    status: str
    user_id: UUID | None
    invited_by: UUID | None
    expires_at: datetime
    created_at: datetime


# ---------------------------------------------------------------------------- CSV import jobs


class ImportJobOut(BaseModel):
    id: UUID
    status: str
    file_name: str
    batch_id: UUID | None
    total_rows: int
    processed_rows: int
    created_count: int
    skipped_count: int
    error_count: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
