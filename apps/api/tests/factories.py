"""Test-data factories. They write with the owner role (bypassing RLS) and commit, so the data is
visible to the runtime-role sessions under test. Every call uses unique names, so tests never
depend on each other's data and the database needs no cleanup between tests."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from app.db.base import new_id
from app.modules.audit.models import AuditLog
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


def _suffix() -> str:
    return uuid7().hex[-12:]


@dataclass
class Factory:
    sessionmaker: async_sessionmaker[AsyncSession]

    async def _save(self, *objs: Any) -> None:
        async with self.sessionmaker() as session, session.begin():
            session.add_all(objs)

    async def org(
        self, *, name: str | None = None, publisher: bool = False, status: str = "active"
    ) -> Organization:
        sfx = _suffix()
        org = Organization(
            id=new_id(),
            name=name or f"Org {sfx}",
            slug=f"org-{sfx}",
            is_content_publisher=publisher,
            status=status,
        )
        await self._save(org)
        return org

    async def user(
        self, *, email: str | None = None, full_name: str | None = None, status: str = "active"
    ) -> User:
        sfx = _suffix()
        user = User(
            id=new_id(),
            keycloak_sub=f"kc-{sfx}",
            email=email or f"user-{sfx}@example.test",
            full_name=full_name or f"User {sfx}",
            status=status,
        )
        await self._save(user)
        return user

    async def member(self, org: Organization, *roles: str, user: User | None = None) -> User:
        """Create (or reuse) a user and give them `roles` in `org`."""
        user = user or await self.user()
        await self._save(
            *(
                Membership(id=new_id(), user_id=user.id, organization_id=org.id, role=role)
                for role in roles
            )
        )
        return user

    async def batch(self, org: Organization, *, name: str | None = None) -> Batch:
        batch = Batch(id=new_id(), organization_id=org.id, name=name or f"Batch {_suffix()}")
        await self._save(batch)
        return batch

    async def add_to_batch(self, batch: Batch, *users: User) -> None:
        await self._save(
            *(
                BatchMember(
                    id=new_id(),
                    batch_id=batch.id,
                    organization_id=batch.organization_id,
                    user_id=u.id,
                )
                for u in users
            )
        )

    async def invitation(
        self, org: Organization, *, email: str | None = None, user: User | None = None
    ) -> Invitation:
        inv = Invitation(
            id=new_id(),
            organization_id=org.id,
            user_id=user.id if user else None,
            email=email or (user.email if user else f"invitee-{_suffix()}@example.test"),
            roles=["student"],
            batch_ids=[],
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        await self._save(inv)
        return inv

    async def import_job(self, org: Organization, *, batch: Batch | None = None) -> ImportJob:
        job = ImportJob(
            id=new_id(),
            organization_id=org.id,
            batch_id=batch.id if batch else None,
            file_key=f"imports/{_suffix()}.csv",
            file_name="students.csv",
        )
        err = ImportJobError(
            id=new_id(),
            import_job_id=job.id,
            organization_id=org.id,
            row_number=2,
            raw={"email": "bad"},
            code="invalid_email",
            message="Invalid email",
        )
        await self._save(job)
        await self._save(err)
        return job

    async def audit(
        self, org: Organization | None, actor: User, action: str = "test.action"
    ) -> UUID:
        entry = AuditLog(
            id=new_id(),
            organization_id=org.id if org else None,
            actor_user_id=actor.id,
            action=action,
            target_type="test",
            target_id="x",
        )
        await self._save(entry)
        return entry.id
