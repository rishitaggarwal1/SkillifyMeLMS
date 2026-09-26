"""Data access for the identity module's own tables.

Repositories never filter by organization themselves for security: every query runs as the
non-owner app role inside a transaction whose tenant context (`set_tenant_context`) makes RLS scope
it. A row the caller may not see is simply absent, so `get()` returns None and updates/deletes
affect nothing; the service layer turns that into 404. Explicit org filters below are for query
shape and index use, not for access control.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorParams, paginate_by_id
from app.db.base import new_id
from app.modules.identity.models import (
    Batch,
    BatchMember,
    ImportJob,
    ImportJobError,
    Invitation,
    Membership,
    Organization,
    User,
)

_ENSURE_USERS = text(
    "SELECT keycloak_sub, user_id, user_status, email_conflict "
    "FROM app.ensure_users(:ids, :subs, :emails, :names)"
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True, slots=True)
class EnsureUser:
    keycloak_sub: str
    email: str
    full_name: str


@dataclass(frozen=True, slots=True)
class EnsuredUser:
    keycloak_sub: str
    user_id: UUID | None  # None when the email belongs to another identity
    status: str | None
    email_conflict: bool


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: UUID) -> Organization | None:
        return await self.session.get(Organization, organization_id)

    async def get_many(self, ids: Sequence[UUID]) -> list[Organization]:
        if not ids:
            return []
        rows = await self.session.scalars(select(Organization).where(Organization.id.in_(ids)))
        return list(rows)

    async def list_page(
        self, params: CursorParams, *, status: str | None = None
    ) -> tuple[list[Organization], str | None]:
        stmt = select(Organization)
        if status is not None:
            stmt = stmt.where(Organization.status == status)
        return await paginate_by_id(self.session, stmt, Organization.id, params)

    async def create(self, *, name: str, slug: str, is_content_publisher: bool) -> Organization:
        org = Organization(
            id=new_id(), name=name, slug=slug, is_content_publisher=is_content_publisher
        )
        self.session.add(org)
        await self.session.flush()
        return org

    async def update(self, organization_id: UUID, values: dict[str, Any]) -> Organization | None:
        if not values:
            return await self.get(organization_id)
        return await self.session.scalar(
            update(Organization)
            .where(Organization.id == organization_id)
            .values(**values, updated_at=func.now())
            .returning(Organization)
            .execution_options(populate_existing=True)
        )

    async def delete(self, organization_id: UUID) -> bool:
        result = await self.session.execute(
            delete(Organization).where(Organization.id == organization_id)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_many(self, ids: Sequence[UUID]) -> list[User]:
        if not ids:
            return []
        return list(await self.session.scalars(select(User).where(User.id.in_(ids))))

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(
            select(User).where(func.lower(User.email) == email.lower())
        )

    async def get_by_emails(self, emails: Sequence[str]) -> list[User]:
        if not emails:
            return []
        lowered = [e.lower() for e in emails]
        return list(
            await self.session.scalars(select(User).where(func.lower(User.email).in_(lowered)))
        )

    async def create(self, *, keycloak_sub: str, email: str, full_name: str, status: str) -> User:
        user = User(
            id=new_id(), keycloak_sub=keycloak_sub, email=email, full_name=full_name, status=status
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def list_members(
        self,
        organization_id: UUID,
        params: CursorParams,
        *,
        q: str | None = None,
        role: str | None = None,
        batch_id: UUID | None = None,
    ) -> tuple[list[User], str | None]:
        """Users with any membership (optionally a given role / batch) in the org, newest first.
        `q` matches name or email (trigram indexes)."""
        membership = select(Membership.id).where(
            Membership.user_id == User.id, Membership.organization_id == organization_id
        )
        if role is not None:
            membership = membership.where(Membership.role == role)
        stmt = select(User).where(exists(membership))
        if batch_id is not None:
            stmt = stmt.where(
                exists(
                    select(BatchMember.id).where(
                        BatchMember.user_id == User.id, BatchMember.batch_id == batch_id
                    )
                )
            )
        if q:
            pattern = f"%{_escape_like(q.lower())}%"
            stmt = stmt.where(
                or_(
                    func.lower(User.email).like(pattern, escape="\\"),
                    func.lower(User.full_name).like(pattern, escape="\\"),
                )
            )
        return await paginate_by_id(self.session, stmt, User.id, params)

    async def ensure_many(self, users: Sequence["EnsureUser"]) -> list["EnsuredUser"]:
        """Find-or-create users by Keycloak id through app.ensure_users() (SECURITY DEFINER: org
        admins can't see users outside their org). Requires org_admin in the current org."""
        if not users:
            return []
        rows = await self.session.execute(
            _ENSURE_USERS,
            {
                "ids": [new_id() for _ in users],
                "subs": [u.keycloak_sub for u in users],
                "emails": [u.email for u in users],
                "names": [u.full_name for u in users],
            },
        )
        return [
            EnsuredUser(
                keycloak_sub=r.keycloak_sub,
                user_id=r.user_id,
                status=r.user_status,
                email_conflict=r.email_conflict,
            )
            for r in rows
        ]

    async def update(self, user_id: UUID, values: dict[str, Any]) -> User | None:
        return await self.session.scalar(
            update(User)
            .where(User.id == user_id)
            .values(**values, updated_at=func.now())
            .returning(User)
            .execution_options(populate_existing=True)
        )


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        """All of a user's memberships across orgs (RLS lets users always see their own)."""
        return list(
            await self.session.scalars(
                select(Membership)
                .where(Membership.user_id == user_id)
                .order_by(Membership.organization_id, Membership.role)
            )
        )

    async def roles_in_org(self, user_id: UUID, organization_id: UUID) -> set[str]:
        rows = await self.session.scalars(
            select(Membership.role).where(
                Membership.user_id == user_id, Membership.organization_id == organization_id
            )
        )
        return set(rows)

    async def roles_for_users(
        self, organization_id: UUID, user_ids: Sequence[UUID]
    ) -> dict[UUID, set[str]]:
        """Roles of many users in one query (avoids N+1 when listing members)."""
        result: dict[UUID, set[str]] = {uid: set() for uid in user_ids}
        if not user_ids:
            return result
        rows = await self.session.execute(
            select(Membership.user_id, Membership.role).where(
                Membership.organization_id == organization_id, Membership.user_id.in_(user_ids)
            )
        )
        for user_id, role in rows:
            result[user_id].add(role)
        return result

    async def count_with_role(self, organization_id: UUID, role: str) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(Membership.organization_id == organization_id, Membership.role == role)
            )
            or 0
        )

    async def member_subs(self, organization_id: UUID) -> list[str]:
        """Keycloak subjects of an org's members (to invalidate cached principals)."""
        return list(
            await self.session.scalars(
                select(User.keycloak_sub)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.organization_id == organization_id)
                .distinct()
            )
        )

    async def add(
        self, *, user_id: UUID, organization_id: UUID, role: str, created_by: UUID | None
    ) -> bool:
        """Grant a role. Returns False if the user already had it."""
        result = await self.session.execute(
            pg_insert(Membership)
            .values(
                id=new_id(),
                user_id=user_id,
                organization_id=organization_id,
                role=role,
                created_by=created_by,
            )
            .on_conflict_do_nothing(constraint="uq_memberships_user_org_role")
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def remove(
        self, *, user_id: UUID, organization_id: UUID, roles: Sequence[str] | None = None
    ) -> int:
        stmt = delete(Membership).where(
            Membership.user_id == user_id, Membership.organization_id == organization_id
        )
        if roles is not None:
            stmt = stmt.where(Membership.role.in_(roles))
        result = await self.session.execute(stmt)
        return int(result.rowcount)  # type: ignore[attr-defined]


class BatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, batch_id: UUID) -> Batch | None:
        return await self.session.get(Batch, batch_id)

    async def get_many(self, ids: Sequence[UUID]) -> list[Batch]:
        if not ids:
            return []
        return list(await self.session.scalars(select(Batch).where(Batch.id.in_(ids))))

    async def member_counts(self, batch_ids: Sequence[UUID]) -> dict[UUID, int]:
        counts: dict[UUID, int] = dict.fromkeys(batch_ids, 0)
        if batch_ids:
            rows = await self.session.execute(
                select(BatchMember.batch_id, func.count())
                .where(BatchMember.batch_id.in_(batch_ids))
                .group_by(BatchMember.batch_id)
            )
            counts.update({batch_id: int(n) for batch_id, n in rows})
        return counts

    async def list_page(
        self, organization_id: UUID, params: CursorParams, *, status: str | None = None
    ) -> tuple[list[Batch], str | None]:
        stmt = select(Batch).where(Batch.organization_id == organization_id)
        if status is not None:
            stmt = stmt.where(Batch.status == status)
        return await paginate_by_id(self.session, stmt, Batch.id, params)

    async def create(
        self, *, organization_id: UUID, name: str, description: str, created_by: UUID | None
    ) -> Batch:
        batch = Batch(
            id=new_id(),
            organization_id=organization_id,
            name=name,
            description=description,
            created_by=created_by,
        )
        self.session.add(batch)
        await self.session.flush()
        return batch

    async def update(self, batch_id: UUID, values: dict[str, Any]) -> Batch | None:
        return await self.session.scalar(
            update(Batch)
            .where(Batch.id == batch_id)
            .values(**values, updated_at=func.now())
            .returning(Batch)
            .execution_options(populate_existing=True)
        )

    async def delete(self, batch_id: UUID) -> bool:
        result = await self.session.execute(delete(Batch).where(Batch.id == batch_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]


class BatchMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_many(
        self,
        *,
        batch_id: UUID,
        organization_id: UUID,
        user_ids: Sequence[UUID],
        added_by: UUID | None,
    ) -> list[UUID]:
        """Add users to a batch; returns only the users that were newly added (idempotent)."""
        if not user_ids:
            return []
        rows = await self.session.scalars(
            pg_insert(BatchMember)
            .values(
                [
                    {
                        "id": new_id(),
                        "batch_id": batch_id,
                        "organization_id": organization_id,
                        "user_id": uid,
                        "added_by": added_by,
                    }
                    for uid in dict.fromkeys(user_ids)
                ]
            )
            .on_conflict_do_nothing(constraint="uq_batch_members_batch_user")
            .returning(BatchMember.user_id)
        )
        return list(rows)

    async def remove(self, *, batch_id: UUID, user_ids: Sequence[UUID]) -> list[UUID]:
        """Remove users from a batch; returns the users that were actually removed."""
        if not user_ids:
            return []
        rows = await self.session.scalars(
            delete(BatchMember)
            .where(BatchMember.batch_id == batch_id, BatchMember.user_id.in_(user_ids))
            .returning(BatchMember.user_id)
        )
        return list(rows)

    async def remove_user_from_org(
        self, *, organization_id: UUID, user_id: UUID
    ) -> list[tuple[UUID, UUID]]:
        """Remove a user from every batch of an org; returns (batch_id, user_id) pairs removed."""
        rows = await self.session.execute(
            delete(BatchMember)
            .where(BatchMember.organization_id == organization_id, BatchMember.user_id == user_id)
            .returning(BatchMember.batch_id, BatchMember.user_id)
        )
        return [(b, u) for b, u in rows]

    async def list_user_ids(
        self, batch_id: UUID, *, after: UUID | None = None, limit: int = 500
    ) -> list[UUID]:
        """Keyset-paginated member IDs of a batch (used for enrollment fan-out in Phase 2)."""
        stmt = select(BatchMember.user_id).where(BatchMember.batch_id == batch_id)
        if after is not None:
            stmt = stmt.where(BatchMember.user_id > after)
        return list(await self.session.scalars(stmt.order_by(BatchMember.user_id).limit(limit)))

    async def list_page(
        self, batch_id: UUID, params: CursorParams
    ) -> tuple[list[BatchMember], str | None]:
        stmt = select(BatchMember).where(BatchMember.batch_id == batch_id)
        return await paginate_by_id(self.session, stmt, BatchMember.id, params)

    async def student_ids(
        self, batch_id: UUID, *, after: UUID | None = None, limit: int = 500
    ) -> list[UUID]:
        """Keyset page of batch members who hold the student role in the batch's org."""
        stmt = (
            select(BatchMember.user_id)
            .join(
                Membership,
                (Membership.user_id == BatchMember.user_id)
                & (Membership.organization_id == BatchMember.organization_id)
                & (Membership.role == "student"),
            )
            .where(BatchMember.batch_id == batch_id)
        )
        if after is not None:
            stmt = stmt.where(BatchMember.user_id > after)
        return list(await self.session.scalars(stmt.order_by(BatchMember.user_id).limit(limit)))

    async def batch_ids_for_users(
        self, organization_id: UUID, user_ids: Sequence[UUID]
    ) -> dict[UUID, list[UUID]]:
        result: dict[UUID, list[UUID]] = {uid: [] for uid in user_ids}
        if user_ids:
            rows = await self.session.execute(
                select(BatchMember.user_id, BatchMember.batch_id)
                .where(
                    BatchMember.organization_id == organization_id,
                    BatchMember.user_id.in_(user_ids),
                )
                .order_by(BatchMember.batch_id)
            )
            for user_id, batch_id in rows:
                result[user_id].append(batch_id)
        return result

    async def batch_ids_for_user(self, organization_id: UUID, user_id: UUID) -> list[UUID]:
        return list(
            await self.session.scalars(
                select(BatchMember.batch_id).where(
                    BatchMember.organization_id == organization_id, BatchMember.user_id == user_id
                )
            )
        )

    async def is_member(self, batch_id: UUID, user_id: UUID) -> bool:
        found = await self.session.scalar(
            select(BatchMember.id).where(
                BatchMember.batch_id == batch_id, BatchMember.user_id == user_id
            )
        )
        return found is not None


class InvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, invitation_id: UUID) -> Invitation | None:
        return await self.session.get(Invitation, invitation_id)

    async def list_page(
        self, organization_id: UUID, params: CursorParams, *, status: str | None = None
    ) -> tuple[list[Invitation], str | None]:
        stmt = select(Invitation).where(Invitation.organization_id == organization_id)
        if status is not None:
            stmt = stmt.where(Invitation.status == status)
        return await paginate_by_id(self.session, stmt, Invitation.id, params)

    async def find_pending(self, organization_id: UUID, email: str) -> Invitation | None:
        return await self.session.scalar(
            select(Invitation).where(
                Invitation.organization_id == organization_id,
                func.lower(Invitation.email) == email.lower(),
                Invitation.status == "pending",
            )
        )

    async def expired_pending(self, now: datetime, limit: int = 500) -> list[Invitation]:
        return list(
            await self.session.scalars(
                select(Invitation)
                .where(Invitation.status == "pending", Invitation.expires_at < now)
                .order_by(Invitation.id)
                .limit(limit)
            )
        )

    async def create(
        self,
        *,
        organization_id: UUID,
        email: str,
        roles: Sequence[str],
        batch_ids: Sequence[UUID],
        invited_by: UUID | None,
        user_id: UUID | None,
        expires_at: datetime,
    ) -> Invitation:
        invitation = Invitation(
            id=new_id(),
            organization_id=organization_id,
            email=email,
            roles=list(roles),
            batch_ids=list(batch_ids),
            invited_by=invited_by,
            user_id=user_id,
            expires_at=expires_at,
        )
        self.session.add(invitation)
        await self.session.flush()
        return invitation

    async def update(self, invitation_id: UUID, values: dict[str, Any]) -> Invitation | None:
        return await self.session.scalar(
            update(Invitation)
            .where(Invitation.id == invitation_id)
            .values(**values, updated_at=func.now())
            .returning(Invitation)
            .execution_options(populate_existing=True)
        )


class ImportJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, job_id: UUID) -> ImportJob | None:
        return await self.session.get(ImportJob, job_id)

    async def list_page(
        self, organization_id: UUID, params: CursorParams
    ) -> tuple[list[ImportJob], str | None]:
        stmt = select(ImportJob).where(ImportJob.organization_id == organization_id)
        return await paginate_by_id(self.session, stmt, ImportJob.id, params)

    async def create(
        self,
        *,
        organization_id: UUID,
        batch_id: UUID | None,
        created_by: UUID | None,
        file_key: str,
        file_name: str,
    ) -> ImportJob:
        job = ImportJob(
            id=new_id(),
            organization_id=organization_id,
            batch_id=batch_id,
            created_by=created_by,
            file_key=file_key,
            file_name=file_name,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def update(self, job_id: UUID, values: dict[str, Any]) -> ImportJob | None:
        return await self.session.scalar(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(**values, updated_at=func.now())
            .returning(ImportJob)
            .execution_options(populate_existing=True)
        )

    async def add_errors(self, rows: Sequence[dict[str, Any]]) -> None:
        if rows:
            await self.session.execute(
                pg_insert(ImportJobError).values([{"id": new_id(), **r} for r in rows])
            )

    async def list_errors(self, job_id: UUID) -> list[ImportJobError]:
        return list(
            await self.session.scalars(
                select(ImportJobError)
                .where(ImportJobError.import_job_id == job_id)
                .order_by(ImportJobError.row_number, ImportJobError.id)
            )
        )
