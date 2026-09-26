"""FastAPI dependencies: authenticate the caller, pick the active org, and give route handlers a
database session whose RLS context is that principal.

    @router.get("/things")
    async def list_things(principal: CurrentPrincipal, session: TenantSession) -> ...
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.jwt import JwtValidator
from app.core.errors import AuthenticationError
from app.core.redis import RedisClient
from app.db.session import DbSession
from app.db.tenancy import set_tenant_context
from app.modules.audit.service import AuditActor
from app.modules.identity import service
from app.modules.identity.authz import Principal

_bearer = HTTPBearer(auto_error=False, description="Keycloak access token")


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
    validator: JwtValidator = request.app.state.jwt_validator
    claims = await validator.validate(credentials.credentials)
    principal = await service.resolve_principal(
        claims=claims,
        requested_org=x_organization_id,
        sessionmaker=request.app.state.sessionmaker,
        redis=redis,
        cache_ttl_seconds=request.app.state.settings.principal_cache_ttl_seconds,
    )
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
