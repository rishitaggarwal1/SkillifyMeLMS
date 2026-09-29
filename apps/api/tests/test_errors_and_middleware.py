"""Error envelope and request-context middleware behaviour, exercised through throwaway routes."""

from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI, Query
from httpx import ASGITransport, AsyncClient
from opentelemetry.util.http import parse_excluded_urls

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.core.telemetry import EXCLUDED_URLS
from app.main import create_app


@pytest.fixture(scope="module")
async def probe_client(settings: Settings, migrated_database: None) -> AsyncIterator[AsyncClient]:
    app: FastAPI = create_app(settings)

    @app.get("/_probe/app-error")
    async def app_error() -> None:
        raise NotFoundError("Course not found.", details={"course_id": "abc"})

    @app.get("/_probe/crash")
    async def crash() -> None:
        msg = "secret internal detail"
        raise RuntimeError(msg)

    @app.get("/_probe/validate")
    async def validate(n: int = Query(ge=1)) -> dict[str, int]:
        return {"n": n}

    async with (
        LifespanManager(app),
        AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client,
    ):
        yield client


async def test_app_error_uses_envelope(probe_client: AsyncClient) -> None:
    response = await probe_client.get("/_probe/app-error")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Course not found.",
            "details": {"course_id": "abc"},
        }
    }


async def test_unknown_route_uses_envelope(probe_client: AsyncClient) -> None:
    response = await probe_client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_validation_error_uses_envelope_without_echoing_input(
    probe_client: AsyncClient,
) -> None:
    response = await probe_client.get("/_probe/validate", params={"n": "0"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["query", "n"]
    assert "input" not in error["details"][0]


async def test_unhandled_exception_returns_500_envelope_with_request_id(
    probe_client: AsyncClient,
) -> None:
    response = await probe_client.get("/_probe/crash")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
            "details": None,
        }
    }
    assert "secret internal detail" not in response.text
    assert response.headers["x-request-id"]


async def test_request_id_is_generated(probe_client: AsyncClient) -> None:
    first = await probe_client.get("/health/live")
    second = await probe_client.get("/health/live")

    assert first.headers["x-request-id"]
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_valid_inbound_request_id_is_propagated(probe_client: AsyncClient) -> None:
    response = await probe_client.get("/health/live", headers={"X-Request-ID": "edge-abc.123"})

    assert response.headers["x-request-id"] == "edge-abc.123"


async def test_malformed_inbound_request_id_is_replaced(probe_client: AsyncClient) -> None:
    response = await probe_client.get(
        "/health/live", headers={"X-Request-ID": 'bad id\twith"stuff'}
    )

    assert response.headers["x-request-id"] != 'bad id\twith"stuff'
    assert len(response.headers["x-request-id"]) == 36  # generated UUIDv7


def test_webhook_secrets_are_not_traced() -> None:
    excluded = parse_excluded_urls(EXCLUDED_URLS)
    assert excluded.url_disabled("http://api:8000/api/v1/webhooks/video/bunny/s3cr3t")
    assert not excluded.url_disabled("http://api:8000/api/v1/videos")
