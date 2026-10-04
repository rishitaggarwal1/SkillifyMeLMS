"""Request writes must be durable before success headers leave the API."""

from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import Message, Receive, Scope, Send
from uuid_utils.compat import uuid7

from app.db.outbox import OutboxEvent
from app.db.session import DbSession, run_after_commit, run_after_commit_async
from app.db.tenancy import set_tenant_context


async def _stage_event(session: AsyncSession, event_id: UUID, hooks: list[str]) -> None:
    await set_tenant_context(session, organization_id=None, user_id=None, is_platform_admin=True)
    session.add(
        OutboxEvent(
            id=event_id,
            organization_id=None,
            aggregate_type="transaction_probe",
            aggregate_id=uuid7(),
            event_type="probe.created",
            payload={"visible": True},
        )
    )
    await session.flush()
    run_after_commit(session, lambda: hooks.append("sync"))

    async def after_commit() -> None:
        hooks.append("async")

    run_after_commit_async(session, after_commit)


async def test_api_write_visible_on_separate_connection_before_response(
    app: FastAPI, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    event_id = uuid7()
    hooks: list[str] = []
    writer_pid: int | None = None
    observed = False

    @app.post("/_probe/durable-write", status_code=201)
    async def write(session: DbSession) -> dict[str, str]:
        nonlocal writer_pid
        writer_pid = await session.scalar(text("SELECT pg_backend_pid()"))
        await _stage_event(session, event_id, hooks)
        return {"id": str(event_id)}

    async def observe_response(scope: Scope, receive: Receive, send: Send) -> None:
        async def observe_send(message: Message) -> None:
            nonlocal observed
            if message["type"] == "http.response.start" and message["status"] == 201:
                # ASGITransport normally awaits cleanup, hiding a commit after response delivery.
                # Read on a separate engine's connection at the instant success headers are sent.
                async with owner_sessionmaker() as reader:
                    assert await reader.scalar(text("SELECT pg_backend_pid()")) != writer_pid
                    stored = await reader.get(OutboxEvent, event_id)
                    assert stored is not None
                    assert stored.payload == {"visible": True}
                assert hooks == ["sync", "async"]
                observed = True
            await send(message)

        await app(scope, receive, observe_send)

    async with AsyncClient(
        transport=ASGITransport(app=observe_response), base_url="http://test"
    ) as client:
        response = await client.post("/_probe/durable-write")

    assert response.status_code == 201
    assert response.json() == {"id": str(event_id)}
    assert observed
    async with owner_sessionmaker() as reader:
        assert await reader.get(OutboxEvent, event_id) is not None


async def test_failing_commit_returns_error_and_writes_nothing(
    app: FastAPI, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    event_id = uuid7()
    hooks: list[str] = []
    endpoint_returned = False

    @app.post("/_probe/failing-commit", status_code=201)
    async def write(session: DbSession) -> dict[str, str]:
        nonlocal endpoint_returned
        await _stage_event(session, event_id, hooks)
        # This real constraint is checked at COMMIT, after the endpoint has returned successfully.
        await session.execute(
            text(
                "CREATE TEMP TABLE commit_probe "
                "(id integer PRIMARY KEY DEFERRABLE INITIALLY DEFERRED) ON COMMIT DROP"
            )
        )
        await session.execute(text("INSERT INTO commit_probe (id) VALUES (1), (1)"))
        endpoint_returned = True
        return {"id": str(event_id)}

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post("/_probe/failing-commit")

    assert endpoint_returned
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
            "details": None,
        }
    }
    assert response.headers["x-request-id"]
    assert hooks == []
    async with owner_sessionmaker() as reader:
        assert await reader.get(OutboxEvent, event_id) is None
