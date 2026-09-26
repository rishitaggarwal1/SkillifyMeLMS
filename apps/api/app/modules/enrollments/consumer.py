"""Kafka consumer for batch membership events (`identity.batch-members.v1`).

A student joining or leaving a batch (or the organization) changes which courses they should be
enrolled in. Each event triggers `reconcile_student`, which recomputes that student's enrollments
from the current state, so redelivered (at-least-once) or reordered events are harmless.

Offsets are committed only after the handler's transaction commits.
"""

import asyncio
import json
from collections.abc import Iterable
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.tenancy import system_transaction
from app.events.envelope import TOPICS
from app.modules.enrollments.service import reconcile_student

logger = get_logger(__name__)

TOPIC = TOPICS["batch_member"]
GROUP_ID = "enrollments.batch-members"
HANDLED_TYPES = frozenset({"batch_member_added", "batch_member_removed"})


class Record(Protocol):
    value: bytes | None
    offset: int


class Consumer(Protocol):
    async def getmany(self, *, timeout_ms: int, max_records: int) -> dict[Any, list[Any]]: ...

    async def commit(self) -> None: ...


class _MemberData(BaseModel):
    organization_id: UUID
    user_id: UUID


class _MemberEvent(BaseModel):
    type: str
    data: _MemberData


def parse_event(raw: bytes | None) -> _MemberEvent | None:
    """The event if it's a well-formed batch membership event, else None (logged and skipped:
    retrying a malformed event can never succeed)."""
    try:
        envelope = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        logger.warning("batch_member_event_malformed")
        return None
    if not isinstance(envelope, dict) or envelope.get("type") not in HANDLED_TYPES:
        return None
    try:
        return _MemberEvent.model_validate(envelope)
    except ValidationError:
        logger.warning("batch_member_event_invalid", event_id=envelope.get("id"))
        return None


async def handle_event(
    sessionmaker: async_sessionmaker[AsyncSession], envelope: dict[str, Any]
) -> bool:
    """Apply one event; False if it isn't a valid event this consumer handles."""
    event = parse_event(json.dumps(envelope).encode())
    if event is None:
        return False
    await _apply(sessionmaker, event)
    return True


async def _apply(sessionmaker: async_sessionmaker[AsyncSession], event: _MemberEvent) -> None:
    org_id = event.data.organization_id
    async with system_transaction(sessionmaker, organization_id=org_id) as session:
        await reconcile_student(session, org_id, event.data.user_id)


async def handle_records(
    sessionmaker: async_sessionmaker[AsyncSession],
    records: Iterable[Record],
    *,
    attempts: int = 5,
    backoff_seconds: float = 0.5,
) -> int:
    handled = 0
    for record in records:
        event = parse_event(record.value)
        if event is None:
            continue
        for attempt in range(1, attempts + 1):
            try:
                await _apply(sessionmaker, event)
                handled += 1
                break
            except Exception:
                logger.exception("batch_member_event_failed", offset=record.offset, attempt=attempt)
                if attempt == attempts:
                    # Give up on this event; the next membership change for the student (or a
                    # course reconcile) repairs their enrollments.
                    break
                await asyncio.sleep(backoff_seconds * attempt)
    return handled


async def run(
    consumer: Consumer,
    sessionmaker: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
    *,
    max_records: int = 200,
) -> None:
    while not stop.is_set():
        batches = await consumer.getmany(timeout_ms=1000, max_records=max_records)
        records = [r for partition in batches.values() for r in partition]
        if records:
            await handle_records(sessionmaker, records)
            await consumer.commit()
