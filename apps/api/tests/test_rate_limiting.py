"""Redis sliding-window rate limiting (real Redis) and its use on auth/invite/import endpoints."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from uuid_utils.compat import uuid7

from app.core.ratelimit import RateLimiter
from tests.factories import Factory
from tests.fixtures import AuthHeaders


@contextmanager
def _settings(app: FastAPI, **overrides: Any) -> Iterator[None]:
    original = app.state.settings
    app.state.settings = original.model_copy(update=overrides)
    try:
        yield
    finally:
        app.state.settings = original


async def test_sliding_window(app: FastAPI) -> None:
    limiter = RateLimiter(app.state.redis)
    key = uuid7().hex
    results = [await limiter.hit("t", key, limit=2, window_seconds=1) for _ in range(3)]
    assert [r.allowed for r in results] == [True, True, False]
    assert results[1].remaining == 0
    assert 1 <= results[2].retry_after_seconds <= 1
    await asyncio.sleep(1.05)  # the oldest hits slide out of the window
    assert (await limiter.hit("t", key, limit=2, window_seconds=1)).allowed


async def test_limit_is_atomic_under_concurrency(app: FastAPI) -> None:
    limiter = RateLimiter(app.state.redis)
    key = uuid7().hex
    results = await asyncio.gather(
        *(limiter.hit("t", key, limit=5, window_seconds=60) for _ in range(25))
    )
    assert sum(r.allowed for r in results) == 5


async def test_is_blocked_does_not_count(app: FastAPI) -> None:
    limiter = RateLimiter(app.state.redis)
    key = uuid7().hex
    for _ in range(3):
        assert await limiter.is_blocked("t", key, limit=1, window_seconds=60) == 0
    await limiter.hit("t", key, limit=1, window_seconds=60)
    assert await limiter.is_blocked("t", key, limit=1, window_seconds=60) > 0


async def test_repeated_auth_failures_throttle_the_client(
    app: FastAPI, client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    user = await factory.user()
    with _settings(app, rl_auth_failures_per_minute=3):
        bad = [
            (await client.get("/api/v1/me", headers={"Authorization": "Bearer junk"})).status_code
            for _ in range(3)
        ]
        blocked = await client.get("/api/v1/me", headers={"Authorization": "Bearer junk"})
        even_valid = await client.get("/api/v1/me", headers=auth_headers(user))

    assert bad == [401, 401, 401]
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
    assert even_valid.status_code == 429  # the client IP is throttled, not the token


async def test_probing_foreign_orgs_counts_as_failure(
    app: FastAPI, client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    mine = await factory.org()
    user = await factory.member(mine, "student")
    with _settings(app, rl_auth_failures_per_minute=2):
        for _ in range(2):
            probe = await client.get("/api/v1/me", headers=auth_headers(user, org=uuid7()))
            assert probe.status_code == 403
        assert (await client.get("/api/v1/me", headers=auth_headers(user))).status_code == 429


async def test_successful_requests_are_not_counted(
    app: FastAPI, client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    user = await factory.user()
    with _settings(app, rl_auth_failures_per_minute=1):
        codes = [
            (await client.get("/api/v1/me", headers=auth_headers(user))).status_code
            for _ in range(5)
        ]
    assert codes == [200] * 5


@pytest.mark.usefixtures("fake_idp")
async def test_invites_are_rate_limited_per_user(
    app: FastAPI, client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    org = await factory.org()
    admin = await factory.member(org, "org_admin")
    h = auth_headers(admin, org=org.id)
    with _settings(app, rl_invites_per_hour=2):
        codes = [
            (
                await client.post(
                    "/api/v1/invitations",
                    headers=h,
                    json={"email": f"p{i}.{uuid7().hex[-6:]}@college.test", "roles": ["student"]},
                )
            ).status_code
            for i in range(3)
        ]
    assert codes == [201, 201, 429]


@pytest.mark.usefixtures("enqueued")
async def test_imports_are_rate_limited_per_user(
    app: FastAPI, client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    org = await factory.org()
    admin = await factory.member(org, "org_admin")
    h = auth_headers(admin, org=org.id)
    with _settings(app, rl_imports_per_hour=1):
        codes = [
            (
                await client.post(
                    "/api/v1/imports",
                    headers=h,
                    files={"file": ("s.csv", b"email,full_name\na@college.test,A\n", "text/csv")},
                )
            ).status_code
            for _ in range(2)
        ]
    assert codes == [202, 429]
