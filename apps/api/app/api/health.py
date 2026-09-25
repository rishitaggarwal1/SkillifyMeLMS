"""Liveness and readiness probes.

- /health/live: the process is up and serving. No dependency checks (a DB blip must not make the
  orchestrator restart every API pod).
- /health/ready: the instance can serve traffic. Checks Postgres and Redis concurrently with a
  timeout; returns 503 with per-check detail if any fail, so load balancers stop routing to it.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import get_logger
from app.core.redis import RedisClient

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger(__name__)

CheckStatus = Literal["ok", "error"]


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class CheckResult(BaseModel):
    status: CheckStatus
    latency_ms: float
    error: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, CheckResult]


@router.get("/live", operation_id="health_live")
async def live() -> LivenessResponse:
    return LivenessResponse(status="ok")


@router.get(
    "/ready",
    operation_id="health_ready",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
async def ready(request: Request, response: Response, redis: RedisClient) -> ReadinessResponse:
    engine: AsyncEngine = request.app.state.engine
    limit_seconds: float = request.app.state.settings.health_check_timeout_seconds

    async def check_database() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def check_redis() -> None:
        await redis.ping()

    database, cache = await asyncio.gather(
        _run_check("database", check_database, limit_seconds),
        _run_check("redis", check_redis, limit_seconds),
    )
    checks = {"database": database, "redis": cache}
    healthy = all(c.status == "ok" for c in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if healthy else "unavailable", checks=checks)


async def _run_check(
    name: str, check: Callable[[], Awaitable[None]], limit_seconds: float
) -> CheckResult:
    start = time.perf_counter()
    try:
        async with asyncio.timeout(limit_seconds):
            await check()
    except Exception as exc:
        logger.warning("readiness_check_failed", check=name, error=repr(exc))
        return CheckResult(status="error", latency_ms=_elapsed_ms(start), error=type(exc).__name__)
    return CheckResult(status="ok", latency_ms=_elapsed_ms(start))


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)
