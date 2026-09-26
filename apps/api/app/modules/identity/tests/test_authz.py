"""Service-layer authorization helpers."""

import pytest

from app.core.errors import PermissionDeniedError
from app.db.base import new_id
from app.modules.identity.authz import (
    ROLE_PERMISSIONS,
    OrganizationRequiredError,
    Permission,
    Principal,
    require_permission,
    require_platform_admin,
    require_role,
)
from app.modules.identity.models import OrgRole

ORG = new_id()


def _principal(*roles: OrgRole, org: object = ORG, platform_admin: bool = False) -> Principal:
    return Principal(
        user_id=new_id(),
        keycloak_sub="sub",
        email="x@example.test",
        full_name="X",
        is_platform_admin=platform_admin,
        organization_id=org,  # type: ignore[arg-type]
        roles=frozenset(roles),
    )


# Expected permission matrix (documented in docs/access-control.md).
EXPECTED = {
    OrgRole.ORG_ADMIN: {
        "org.read",
        "batch.manage",
        "batch.read",
        "member.read",
        "member.manage",
        "member.invite",
        "member.import",
        "audit.read",
        "course.read",
        "course.edit",
        "course.assign",
        "course.distribute",
        "enrollment.upgrade",
        "skill.manage",
    },
    OrgRole.INSTRUCTOR: {
        "org.read",
        "batch.read",
        "member.read",
        "course.read",
        "course.edit",
        "course.assign",
        "skill.manage",
    },
    OrgRole.LAB_AUTHOR: {"org.read", "lab.author", "skill.manage"},
    OrgRole.STUDENT: {"org.read"},
}


def test_role_permission_matrix() -> None:
    assert {role: {p.value for p in perms} for role, perms in ROLE_PERMISSIONS.items()} == EXPECTED
    assert set(ROLE_PERMISSIONS) == set(OrgRole)


def test_permissions_union_across_roles() -> None:
    p = _principal(OrgRole.INSTRUCTOR, OrgRole.LAB_AUTHOR)
    assert {x.value for x in p.permissions} == EXPECTED[OrgRole.INSTRUCTOR] | EXPECTED[
        OrgRole.LAB_AUTHOR
    ]


@pytest.mark.parametrize("role", list(OrgRole))
@pytest.mark.parametrize("permission", [p for p in Permission if p is not Permission.ORG_MANAGE])
def test_require_permission_follows_matrix(role: OrgRole, permission: Permission) -> None:
    principal = _principal(role)
    if permission.value in EXPECTED[role]:
        assert require_permission(principal, permission) == ORG
    else:
        with pytest.raises(PermissionDeniedError):
            require_permission(principal, permission)


def test_org_scoped_permissions_need_an_active_org() -> None:
    with pytest.raises(OrganizationRequiredError):
        require_permission(_principal(OrgRole.ORG_ADMIN, org=None), Permission.BATCH_MANAGE)


def test_org_manage_is_platform_level() -> None:
    assert (
        require_permission(_principal(org=None, platform_admin=True), Permission.ORG_MANAGE) is None
    )
    with pytest.raises(PermissionDeniedError):
        require_permission(_principal(OrgRole.ORG_ADMIN), Permission.ORG_MANAGE)
    with pytest.raises(PermissionDeniedError):
        require_platform_admin(_principal(OrgRole.ORG_ADMIN))


def test_platform_admin_has_every_permission_in_any_org() -> None:
    principal = _principal(platform_admin=True)
    for permission in Permission:
        require_permission(principal, permission)


def test_require_role() -> None:
    assert (
        require_role(_principal(OrgRole.INSTRUCTOR), OrgRole.INSTRUCTOR, OrgRole.ORG_ADMIN) == ORG
    )
    with pytest.raises(PermissionDeniedError):
        require_role(_principal(OrgRole.STUDENT), OrgRole.INSTRUCTOR)
    with pytest.raises(OrganizationRequiredError):
        require_role(_principal(OrgRole.STUDENT, org=None), OrgRole.STUDENT)
