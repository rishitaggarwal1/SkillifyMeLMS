"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api import health, v1
from app.core.auth.jwt import HttpJwksSource, JwksCache, JwtValidator
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.ratelimit import RateLimiter
from app.core.redis import create_redis
from app.core.storage import ObjectStorage
from app.core.telemetry import configure_tracing, instrument_engine
from app.core.validation import configure_email_validation
from app.db.session import create_engine, create_sessionmaker
from app.modules.identity.keycloak_admin import KeycloakAdmin
from app.modules.identity.tasks import enqueue_import

logger = get_logger(__name__)


def create_jwt_validator(settings: Settings, http: httpx.AsyncClient) -> JwtValidator:
    jwks = JwksCache(
        HttpJwksSource(http, settings.oidc_jwks_url),
        ttl_seconds=settings.jwks_cache_ttl_seconds,
        refetch_cooldown_seconds=settings.jwks_refetch_cooldown_seconds,
    )
    return JwtValidator(
        jwks,
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        allowed_clients=frozenset(settings.oidc_allowed_clients),
        leeway_seconds=settings.jwt_leeway_seconds,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)
    configure_email_validation(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        instrument_engine(engine, settings)
        redis = create_redis(settings)
        http = httpx.AsyncClient(timeout=10.0)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.redis = redis
        app.state.http = http
        app.state.jwt_validator = create_jwt_validator(settings, http)
        app.state.keycloak_admin = (
            KeycloakAdmin(http, settings) if settings.kc_admin_client_secret else None
        )
        app.state.rate_limiter = RateLimiter(redis)
        app.state.storage = ObjectStorage(settings)
        app.state.enqueue_import = enqueue_import
        logger.info("startup", environment=settings.environment, oidc_issuer=settings.oidc_issuer)
        try:
            yield
        finally:
            await http.aclose()
            await redis.aclose()
            await engine.dispose()
            logger.info("shutdown")

    is_prod = settings.environment == "production"
    app = FastAPI(
        title="SkillifyMe Portal API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.state.settings = settings

    register_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    configure_tracing(app, settings)

    app.include_router(health.router)
    app.include_router(v1.router)
    return app
