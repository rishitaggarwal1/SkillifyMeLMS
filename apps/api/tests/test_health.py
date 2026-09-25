from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app


async def test_live_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_checks_database_and_redis(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"database", "redis"}
    for check in body["checks"].values():
        assert check["status"] == "ok"
        assert check["error"] is None
        assert check["latency_ms"] >= 0


async def test_ready_returns_503_when_redis_is_unreachable(
    settings: Settings, migrated_database: None
) -> None:
    broken = settings.model_copy(
        update={
            "redis_url": SecretStr("redis://127.0.0.1:1/0"),
            "health_check_timeout_seconds": 1.0,
        }
    )
    app = create_app(broken)

    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "error"
    assert body["checks"]["redis"]["error"]
