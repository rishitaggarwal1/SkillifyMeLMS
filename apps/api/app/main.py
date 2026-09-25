"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, v1
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.redis import create_redis
from app.core.telemetry import configure_tracing, instrument_engine
from app.db.session import create_engine, create_sessionmaker

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        instrument_engine(engine, settings)
        redis = create_redis(settings)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.redis = redis
        logger.info("startup", environment=settings.environment)
        try:
            yield
        finally:
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
