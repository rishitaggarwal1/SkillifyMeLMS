"""FastAPI dependencies: authenticate the caller, pick the active org, and give route handlers a
database session whose RLS context is that principal.

    @router.get("/things")
    async def list_things(principal: CurrentPrincipal, session: TenantSession) -> ...
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.jwt import JwtValidator
from app.core.errors import AuthenticationError, PermissionDeniedError, RateLimitedError
from app.core.jobs import JobQueue
from app.core.ratelimit import RateLimiter
from app.core.redis import RedisClient
from app.db.session import DbSession
from app.db.tenancy import set_tenant_context
from app.modules.audit.service import AuditActor
from app.modules.identity import service
from app.modules.identity.authz import Principal

_bearer = HTTPBearer(auto_error=False, description="Keycloak access token")
_AUTH_FAILURES = "auth_failures"


async def get_principal(
    request: Request,
    redis: RedisClient,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_organization_id: Annotated[
        UUID | None,
        Header(description="Active organization for this request (from the org switcher)."),
    ] = None,
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError
    settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client else "unknown"
    limit = {"limit": settings.rl_auth_failures_per_minute, "window_seconds": 60}
    # Throttle clients that keep presenting bad tokens or probing organizations they don't
    # belong to. Successful requests are never counted.
    if retry_after := await limiter.is_blocked(_AUTH_FAILURES, client_ip, **limit):
        raise RateLimitedError(
            details={"retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
    validator: JwtValidator = request.app.state.jwt_validator
    try:
        claims = await validator.validate(credentials.credentials)
        principal = await service.resolve_principal(
            claims=claims,
            requested_org=x_organization_id,
            sessionmaker=request.app.state.sessionmaker,
            redis=redis,
            cache_ttl_seconds=settings.principal_cache_ttl_seconds,
        )
    except (AuthenticationError, PermissionDeniedError):
        await limiter.hit(_AUTH_FAILURES, client_ip, **limit)
        raise
    request.state.principal = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def get_tenant_session(principal: CurrentPrincipal, session: DbSession) -> AsyncSession:
    """The request's DB session with RLS context = (active org, user, platform-admin flag)."""
    await set_tenant_context(
        session,
        organization_id=principal.organization_id,
        user_id=principal.user_id,
        is_platform_admin=principal.is_platform_admin,
    )
    return session


TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]


def get_audit_actor(request: Request, principal: CurrentPrincipal) -> AuditActor:
    client = request.client
    return AuditActor(
        user_id=principal.user_id,
        organization_id=principal.organization_id,
        is_platform_admin=principal.is_platform_admin,
        ip=client.host if client else None,
        request_id=getattr(request.state, "request_id", None)
        or request.scope.get("state", {}).get("request_id"),
    )


AuditActorDep = Annotated[AuditActor, Depends(get_audit_actor)]


@dataclass(frozen=True, slots=True)
class RequestContext:
    """What a service operation needs about the request: the RLS-scoped session, the caller, the
    audit actor and the background job queue."""

    session: AsyncSession
    principal: Principal
    actor: AuditActor
    jobs: JobQueue
    redis: Redis  # caches (course versions, heartbeat checks); never the source of truth


async def get_request_context(
    request: Request, principal: CurrentPrincipal, session: TenantSession, actor: AuditActorDep
) -> RequestContext:
    return RequestContext(
        session=session, principal=principal, actor=actor, jobs=request.app.state.jobs,
        redis=request.app.state.redis,
    )  # fmt: skip


RequestCtx = Annotated[RequestContext, Depends(get_request_context)]
