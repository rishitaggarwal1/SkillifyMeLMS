"""Shared async Redis client (connection-pooled; one per process, created in the app lifespan)."""

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.core.config import Settings


def create_redis(settings: Settings) -> "Redis":
    return Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=settings.health_check_timeout_seconds,
        socket_timeout=5,
        health_check_interval=30,
    )


def get_redis(request: Request) -> "Redis":
    client: Redis = request.app.state.redis
    return client


RedisClient = Annotated["Redis", Depends(get_redis)]
