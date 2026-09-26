from uuid import UUID

from pydantic import BaseModel

from app.modules.identity.models import OrgRole


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
