"""Outbox → Kafka relay.

Claims up to N unpublished events with `FOR UPDATE SKIP LOCKED` (so several relay replicas can run
safely), publishes them, waits for broker acknowledgement, then stamps `published_at` in the same
transaction. If publishing fails the transaction rolls back and the events are retried: delivery is
at-least-once and consumers must dedupe on the event id.

Connects as the `skillify_relay` role, which may only read outbox events and set published_at.
"""

import asyncio
from contextlib import suppress
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.outbox import OutboxEvent
from app.events.envelope import encode, topic_for

logger = get_logger(__name__)


class Producer(Protocol):
    async def send(
        self,
        topic: str,
        *,
        value: bytes | None = None,
        key: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> "asyncio.Future[object]": ...


class OutboxRelay:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        producer: Producer,
        *,
        batch_size: int = 500,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.producer = producer
        self.batch_size = batch_size

    async def publish_batch(self) -> int:
        """Publish one batch; returns how many events were published."""
        async with self.sessionmaker() as session, session.begin():
            events = list(
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            if not events:
                return 0
            acks = []
            for event in events:
                key, value, headers = encode(event)
                acks.append(
                    await self.producer.send(
                        topic_for(event), value=value, key=key, headers=headers
                    )
                )
            await asyncio.gather(*acks)  # raises if any send failed -> rollback, retry later
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id.in_([e.id for e in events]))
                .values(published_at=func.now())
            )
        logger.info("outbox_published", count=len(events))
        return len(events)

    async def run(self, stop: asyncio.Event, *, poll_interval: float) -> None:
        while not stop.is_set():
            try:
                published = await self.publish_batch()
            except Exception:
                logger.exception("outbox_relay_error")
                published = 0
                await asyncio.sleep(min(poll_interval * 10, 5))
            if published == 0:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)
