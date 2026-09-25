"""Transactional outbox.

Domain events are inserted in the same transaction as the state change that produced them. A relay
(added with the first event-producing feature) publishes unpublished rows to Kafka and stamps
`published_at`. This gives at-least-once delivery without dual writes; consumers must be idempotent
(dedupe on the event `id`).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class OutboxEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        # The relay polls for unpublished events in order; keep that index small with a predicate.
        Index(
            "ix_outbox_events_unpublished",
            "occurred_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    # Nullable: platform-level events are not tied to a tenant.
    organization_id: Mapped[UUID | None] = mapped_column(index=True)
    aggregate_type: Mapped[str] = mapped_column(String(100))
    aggregate_id: Mapped[UUID]
    event_type: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    occurred_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    published_at: Mapped[datetime | None]


def add_outbox_event(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    organization_id: UUID | None,
    headers: dict[str, Any] | None = None,
) -> OutboxEvent:
    """Stage an event in the caller's transaction. It is committed (or rolled back) with it."""
    event = OutboxEvent(
        organization_id=organization_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
        headers=headers or {},
    )
    session.add(event)
    return event
