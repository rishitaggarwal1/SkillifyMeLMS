"""Identity module public interface. Other modules (and this module's router) call these functions;
nothing outside the module touches its repositories or tables.

Every admin operation: checks the permission first (clear 403s; RLS is the backstop), writes an
audit entry in the same transaction, and emits batch membership events through the outbox.
"""

import csv
import io
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.pagination import CursorParams
from app.core.storage import ObjectStorage
from app.db.base import new_id
from app.db.session import run_after_commit, run_after_commit_async
from app.db.tenancy import set_tenant_context
from app.modules.audit import service as audit
from app.modules.audit.service import AuditActor
from app.modules.identity import events
from app.modules.identity.authz import (
    Permission,
    Principal,
    require_org_permission,
    require_permission,
)
from app.modules.identity.keycloak_admin import IdentityProviderAdmin, NewUser
from app.modules.identity.models import (
    Batch,
    BatchStatus,
    ImportJob,
    Invitation,
    InvitationStatus,
    Organization,
    OrgRole,
    UserStatus,
)
from app.modules.identity.principal import invalidate_principal, resolve_principal
from app.modules.identity.repository import (
    BatchMemberRepository,
    BatchRepository,
    EnsureUser,
    ImportJobRepository,
    InvitationRepository,
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)
from app.modules.identity.schemas import (
    BatchCreate,
    BatchMembersAddResult,
    BatchOut,
    BatchUpdate,
    ImportJobOut,
    InvitationCreate,
    InvitationOut,
    MemberOut,
    MemberUpdate,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
    UserOut,
)

__all__ = ["invalidate_principal", "resolve_principal"]  # re-exported principal helpers


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_error"
    message = "The request is invalid."


@dataclass(frozen=True, slots=True)
class Ctx:
    """Everything an admin operation needs about the request."""

    session: AsyncSession
    principal: Principal
    actor: AuditActor
    redis: "Redis"


def _org_out(org: Organization) -> OrganizationOut:
    return OrganizationOut.model_validate(org, from_attributes=True)


def _batch_out(batch: Batch, member_count: int) -> BatchOut:
    return BatchOut(
        id=batch.id,
        name=batch.name,
        description=batch.description,
        status=batch.status,
        member_count=member_count,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


async def _unique(session: AsyncSession, action: Any, error: ConflictError) -> Any:
    """Run a write in a savepoint, turning a unique violation into a 409."""
    try:
        async with session.begin_nested():
            return await action
    except IntegrityError as exc:
        raise error from exc


# ============================================================================ Phase 2 interface


async def iter_batch_student_ids(
    session: AsyncSession, batch_id: UUID, *, page_size: int = 500
) -> AsyncIterator[list[UUID]]:
    """Yield pages of student user ids in a batch (keyset pagination; used for enrollment
    fan-out). Scoped by the session's RLS context."""
    repo = BatchMemberRepository(session)
    after: UUID | None = None
    while True:
        page = await repo.student_ids(batch_id, after=after, limit=page_size)
        if not page:
            return
        yield page
        after = page[-1]


async def batch_belongs_to_org(
    session: AsyncSession, batch_id: UUID, organization_id: UUID
) -> bool:
    batch = await BatchRepository(session).get(batch_id)
    return batch is not None and batch.organization_id == organization_id


async def list_org_batches(
    session: AsyncSession, organization_id: UUID, params: CursorParams
) -> tuple[list[Batch], str | None]:
    return await BatchRepository(session).list_page(
        organization_id, params, status=BatchStatus.ACTIVE
    )


# ============================================================================ organizations


async def create_organization(ctx: Ctx, data: OrganizationCreate) -> OrganizationOut:
    require_permission(ctx.principal, Permission.ORG_MANAGE)
    org = await _unique(
        ctx.session,
        OrganizationRepository(ctx.session).create(
            name=data.name, slug=data.slug, is_content_publisher=data.is_content_publisher
        ),
        ConflictError("An organization with this slug already exists.", code="slug_taken"),
    )
    out = _org_out(org)
    await audit.record(
        ctx.session, ctx.actor, action="organization.created", target_type="organization",
        target_id=org.id, after=out, organization_id=org.id,
    )  # fmt: skip
    return out


async def list_organizations(
    ctx: Ctx, params: CursorParams, status: str | None
) -> tuple[list[OrganizationOut], str | None]:
    require_permission(ctx.principal, Permission.ORG_MANAGE)
    orgs, cursor = await OrganizationRepository(ctx.session).list_page(params, status=status)
    return [_org_out(o) for o in orgs], cursor


async def get_organization(ctx: Ctx, organization_id: UUID) -> OrganizationOut:
    require_permission(ctx.principal, Permission.ORG_MANAGE)
    org = await OrganizationRepository(ctx.session).get(organization_id)
    if org is None:
        raise NotFoundError("Organization not found.")
    return _org_out(org)


async def get_current_organization(ctx: Ctx) -> OrganizationOut:
    org_id = require_org_permission(ctx.principal, Permission.ORG_READ)
    org = await OrganizationRepository(ctx.session).get(org_id)
    if org is None:
        raise NotFoundError("Organization not found.")
    return _org_out(org)


async def update_organization(
    ctx: Ctx, organization_id: UUID, data: OrganizationUpdate
) -> OrganizationOut:
    require_permission(ctx.principal, Permission.ORG_MANAGE)
    repo = OrganizationRepository(ctx.session)
    before = await repo.get(organization_id)
    if before is None:
        raise NotFoundError("Organization not found.")
    before_out = _org_out(before)
    values = data.model_dump(exclude_unset=True, exclude_none=True)
    org = await repo.update(organization_id, values)
    if org is None:
        raise NotFoundError("Organization not found.")
    after = _org_out(org)
    await audit.record(
        ctx.session, ctx.actor,
        action="organization.archived" if values.get("status") == "archived"
        else "organization.updated",
        target_type="organization", target_id=org.id, before=before_out, after=after,
        organization_id=org.id,
    )  # fmt: skip
    if "status" in values:
        # Archiving/restoring changes every member's effective roles.
        subs = await MembershipRepository(ctx.session).member_subs(organization_id)
        _invalidate_after_commit(ctx, subs)
    return after


def _invalidate_after_commit(ctx: Ctx, subs: Sequence[str]) -> None:
    """Drop cached principals once the membership change is committed."""
    if subs:
        run_after_commit_async(ctx.session, lambda: invalidate_principal(ctx.redis, *subs))


# ============================================================================ batches


async def _get_batch(ctx: Ctx, batch_id: UUID) -> Batch:
    batch = await BatchRepository(ctx.session).get(batch_id)
    if batch is None or batch.organization_id != ctx.principal.organization_id:
        raise NotFoundError("Batch not found.")
    return batch


async def create_batch(ctx: Ctx, data: BatchCreate) -> BatchOut:
    org_id = require_org_permission(ctx.principal, Permission.BATCH_MANAGE)
    batch = await _unique(
        ctx.session,
        BatchRepository(ctx.session).create(
            organization_id=org_id,
            name=data.name,
            description=data.description,
            created_by=ctx.principal.user_id,
        ),
        ConflictError("A batch with this name already exists.", code="batch_name_taken"),
    )
    await ctx.session.refresh(batch)
    out = _batch_out(batch, 0)
    await audit.record(
        ctx.session, ctx.actor, action="batch.created", target_type="batch", target_id=batch.id,
        after=out,
    )  # fmt: skip
    return out


async def list_batches(
    ctx: Ctx, params: CursorParams, status: str | None
) -> tuple[list[BatchOut], str | None]:
    org_id = require_org_permission(ctx.principal, Permission.BATCH_READ)
    repo = BatchRepository(ctx.session)
    batches, cursor = await repo.list_page(org_id, params, status=status)
    counts = await repo.member_counts([b.id for b in batches])
    return [_batch_out(b, counts[b.id]) for b in batches], cursor


async def get_batch(ctx: Ctx, batch_id: UUID) -> BatchOut:
    require_permission(ctx.principal, Permission.BATCH_READ)
    batch = await _get_batch(ctx, batch_id)
    counts = await BatchRepository(ctx.session).member_counts([batch.id])
    return _batch_out(batch, counts[batch.id])


async def update_batch(ctx: Ctx, batch_id: UUID, data: BatchUpdate) -> BatchOut:
    require_permission(ctx.principal, Permission.BATCH_MANAGE)
    repo = BatchRepository(ctx.session)
    current = await _get_batch(ctx, batch_id)
    counts = await repo.member_counts([batch_id])
    before = _batch_out(current, counts[batch_id])
    values = data.model_dump(exclude_unset=True, exclude_none=True)
    batch = await _unique(
        ctx.session,
        repo.update(batch_id, values),
        ConflictError("A batch with this name already exists.", code="batch_name_taken"),
    )
    after = _batch_out(batch, counts[batch_id])
    await audit.record(
        ctx.session, ctx.actor,
        action="batch.archived" if values.get("status") == "archived" else "batch.updated",
        target_type="batch", target_id=batch_id, before=before, after=after,
    )  # fmt: skip
    return after


async def list_batch_members(
    ctx: Ctx, batch_id: UUID, params: CursorParams
) -> tuple[list[MemberOut], str | None]:
    org_id = require_org_permission(ctx.principal, Permission.BATCH_READ)
    await _get_batch(ctx, batch_id)
    rows, cursor = await BatchMemberRepository(ctx.session).list_page(batch_id, params)
    return await _members_out(ctx, org_id, [r.user_id for r in rows]), cursor


async def add_batch_members(
    ctx: Ctx, batch_id: UUID, user_ids: Sequence[UUID]
) -> BatchMembersAddResult:
    org_id = require_org_permission(ctx.principal, Permission.BATCH_MANAGE)
    batch = await _get_batch(ctx, batch_id)
    if batch.status != BatchStatus.ACTIVE:
        raise ConflictError("The batch is archived.", code="batch_archived")
    unique_ids = list(dict.fromkeys(user_ids))
    roles = await MembershipRepository(ctx.session).roles_for_users(org_id, unique_ids)
    non_members = [uid for uid in unique_ids if not roles[uid]]
    if non_members:
        raise ValidationFailedError(
            "Only members of this organization can be added to its batches.",
            code="not_organization_members",
            details={"user_ids": non_members},
        )
    added = await BatchMemberRepository(ctx.session).add_many(
        batch_id=batch_id,
        organization_id=org_id,
        user_ids=unique_ids,
        added_by=ctx.principal.user_id,
    )
    events.batch_members_added(
        ctx.session,
        organization_id=org_id,
        batch_id=batch_id,
        user_ids=added,
        actor_user_id=ctx.principal.user_id,
    )
    if added:
        await audit.record(
            ctx.session, ctx.actor, action="batch.members_added", target_type="batch",
            target_id=batch_id, after={"user_ids": added},
        )  # fmt: skip
    return BatchMembersAddResult(
        added=added, already_members=[u for u in unique_ids if u not in set(added)]
    )


async def remove_batch_member(ctx: Ctx, batch_id: UUID, user_id: UUID) -> None:
    org_id = require_org_permission(ctx.principal, Permission.BATCH_MANAGE)
    await _get_batch(ctx, batch_id)
    removed = await BatchMemberRepository(ctx.session).remove(batch_id=batch_id, user_ids=[user_id])
    if not removed:
        raise NotFoundError("This user is not in the batch.")
    events.batch_members_removed(
        ctx.session,
        organization_id=org_id,
        batch_id=batch_id,
        user_ids=removed,
        actor_user_id=ctx.principal.user_id,
    )
    await audit.record(
        ctx.session, ctx.actor, action="batch.member_removed", target_type="batch",
        target_id=batch_id, before={"user_ids": removed},
    )  # fmt: skip


# ============================================================================ members


async def _members_out(ctx: Ctx, org_id: UUID, user_ids: Sequence[UUID]) -> list[MemberOut]:
    """Users + roles + batches for many members in three queries (no N+1), in input order."""
    users = {u.id: u for u in await UserRepository(ctx.session).get_many(user_ids)}
    roles = await MembershipRepository(ctx.session).roles_for_users(org_id, user_ids)
    batches = await BatchMemberRepository(ctx.session).batch_ids_for_users(org_id, user_ids)
    return [
        MemberOut(
            user=UserOut.model_validate(users[uid], from_attributes=True),
            roles=sorted(OrgRole(r) for r in roles[uid]),
            batch_ids=batches[uid],
        )
        for uid in user_ids
        if uid in users
    ]


async def list_members(
    ctx: Ctx,
    params: CursorParams,
    *,
    q: str | None,
    role: OrgRole | None,
    batch_id: UUID | None,
) -> tuple[list[MemberOut], str | None]:
    org_id = require_org_permission(ctx.principal, Permission.MEMBER_READ)
    users, cursor = await UserRepository(ctx.session).list_members(
        org_id, params, q=q, role=role, batch_id=batch_id
    )
    return await _members_out(ctx, org_id, [u.id for u in users]), cursor


async def get_member(ctx: Ctx, user_id: UUID) -> MemberOut:
    org_id = require_org_permission(ctx.principal, Permission.MEMBER_READ)
    members = await _members_out(ctx, org_id, [user_id])
    if not members or not members[0].roles:
        raise NotFoundError("Member not found.")
    return members[0]


async def _guard_last_admin(ctx: Ctx, org_id: UUID, current_roles: set[str]) -> None:
    if OrgRole.ORG_ADMIN in current_roles and (
        await MembershipRepository(ctx.session).count_with_role(org_id, OrgRole.ORG_ADMIN) <= 1
    ):
        raise ConflictError(
            "An organization must keep at least one org admin.", code="last_org_admin"
        )


async def update_member_roles(ctx: Ctx, user_id: UUID, data: MemberUpdate) -> MemberOut:
    org_id = require_org_permission(ctx.principal, Permission.MEMBER_MANAGE)
    before = await get_member(ctx, user_id)
    current = {r.value for r in before.roles}
    wanted = {r.value for r in data.roles}
    if OrgRole.ORG_ADMIN not in wanted:
        await _guard_last_admin(ctx, org_id, current)
    memberships = MembershipRepository(ctx.session)
    if current - wanted:
        await memberships.remove(
            user_id=user_id, organization_id=org_id, roles=sorted(current - wanted)
        )
    for role in sorted(wanted - current):
        await memberships.add(
            user_id=user_id, organization_id=org_id, role=role, created_by=ctx.principal.user_id
        )
    after = await get_member(ctx, user_id)
    await audit.record(
        ctx.session, ctx.actor, action="member.roles_changed", target_type="user",
        target_id=user_id, before={"roles": before.roles}, after={"roles": after.roles},
    )  # fmt: skip
    user = await UserRepository(ctx.session).get(user_id)
    if user is not None:
        _invalidate_after_commit(ctx, [user.keycloak_sub])
    return after


async def remove_member(ctx: Ctx, user_id: UUID) -> None:
    org_id = require_org_permission(ctx.principal, Permission.MEMBER_MANAGE)
    before = await get_member(ctx, user_id)
    await _guard_last_admin(ctx, org_id, {r.value for r in before.roles})
    await _remove_from_org(ctx, org_id, user_id, reason="left_organization")
    await audit.record(
        ctx.session, ctx.actor, action="member.removed", target_type="user", target_id=user_id,
        before=before,
    )  # fmt: skip


async def _remove_from_org(
    ctx: Ctx, org_id: UUID, user_id: UUID, *, reason: events.RemoveReason
) -> None:
    await _drop_org_access(
        ctx.session, org_id, user_id, actor_user_id=ctx.principal.user_id, reason=reason
    )
    user = await UserRepository(ctx.session).get(user_id)
    if user is not None:
        _invalidate_after_commit(ctx, [user.keycloak_sub])


async def _drop_org_access(
    session: AsyncSession,
    org_id: UUID,
    user_id: UUID,
    *,
    actor_user_id: UUID | None,
    reason: events.RemoveReason,
) -> None:
    """Remove a user's batch memberships (with events) and roles in one org."""
    removed = await BatchMemberRepository(session).remove_user_from_org(
        organization_id=org_id, user_id=user_id
    )
    for batch_id, uid in removed:
        events.batch_members_removed(
            session, organization_id=org_id, batch_id=batch_id, user_ids=[uid],
            actor_user_id=actor_user_id, reason=reason,
        )  # fmt: skip
    await MembershipRepository(session).remove(user_id=user_id, organization_id=org_id)


# ============================================================================ invitations


def _invitation_out(inv: Invitation) -> InvitationOut:
    return InvitationOut.model_validate(inv, from_attributes=True)


async def _active_batches(ctx: Ctx, org_id: UUID, batch_ids: Sequence[UUID]) -> list[UUID]:
    unique = list(dict.fromkeys(batch_ids))
    found = {
        b.id
        for b in await BatchRepository(ctx.session).get_many(unique)
        if b.organization_id == org_id and b.status == BatchStatus.ACTIVE
    }
    missing = [b for b in unique if b not in found]
    if missing:
        raise ValidationFailedError(
            "Unknown or archived batches.", code="invalid_batches", details={"batch_ids": missing}
        )
    return unique


async def create_invitation(
    ctx: Ctx, idp: IdentityProviderAdmin, settings: Settings, data: InvitationCreate
) -> InvitationOut:
    org_id = require_org_permission(ctx.principal, Permission.MEMBER_INVITE)
    email = data.email.lower()
    batch_ids = await _active_batches(ctx, org_id, data.batch_ids)
    invitations = InvitationRepository(ctx.session)
    if await invitations.find_pending(org_id, email) is not None:
        raise ConflictError(
            "This email already has a pending invitation.", code="invitation_pending"
        )

    keycloak_ids = await idp.ensure_users([NewUser(email=email, full_name=data.full_name)])
    [ensured] = await UserRepository(ctx.session).ensure_many(
        [EnsureUser(keycloak_sub=keycloak_ids[email], email=email, full_name=data.full_name)]
    )
    if ensured.email_conflict or ensured.user_id is None:
        raise ConflictError("This email is linked to another account.", code="email_in_use")
    user_id = ensured.user_id
    memberships = MembershipRepository(ctx.session)
    if (await memberships.roles_for_users(org_id, [user_id]))[user_id]:
        raise ConflictError("This person is already a member.", code="already_member")

    for role in data.roles:
        await memberships.add(
            user_id=user_id, organization_id=org_id, role=role, created_by=ctx.principal.user_id
        )
    for batch_id in batch_ids:
        added = await BatchMemberRepository(ctx.session).add_many(
            batch_id=batch_id, organization_id=org_id, user_ids=[user_id],
            added_by=ctx.principal.user_id,
        )  # fmt: skip
        events.batch_members_added(
            ctx.session, organization_id=org_id, batch_id=batch_id, user_ids=added,
            actor_user_id=ctx.principal.user_id, reason="invitation",
        )  # fmt: skip
    invitation = await invitations.create(
        organization_id=org_id,
        email=email,
        roles=[r.value for r in data.roles],
        batch_ids=batch_ids,
        invited_by=ctx.principal.user_id,
        user_id=user_id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.invitation_ttl_days),
    )
    await ctx.session.refresh(invitation)
    out = _invitation_out(invitation)
    await audit.record(
        ctx.session, ctx.actor, action="invitation.created", target_type="invitation",
        target_id=invitation.id, after=out,
    )  # fmt: skip
    # New accounts need a password; existing ones (from another org) just see the new org.
    if ensured.status == UserStatus.INVITED:
        await idp.send_setup_email(keycloak_ids[email])
    user = await UserRepository(ctx.session).get(user_id)
    if user is not None:
        _invalidate_after_commit(ctx, [user.keycloak_sub])
    return out


async def list_invitations(
    ctx: Ctx, params: CursorParams, status: str | None
) -> tuple[list[InvitationOut], str | None]:
    org_id = require_org_permission(ctx.principal, Permission.MEMBER_INVITE)
    rows, cursor = await InvitationRepository(ctx.session).list_page(org_id, params, status=status)
    return [_invitation_out(i) for i in rows], cursor


async def _pending_invitation(ctx: Ctx, invitation_id: UUID) -> Invitation:
    inv = await InvitationRepository(ctx.session).get(invitation_id)
    if inv is None or inv.organization_id != ctx.principal.organization_id:
        raise NotFoundError("Invitation not found.")
    if inv.status != InvitationStatus.PENDING:
        raise ConflictError(f"The invitation is {inv.status}.", code=f"invitation_{inv.status}")
    return inv


async def revoke_invitation(ctx: Ctx, invitation_id: UUID) -> InvitationOut:
    """Revoke a pending invitation and the access it granted (the person never signed in)."""
    org_id = require_org_permission(ctx.principal, Permission.MEMBER_INVITE)
    inv = await _pending_invitation(ctx, invitation_id)
    if inv.user_id is not None:
        await _remove_from_org(ctx, org_id, inv.user_id, reason="invitation_revoked")
    updated = await InvitationRepository(ctx.session).update(inv.id, {"status": "revoked"})
    if updated is None:
        raise NotFoundError("Invitation not found.")
    out = _invitation_out(updated)
    await audit.record(
        ctx.session, ctx.actor, action="invitation.revoked", target_type="invitation",
        target_id=inv.id, after=out,
    )  # fmt: skip
    return out


async def resend_invitation(
    ctx: Ctx, idp: IdentityProviderAdmin, invitation_id: UUID
) -> InvitationOut:
    require_permission(ctx.principal, Permission.MEMBER_INVITE)
    inv = await _pending_invitation(ctx, invitation_id)
    user = await UserRepository(ctx.session).get(inv.user_id) if inv.user_id else None
    if user is None:
        raise NotFoundError("Invitation not found.")
    await idp.send_setup_email(user.keycloak_sub)
    await audit.record(
        ctx.session, ctx.actor, action="invitation.resent", target_type="invitation",
        target_id=inv.id,
    )  # fmt: skip
    return _invitation_out(inv)


async def expire_invitations(session: AsyncSession, now: datetime) -> int:
    """System job: expire overdue pending invitations and withdraw the access they granted to
    people who never signed in. Must run with platform-admin RLS context (all orgs)."""
    expired = 0
    invitations = InvitationRepository(session)
    users = UserRepository(session)
    for inv in await invitations.expired_pending(now):
        # Act inside the invitation's org, so events and writes are scoped to it (outbox RLS).
        await set_tenant_context(
            session, organization_id=inv.organization_id, user_id=None, is_platform_admin=True
        )
        user = await users.get(inv.user_id) if inv.user_id else None
        if user is not None and user.status == UserStatus.INVITED:
            await _drop_org_access(
                session, inv.organization_id, user.id, actor_user_id=None,
                reason="invitation_revoked",
            )  # fmt: skip
        await invitations.update(inv.id, {"status": InvitationStatus.EXPIRED})
        await session.flush()  # write this org's events while its context is set
        expired += 1
    return expired


# ============================================================================ imports

CSV_CONTENT_TYPES = frozenset(
    {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain", ""}
)


async def start_import(
    ctx: Ctx,
    storage: ObjectStorage,
    enqueue: Callable[[UUID, UUID, UUID], None],
    *,
    file_name: str,
    content_type: str,
    data: bytes,
    batch_id: UUID | None,
    max_bytes: int,
) -> ImportJobOut:
    org_id = require_org_permission(ctx.principal, Permission.MEMBER_IMPORT)
    if len(data) > max_bytes:
        raise ValidationFailedError(
            f"The file is larger than {max_bytes // (1024 * 1024)} MB.", code="file_too_large"
        )
    if not data.strip():
        raise ValidationFailedError("The file is empty.", code="empty_file")
    if content_type.split(";", maxsplit=1)[0].strip().lower() not in CSV_CONTENT_TYPES and not (
        file_name.lower().endswith(".csv")
    ):
        raise ValidationFailedError("Upload a .csv file.", code="unsupported_file_type")
    if batch_id is not None:
        await _active_batches(ctx, org_id, [batch_id])

    job_id = new_id()
    key = f"imports/{org_id}/{job_id}.csv"
    await storage.aput_bytes(key, data, "text/csv")
    job = await ImportJobRepository(ctx.session).create(
        organization_id=org_id,
        batch_id=batch_id,
        created_by=ctx.principal.user_id,
        file_key=key,
        file_name=file_name[:255],
    )
    await ctx.session.flush()
    await ctx.session.refresh(job)
    await audit.record(
        ctx.session, ctx.actor, action="import.started", target_type="import_job",
        target_id=job.id, after={"file_name": job.file_name, "batch_id": batch_id},
    )  # fmt: skip
    user_id = ctx.principal.user_id
    run_after_commit(ctx.session, lambda: enqueue(job.id, org_id, user_id))
    return ImportJobOut.model_validate(job, from_attributes=True)


async def _get_job(ctx: Ctx, job_id: UUID) -> ImportJob:
    job = await ImportJobRepository(ctx.session).get(job_id)
    if job is None or job.organization_id != ctx.principal.organization_id:
        raise NotFoundError("Import not found.")
    return job


async def list_imports(ctx: Ctx, params: CursorParams) -> tuple[list[ImportJobOut], str | None]:
    org_id = require_org_permission(ctx.principal, Permission.MEMBER_IMPORT)
    jobs, cursor = await ImportJobRepository(ctx.session).list_page(org_id, params)
    return [ImportJobOut.model_validate(j, from_attributes=True) for j in jobs], cursor


async def get_import(ctx: Ctx, job_id: UUID) -> ImportJobOut:
    require_permission(ctx.principal, Permission.MEMBER_IMPORT)
    return ImportJobOut.model_validate(await _get_job(ctx, job_id), from_attributes=True)


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: object) -> str:
    """Neutralize spreadsheet formula injection (OWASP): prefix risky cells with a quote."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


async def import_errors_csv(ctx: Ctx, job_id: UUID) -> str:
    require_permission(ctx.principal, Permission.MEMBER_IMPORT)
    await _get_job(ctx, job_id)
    errors = await ImportJobRepository(ctx.session).list_errors(job_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["row", "code", "message", "email", "full_name"])
    for e in errors:
        writer.writerow(
            [
                e.row_number,
                _csv_safe(e.code),
                _csv_safe(e.message),
                _csv_safe(e.raw.get("email")),
                _csv_safe(e.raw.get("full_name")),
            ]
        )
    return buffer.getvalue()
