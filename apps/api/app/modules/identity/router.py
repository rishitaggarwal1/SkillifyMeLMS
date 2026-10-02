"""Identity HTTP API (thin: parse input, call the service, shape output)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status

from app.core.pagination import CursorPage, PageParams
from app.core.ratelimit import RateLimiter
from app.core.redis import RedisClient
from app.modules.identity import service
from app.modules.identity.dependencies import AuditActorDep, CurrentPrincipal, TenantSession
from app.modules.identity.models import OrgRole
from app.modules.identity.repository import OrganizationRepository
from app.modules.identity.schemas import (
    BatchCreate,
    BatchMembersAdd,
    BatchMembersAddResult,
    BatchOut,
    BatchUpdate,
    DirectoryOrganization,
    ImportJobOut,
    InvitationCreate,
    InvitationOut,
    MemberOut,
    MembershipOut,
    MemberUpdate,
    MeResponse,
    MeUser,
    OrgAdminInvite,
    OrganizationCreate,
    OrganizationOut,
    OrganizationSummary,
    OrganizationUpdate,
    PlatformUserDetail,
    PlatformUserOut,
)

router = APIRouter()

StatusFilter = Annotated[Literal["active", "archived"] | None, Query(alias="status")]


async def get_ctx(
    principal: CurrentPrincipal, session: TenantSession, actor: AuditActorDep, redis: RedisClient
) -> service.Ctx:
    return service.Ctx(session=session, principal=principal, actor=actor, redis=redis)


Ctx = Annotated[service.Ctx, Depends(get_ctx)]


def _limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


async def invite_rate_limit(request: Request, principal: CurrentPrincipal) -> None:
    settings = request.app.state.settings
    await _limiter(request).check(
        "invites", str(principal.user_id), limit=settings.rl_invites_per_hour, window_seconds=3600
    )


async def import_rate_limit(request: Request, principal: CurrentPrincipal) -> None:
    settings = request.app.state.settings
    await _limiter(request).check(
        "imports", str(principal.user_id), limit=settings.rl_imports_per_hour, window_seconds=3600
    )


# ============================================================================ me


@router.get("/me", operation_id="get_me", tags=["identity"])
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


# ============================================================================ organizations

orgs = APIRouter(prefix="/organizations", tags=["organizations"])


@orgs.post("", status_code=status.HTTP_201_CREATED, operation_id="create_organization")
async def create_organization(ctx: Ctx, body: OrganizationCreate) -> OrganizationOut:
    """Create an organization (platform admins)."""
    return await service.create_organization(ctx, body)


@orgs.get("", operation_id="list_organizations")
async def list_organizations(
    ctx: Ctx,
    page: PageParams,
    status_: StatusFilter = None,
    q: Annotated[
        str | None, Query(min_length=1, max_length=100, description="Name contains")
    ] = None,
) -> CursorPage[OrganizationOut]:
    items, cursor = await service.list_organizations(ctx, page, status_, q)
    return CursorPage(items=items, next_cursor=cursor)


@orgs.get("/directory", operation_id="list_organization_directory")
async def organization_directory(
    ctx: Ctx,
    page: PageParams,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    ids: Annotated[list[UUID] | None, Query(max_length=100)] = None,
) -> CursorPage[DirectoryOrganization]:
    """Active organizations by name (id and name only), for staff of content-publisher orgs to
    pick who to assign courses to. `ids` looks up names for known ids."""
    items, cursor = await service.organization_directory(ctx, page, search=q, ids=ids)
    return CursorPage(items=items, next_cursor=cursor)


@orgs.get("/current", operation_id="get_current_organization")
async def get_current_organization(ctx: Ctx) -> OrganizationOut:
    """The active organization (any member)."""
    return await service.get_current_organization(ctx)


@orgs.get("/{organization_id}", operation_id="get_organization")
async def get_organization(ctx: Ctx, organization_id: UUID) -> OrganizationOut:
    return await service.get_organization(ctx, organization_id)


@orgs.patch("/{organization_id}", operation_id="update_organization")
async def update_organization(
    ctx: Ctx, organization_id: UUID, body: OrganizationUpdate
) -> OrganizationOut:
    return await service.update_organization(ctx, organization_id, body)


@orgs.delete("/{organization_id}", operation_id="archive_organization")
async def archive_organization(ctx: Ctx, organization_id: UUID) -> OrganizationOut:
    """Archive (soft-delete): members lose access; data is kept."""
    return await service.update_organization(
        ctx, organization_id, OrganizationUpdate(status="archived")
    )


# ============================================================================ batches

batches = APIRouter(prefix="/batches", tags=["batches"])


@batches.post("", status_code=status.HTTP_201_CREATED, operation_id="create_batch")
async def create_batch(ctx: Ctx, body: BatchCreate) -> BatchOut:
    return await service.create_batch(ctx, body)


@batches.get("", operation_id="list_batches")
async def list_batches(
    ctx: Ctx,
    page: PageParams,
    status_: StatusFilter = None,
    name: Annotated[
        str | None,
        Query(min_length=1, max_length=120, description="Exact batch name (case-insensitive)"),
    ] = None,
) -> CursorPage[BatchOut]:
    items, cursor = await service.list_batches(ctx, page, status_, name)
    return CursorPage(items=items, next_cursor=cursor)


@batches.get("/{batch_id}", operation_id="get_batch")
async def get_batch(ctx: Ctx, batch_id: UUID) -> BatchOut:
    return await service.get_batch(ctx, batch_id)


@batches.patch("/{batch_id}", operation_id="update_batch")
async def update_batch(ctx: Ctx, batch_id: UUID, body: BatchUpdate) -> BatchOut:
    return await service.update_batch(ctx, batch_id, body)


@batches.delete("/{batch_id}", operation_id="archive_batch")
async def archive_batch(ctx: Ctx, batch_id: UUID) -> BatchOut:
    """Archive (soft-delete): members and history are kept; no new members can be added."""
    return await service.update_batch(ctx, batch_id, BatchUpdate(status="archived"))


@batches.get("/{batch_id}/members", operation_id="list_batch_members")
async def list_batch_members(ctx: Ctx, batch_id: UUID, page: PageParams) -> CursorPage[MemberOut]:
    items, cursor = await service.list_batch_members(ctx, batch_id, page)
    return CursorPage(items=items, next_cursor=cursor)


@batches.post("/{batch_id}/members", operation_id="add_batch_members")
async def add_batch_members(
    ctx: Ctx, batch_id: UUID, body: BatchMembersAdd
) -> BatchMembersAddResult:
    return await service.add_batch_members(ctx, batch_id, body.user_ids)


@batches.delete(
    "/{batch_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="remove_batch_member",
)
async def remove_batch_member(ctx: Ctx, batch_id: UUID, user_id: UUID) -> None:
    await service.remove_batch_member(ctx, batch_id, user_id)


# ============================================================================ members

members = APIRouter(prefix="/members", tags=["members"])


@members.get("", operation_id="list_members")
async def list_members(
    ctx: Ctx,
    page: PageParams,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    role: OrgRole | None = None,
    batch_id: UUID | None = None,
) -> CursorPage[MemberOut]:
    """Search members by name or email, filter by role or batch."""
    items, cursor = await service.list_members(ctx, page, q=q, role=role, batch_id=batch_id)
    return CursorPage(items=items, next_cursor=cursor)


@members.get("/{user_id}", operation_id="get_member")
async def get_member(ctx: Ctx, user_id: UUID) -> MemberOut:
    return await service.get_member(ctx, user_id)


@members.patch("/{user_id}", operation_id="update_member")
async def update_member(ctx: Ctx, user_id: UUID, body: MemberUpdate) -> MemberOut:
    """Replace the member's roles in the active organization."""
    return await service.update_member_roles(ctx, user_id, body)


@members.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="remove_member")
async def remove_member(ctx: Ctx, user_id: UUID) -> None:
    """Remove from the organization (and all its batches)."""
    await service.remove_member(ctx, user_id)


# ============================================================================ invitations

invitations = APIRouter(prefix="/invitations", tags=["invitations"])


@invitations.post(
    "",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_invitation",
    dependencies=[Depends(invite_rate_limit)],
)
async def create_invitation(ctx: Ctx, request: Request, body: InvitationCreate) -> InvitationOut:
    """Invite someone by email: grants the roles/batches now and emails a link to set a password
    (new accounts only)."""
    return await service.create_invitation(
        ctx, request.app.state.keycloak_admin, request.app.state.settings, body
    )


@invitations.get("", operation_id="list_invitations")
async def list_invitations(
    ctx: Ctx,
    page: PageParams,
    status_: Annotated[
        Literal["pending", "accepted", "revoked", "expired"] | None, Query(alias="status")
    ] = None,
) -> CursorPage[InvitationOut]:
    items, cursor = await service.list_invitations(ctx, page, status_)
    return CursorPage(items=items, next_cursor=cursor)


@invitations.delete("/{invitation_id}", operation_id="revoke_invitation")
async def revoke_invitation(ctx: Ctx, invitation_id: UUID) -> InvitationOut:
    return await service.revoke_invitation(ctx, invitation_id)


@invitations.post(
    "/{invitation_id}/resend",
    operation_id="resend_invitation",
    dependencies=[Depends(invite_rate_limit)],
)
async def resend_invitation(ctx: Ctx, request: Request, invitation_id: UUID) -> InvitationOut:
    return await service.resend_invitation(ctx, request.app.state.keycloak_admin, invitation_id)


# ============================================================================ imports

imports = APIRouter(prefix="/imports", tags=["imports"])


@imports.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="create_import",
    dependencies=[Depends(import_rate_limit)],
)
async def create_import(
    ctx: Ctx,
    request: Request,
    file: Annotated[UploadFile, File(description="CSV with email and full_name columns")],
    batch_id: Annotated[UUID | None, Form()] = None,
) -> ImportJobOut:
    """Upload a CSV of students; processing continues in the background (poll GET /imports/{id})."""
    settings = request.app.state.settings
    data = await file.read(settings.import_max_bytes + 1)
    return await service.start_import(
        ctx,
        request.app.state.storage,
        request.app.state.enqueue_import,
        file_name=file.filename or "import.csv",
        content_type=file.content_type or "",
        data=data,
        batch_id=batch_id,
        max_bytes=settings.import_max_bytes,
    )


@imports.get("", operation_id="list_imports")
async def list_imports(ctx: Ctx, page: PageParams) -> CursorPage[ImportJobOut]:
    items, cursor = await service.list_imports(ctx, page)
    return CursorPage(items=items, next_cursor=cursor)


@imports.get("/{job_id}", operation_id="get_import")
async def get_import(ctx: Ctx, job_id: UUID) -> ImportJobOut:
    return await service.get_import(ctx, job_id)


@imports.get(
    "/{job_id}/errors.csv",
    operation_id="download_import_errors",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Per-row errors as CSV"}},
)
async def download_import_errors(ctx: Ctx, job_id: UUID) -> Response:
    content = await service.import_errors_csv(ctx, job_id)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="import-{job_id}-errors.csv"'},
    )


# ============================================================================ platform admin

platform = APIRouter(prefix="/platform", tags=["platform"])


@platform.get("/users", operation_id="platform_list_users")
async def platform_list_users(
    ctx: Ctx,
    page: PageParams,
    *,
    q: Annotated[
        str | None, Query(min_length=1, max_length=100, description="Name or email contains")
    ] = None,
    role: OrgRole | None = None,
    organization_id: UUID | None = None,
    status_: Annotated[
        Literal["invited", "active", "disabled"] | None, Query(alias="status")
    ] = None,
) -> CursorPage[PlatformUserOut]:
    """Users across every organization (platform admins)."""
    items, cursor = await service.platform_list_users(
        ctx, page, q=q, role=role, organization_id=organization_id, status=status_
    )
    return CursorPage(items=items, next_cursor=cursor)


@platform.get("/users/{user_id}", operation_id="platform_get_user")
async def platform_get_user(ctx: Ctx, user_id: UUID) -> PlatformUserDetail:
    """A user with every membership and batch (platform admins)."""
    return await service.platform_get_user(ctx, user_id)


@platform.post("/users/{user_id}/disable", operation_id="platform_disable_user")
async def platform_disable_user(ctx: Ctx, request: Request, user_id: UUID) -> PlatformUserDetail:
    """Block sign-in and API access, and end the user's sessions (platform admins)."""
    return await service.set_user_enabled(
        ctx, request.app.state.keycloak_admin, user_id, enabled=False
    )


@platform.post("/users/{user_id}/enable", operation_id="platform_enable_user")
async def platform_enable_user(ctx: Ctx, request: Request, user_id: UUID) -> PlatformUserDetail:
    return await service.set_user_enabled(
        ctx, request.app.state.keycloak_admin, user_id, enabled=True
    )


@platform.post(
    "/organizations/{organization_id}/admins",
    status_code=status.HTTP_201_CREATED,
    operation_id="platform_invite_org_admin",
    dependencies=[Depends(invite_rate_limit)],
)
async def platform_invite_org_admin(
    ctx: Ctx, request: Request, organization_id: UUID, body: OrgAdminInvite
) -> InvitationOut:
    """Invite an org admin into an organization, e.g. a college just created (platform admins).
    The invitation behaves like one an org admin sends."""
    return await service.invite_org_admin(
        ctx, request.app.state.keycloak_admin, request.app.state.settings, organization_id, body
    )


for sub in (orgs, batches, members, invitations, imports, platform):
    router.include_router(sub)
