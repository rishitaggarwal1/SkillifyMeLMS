"""Principal resolution (re-exported by `app.modules.identity.service`).

A validated access token -> our user (provisioned just-in-time on first
login) + their memberships -> the active organization chosen by the X-Organization-Id header. The
user/membership part is cached in Redis for a short TTL and invalidated whenever memberships change.
"""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth.jwt import TokenClaims
from app.core.errors import ConflictError, PermissionDeniedError
from app.core.logging import get_logger
from app.db.base import new_id
from app.db.tenancy import set_tenant_context
from app.modules.identity.authz import PLATFORM_ADMIN_REALM_ROLE, Principal
from app.modules.identity.models import OrgRole, UserStatus
from app.modules.identity.repository import MembershipRepository, OrganizationRepository

logger = get_logger(__name__)

_CACHE_PREFIX = "identity:principal:"
# SECURITY DEFINER: creates/updates the user without the caller needing any tenant context.
_PROVISION = text("SELECT user_id, user_status FROM app.provision_user(:id, :sub, :email, :name)")


def _cache_key(keycloak_sub: str) -> str:
    return f"{_CACHE_PREFIX}{keycloak_sub}"


@dataclass(frozen=True, slots=True)
class _Identity:
    """The cacheable part of a principal (everything except the per-request active org)."""

    user_id: UUID
    status: str
    email: str
    full_name: str
    memberships: dict[UUID, frozenset[OrgRole]]

    def to_json(self) -> str:
        return json.dumps(
            {
                "user_id": str(self.user_id),
                "status": self.status,
                "email": self.email,
                "full_name": self.full_name,
                "memberships": {str(o): sorted(r) for o, r in self.memberships.items()},
            }
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> "_Identity":
        data: dict[str, Any] = json.loads(raw)
        return cls(
            user_id=UUID(data["user_id"]),
            status=data["status"],
            email=data["email"],
            full_name=data["full_name"],
            memberships={
                UUID(o): frozenset(OrgRole(r) for r in roles)
                for o, roles in data["memberships"].items()
            },
        )


async def _load_identity(
    sessionmaker: async_sessionmaker[AsyncSession], claims: TokenClaims
) -> _Identity:
    async with sessionmaker() as session, session.begin():
        try:
            row = (
                await session.execute(
                    _PROVISION,
                    {"id": new_id(), "sub": claims.sub, "email": claims.email, "name": claims.name},
                )
            ).one()
        except IntegrityError as exc:
            # The email already belongs to a different Keycloak account.
            raise ConflictError(
                "This email address is linked to another account.", code="email_in_use"
            ) from exc
        user_id: UUID = row.user_id
        await set_tenant_context(session, organization_id=None, user_id=user_id)
        memberships = await MembershipRepository(session).list_for_user(user_id)
        # Only active orgs: RLS hides archived orgs (their roles grant nothing).
        visible = {
            o.id
            for o in await OrganizationRepository(session).get_many(
                list({m.organization_id for m in memberships})
            )
        }
    roles: dict[UUID, set[OrgRole]] = {}
    for m in memberships:
        if m.organization_id in visible:
            roles.setdefault(m.organization_id, set()).add(OrgRole(m.role))
    return _Identity(
        user_id=user_id,
        status=row.user_status,
        email=claims.email,
        full_name=claims.name,
        memberships={org: frozenset(r) for org, r in roles.items()},
    )


async def resolve_principal(
    *,
    claims: TokenClaims,
    requested_org: UUID | None,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: "Redis",
    cache_ttl_seconds: int,
) -> Principal:
    if not claims.email_verified:
        raise PermissionDeniedError("Verify your email address first.", code="email_not_verified")

    identity: _Identity | None = None
    key = _cache_key(claims.sub)
    if cache_ttl_seconds > 0:
        cached = await redis.get(key)
        if cached is not None:
            identity = _Identity.from_json(cached)
    if identity is None:
        identity = await _load_identity(sessionmaker, claims)
        if cache_ttl_seconds > 0:
            await redis.set(key, identity.to_json(), ex=cache_ttl_seconds)

    if identity.status == UserStatus.DISABLED:
        raise PermissionDeniedError("This account is disabled.", code="account_disabled")

    is_platform_admin = PLATFORM_ADMIN_REALM_ROLE in claims.realm_roles
    active_org: UUID | None = None
    roles: frozenset[OrgRole] = frozenset()
    if requested_org is not None:
        if requested_org in identity.memberships:
            active_org, roles = requested_org, identity.memberships[requested_org]
        elif is_platform_admin and await _org_exists(sessionmaker, identity, requested_org):
            active_org = requested_org
        else:
            raise PermissionDeniedError(
                "You are not a member of this organization.", code="organization_access_denied"
            )
    elif len(identity.memberships) == 1:
        [(active_org, roles)] = identity.memberships.items()

    return Principal(
        user_id=identity.user_id,
        keycloak_sub=claims.sub,
        email=identity.email,
        full_name=identity.full_name,
        is_platform_admin=is_platform_admin,
        organization_id=active_org,
        roles=roles,
        memberships=identity.memberships,
    )


async def _org_exists(
    sessionmaker: async_sessionmaker[AsyncSession], identity: _Identity, org_id: UUID
) -> bool:
    async with sessionmaker() as session, session.begin():
        await set_tenant_context(
            session, organization_id=None, user_id=identity.user_id, is_platform_admin=True
        )
        return await OrganizationRepository(session).get(org_id) is not None


async def invalidate_principal(redis: "Redis", *keycloak_subs: str) -> None:
    """Drop cached identities, e.g. after membership or status changes."""
    if keycloak_subs:
        await redis.delete(*(_cache_key(s) for s in keycloak_subs))
