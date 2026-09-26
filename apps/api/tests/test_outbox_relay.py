"""Outbox relay against real Postgres (as the `skillify_relay` role) and real Kafka (Redpanda)."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.base import new_id
from app.db.session import create_sessionmaker
from app.events.envelope import TOPICS
from app.events.relay import OutboxRelay

TOPIC = TOPICS["batch_member"]


@pytest.fixture(scope="module")
async def relay_sessionmaker(
    settings: Settings, migrated_database: None
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert settings.relay_database_url is not None, "RELAY_DATABASE_URL must be configured"
    engine = create_async_engine(settings.relay_database_url.get_secret_value())
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
async def producer(settings: Settings) -> AsyncIterator[AIOKafkaProducer]:
    p = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers, acks="all", enable_idempotence=True
    )
    await p.start()
    try:
        yield p
    finally:
        await p.stop()


async def _insert_event(
    owner: async_sessionmaker[AsyncSession], *, aggregate_id: UUID, org: UUID
) -> UUID:
    event_id = new_id()
    async with owner() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO outbox_events (id, organization_id, aggregate_type, aggregate_id, "
                "event_type, payload, headers) VALUES (:id, :org, 'batch_member', :agg, "
                "'batch_member_added', CAST(:payload AS jsonb), '{\"version\": 1}')"
            ),
            {
                "id": event_id,
                "org": org,
                "agg": aggregate_id,
                "payload": json.dumps({"batch_id": str(aggregate_id), "user_id": "u-1"}),
            },
        )
    return event_id


async def _published_at(owner: async_sessionmaker[AsyncSession], event_id: UUID) -> Any:
    async with owner() as s:
        return await s.scalar(
            text("SELECT published_at FROM outbox_events WHERE id = :id"), {"id": event_id}
        )


async def _consume_one(settings: Settings, key: bytes, wait_seconds: float = 20) -> dict[str, Any]:
    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        group_id=None,
    )
    await consumer.start()
    try:
        async with asyncio.timeout(wait_seconds):
            while True:
                batches = await consumer.getmany(timeout_ms=1000)
                for records in batches.values():
                    for record in records:
                        if record.key == key:
                            value: dict[str, Any] = json.loads(record.value)
                            value["_headers"] = dict(record.headers)
                            return value
    finally:
        await consumer.stop()


async def test_relay_publishes_envelope_and_marks_event(
    settings: Settings,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    producer: AIOKafkaProducer,
) -> None:
    aggregate_id, org = new_id(), new_id()
    event_id = await _insert_event(owner_sessionmaker, aggregate_id=aggregate_id, org=org)
    relay = OutboxRelay(relay_sessionmaker, producer, batch_size=100)

    while await _published_at(owner_sessionmaker, event_id) is None:
        assert await relay.publish_batch() > 0

    message = await _consume_one(settings, str(aggregate_id).encode())
    assert message["id"] == str(event_id)
    assert message["type"] == "batch_member_added"
    assert message["version"] == 1
    assert message["organization_id"] == str(org)
    assert message["aggregate"] == {"type": "batch_member", "id": str(aggregate_id)}
    assert message["data"] == {"batch_id": str(aggregate_id), "user_id": "u-1"}
    assert message["_headers"]["event_type"] == b"batch_member_added"


class _FailingProducer:
    async def send(self, *args: Any, **kwargs: Any) -> "asyncio.Future[object]":
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        future.set_exception(ConnectionError("broker down"))
        return future


async def test_failed_publish_leaves_events_unpublished(
    owner_sessionmaker: async_sessionmaker[AsyncSession],
    relay_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    event_id = await _insert_event(owner_sessionmaker, aggregate_id=new_id(), org=new_id())
    relay = OutboxRelay(relay_sessionmaker, _FailingProducer(), batch_size=5000)

    with pytest.raises(ConnectionError):
        await relay.publish_batch()

    assert await _published_at(owner_sessionmaker, event_id) is None


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE outbox_events SET payload = '{}'::jsonb",
        "DELETE FROM outbox_events",
        "INSERT INTO outbox_events (id, aggregate_type, aggregate_id, event_type, payload) "
        "VALUES (gen_random_uuid(), 'x', gen_random_uuid(), 'x', '{}')",
        "SELECT count(*) FROM users",
    ],
)
async def test_relay_role_can_only_read_and_mark_published(
    relay_sessionmaker: async_sessionmaker[AsyncSession], sql: str
) -> None:
    async with relay_sessionmaker() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text(sql))
