from fastapi import APIRouter

from app.modules.identity.dependencies import CurrentPrincipal, TenantSession
from app.modules.identity.repository import OrganizationRepository
from app.modules.identity.schemas import MembershipOut, MeResponse, MeUser, OrganizationSummary

router = APIRouter(tags=["identity"])


@router.get("/me", operation_id="get_me")
async def get_me(principal: CurrentPrincipal, session: TenantSession) -> MeResponse:
    """The signed-in user, their organizations and roles (for the org switcher), and the active
    organization's permissions."""
    orgs = await OrganizationRepository(session).get_many(list(principal.memberships))
    by_id = {o.id: o for o in orgs}
    memberships = [
        MembershipOut(
            organization=OrganizationSummary.model_validate(by_id[org_id], from_attributes=True),
            roles=sorted(roles),
        )
        for org_id, roles in principal.memberships.items()
        if org_id in by_id
    ]
    memberships.sort(key=lambda m: m.organization.name.lower())
    return MeResponse(
        user=MeUser(id=principal.user_id, email=principal.email, full_name=principal.full_name),
        is_platform_admin=principal.is_platform_admin,
        active_organization_id=principal.organization_id,
        active_roles=sorted(principal.roles),
        permissions=sorted(p.value for p in principal.permissions),
        memberships=memberships,
    )
