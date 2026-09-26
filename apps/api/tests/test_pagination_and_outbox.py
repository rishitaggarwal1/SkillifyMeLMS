from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.core.errors import InvalidCursorError
from app.core.pagination import CursorParams, decode_cursor, encode_cursor, paginate_by_id
from app.db.outbox import OutboxEvent, add_outbox_event
from app.db.tenancy import set_tenant_context

# The outbox is RLS-scoped: the runtime role reads/writes only its current org's events.
ORG = uuid7()


@pytest.fixture
async def outbox_session(db_session: AsyncSession) -> AsyncSession:
    await set_tenant_context(db_session, organization_id=ORG, user_id=None)
    return db_session


async def _seed_events(session: AsyncSession, aggregate_id: UUID, count: int) -> list[UUID]:
    events = [
        add_outbox_event(
            session,
            aggregate_type="probe",
            aggregate_id=aggregate_id,
            event_type="probe.created",
            payload={"n": n},
            organization_id=ORG,
        )
        for n in range(count)
    ]
    await session.flush()
    return [e.id for e in events]


async def test_outbox_event_is_persisted_with_defaults(outbox_session: AsyncSession) -> None:
    aggregate_id = uuid7()
    [event_id] = await _seed_events(outbox_session, aggregate_id, 1)

    stored = await outbox_session.get(OutboxEvent, event_id, populate_existing=True)

    assert stored is not None
    assert stored.id.version == 7
    assert stored.payload == {"n": 0}
    assert stored.headers == {}
    assert stored.published_at is None
    assert stored.occurred_at.tzinfo is not None


async def test_paginate_walks_all_pages_newest_first(outbox_session: AsyncSession) -> None:
    aggregate_id = uuid7()
    ids = await _seed_events(outbox_session, aggregate_id, 5)
    stmt = select(OutboxEvent).where(OutboxEvent.aggregate_id == aggregate_id)

    seen: list[UUID] = []
    cursor: str | None = None
    pages = 0
    while True:
        page, cursor = await paginate_by_id(
            outbox_session, stmt, OutboxEvent.id, CursorParams(limit=2, cursor=cursor)
        )
        seen.extend(e.id for e in page)
        pages += 1
        if cursor is None:
            break

    assert pages == 3
    assert seen == sorted(ids, reverse=True)


async def test_paginate_ascending(outbox_session: AsyncSession) -> None:
    aggregate_id = uuid7()
    ids = await _seed_events(outbox_session, aggregate_id, 3)
    stmt = select(OutboxEvent).where(OutboxEvent.aggregate_id == aggregate_id)

    page, cursor = await paginate_by_id(
        outbox_session, stmt, OutboxEvent.id, CursorParams(limit=10), descending=False
    )

    assert [e.id for e in page] == ids
    assert cursor is None


def test_cursor_round_trip() -> None:
    assert decode_cursor(encode_cursor({"id": "x"})) == {"id": "x"}


@pytest.mark.parametrize("bad", ["", "not-base64!!", encode_cursor({"id": "x"})[:-3] + "AAA"])
def test_decode_rejects_garbage(bad: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(bad)


async def test_paginate_rejects_cursor_without_valid_id(outbox_session: AsyncSession) -> None:
    stmt = select(OutboxEvent)
    with pytest.raises(InvalidCursorError):
        await paginate_by_id(
            outbox_session,
            stmt,
            OutboxEvent.id,
            CursorParams(cursor=encode_cursor({"id": "not-a-uuid"})),
        )
