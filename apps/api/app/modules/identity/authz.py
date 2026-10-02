"""Authorization for the service layer: who is acting, in which org, with which roles.

Every service function that changes or reads tenant data calls `require_permission` (or
`require_role`) first, so users get a clear 403 instead of an empty result. RLS stays the backstop:
even a missing check here cannot leak another org's rows.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from app.core.errors import AppError, PermissionDeniedError
from app.modules.identity.models import STAFF_ROLES, OrgRole

PLATFORM_ADMIN_REALM_ROLE = "platform_admin"


class Permission(StrEnum):
    ORG_MANAGE = "org.manage"  # create / update / archive organizations (platform level)
    ORG_READ = "org.read"
    BATCH_MANAGE = "batch.manage"
    BATCH_READ = "batch.read"
    MEMBER_READ = "member.read"
    MEMBER_MANAGE = "member.manage"  # change roles, remove from org
    MEMBER_INVITE = "member.invite"
    MEMBER_IMPORT = "member.import"
    AUDIT_READ = "audit.read"
    LAB_AUTHOR = "lab.author"  # used from Phase 4 (coding labs)
    # Phase 2: courses and learning content.
    COURSE_READ = "course.read"  # courses the org owns or was assigned, and their assignments
    COURSE_EDIT = "course.edit"  # author the org's own courses (drafts, publishing)
    COURSE_ASSIGN = "course.assign"  # owner-org editors assign their courses (publisher-made rows)
    COURSE_DISTRIBUTE = "course.distribute"  # narrow an org grant to the org's own batches
    ENROLLMENT_UPGRADE = "enrollment.upgrade"  # opt the org's enrollments into a new major
    # Also requires the active org to be a content publisher (checked in the skills service).
    SKILL_MANAGE = "skill.manage"
    # Phase 2.5: grade assignment submissions of the org's own students.
    ASSIGNMENT_GRADE = "assignment.grade"


# Single source of truth for role -> permissions (documented in docs/access-control.md).
ROLE_PERMISSIONS: Mapping[OrgRole, frozenset[Permission]] = {
    OrgRole.ORG_ADMIN: frozenset(
        {
            Permission.ORG_READ,
            Permission.BATCH_MANAGE,
            Permission.BATCH_READ,
            Permission.MEMBER_READ,
            Permission.MEMBER_MANAGE,
            Permission.MEMBER_INVITE,
            Permission.MEMBER_IMPORT,
            Permission.AUDIT_READ,
            Permission.COURSE_READ,
            Permission.COURSE_EDIT,
            Permission.COURSE_ASSIGN,
            Permission.COURSE_DISTRIBUTE,
            Permission.ENROLLMENT_UPGRADE,
            Permission.SKILL_MANAGE,
            Permission.ASSIGNMENT_GRADE,
        }
    ),
    OrgRole.INSTRUCTOR: frozenset(
        {
            Permission.ORG_READ,
            Permission.BATCH_READ,
            Permission.MEMBER_READ,
            Permission.COURSE_READ,
            Permission.COURSE_EDIT,
            Permission.COURSE_ASSIGN,
            Permission.SKILL_MANAGE,
            Permission.ASSIGNMENT_GRADE,
        }
    ),
    OrgRole.LAB_AUTHOR: frozenset(
        {Permission.ORG_READ, Permission.LAB_AUTHOR, Permission.SKILL_MANAGE}
    ),
    OrgRole.STUDENT: frozenset({Permission.ORG_READ}),
}


class OrganizationRequiredError(AppError):
    code = "organization_required"
    message = "Select an organization (X-Organization-Id header) for this request."


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller and their active organization for this request."""

    user_id: UUID
    keycloak_sub: str
    email: str
    full_name: str
    is_platform_admin: bool
    organization_id: UUID | None
    roles: frozenset[OrgRole]  # roles in the active organization
    memberships: Mapping[UUID, frozenset[OrgRole]] = field(default_factory=dict)

    @property
    def permissions(self) -> frozenset[Permission]:
        if self.is_platform_admin:
            return frozenset(Permission)
        granted: set[Permission] = set()
        for role in self.roles:
            granted |= ROLE_PERMISSIONS[role]
        return frozenset(granted)

    @property
    def is_staff(self) -> bool:
        return bool(self.roles & STAFF_ROLES)

    def has_role(self, *roles: OrgRole) -> bool:
        return self.is_platform_admin or bool(self.roles & set(roles))


def require_org(principal: Principal) -> UUID:
    """The active organization, or 400 if the request didn't select one."""
    if principal.organization_id is None:
        raise OrganizationRequiredError
    return principal.organization_id


def require_platform_admin(principal: Principal) -> None:
    if not principal.is_platform_admin:
        raise PermissionDeniedError(code="platform_admin_required")


def require_role(principal: Principal, *roles: OrgRole) -> UUID:
    """Require one of `roles` in the active org (platform admins pass). Returns the org id."""
    org = require_org(principal)
    if not principal.has_role(*roles):
        raise PermissionDeniedError(details={"required_roles": sorted(roles)})
    return org


def require_org_permission(principal: Principal, permission: Permission) -> UUID:
    """Require an org-scoped permission; returns the active organization id."""
    org = require_org(principal)
    if permission not in principal.permissions:
        raise PermissionDeniedError(details={"required_permission": permission.value})
    return org


def require_permission(principal: Principal, permission: Permission) -> UUID | None:
    """Require a permission. Org-scoped permissions also require an active org (returned).

    `org.manage` is platform-level and needs no org."""
    if permission is Permission.ORG_MANAGE:
        require_platform_admin(principal)
        return principal.organization_id
    org = require_org(principal)
    if permission not in principal.permissions:
        raise PermissionDeniedError(details={"required_permission": permission.value})
    return org
