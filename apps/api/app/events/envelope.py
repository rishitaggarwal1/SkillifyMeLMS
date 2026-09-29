"""Kafka wire format for outbox events. Documented in docs/events.md; keep them in sync.

Envelope (JSON value):
    {
      "id": "<event uuid>",           consumers dedupe on this
      "type": "batch_member_added",
      "version": 1,                   schema version of `data` for this type
      "occurred_at": "<ISO-8601 UTC>",
      "organization_id": "<uuid>" | null,
      "aggregate": {"type": "batch_member", "id": "<uuid>"},
      "data": { ...event-specific payload... }
    }

Message key = aggregate id, so all events for one aggregate land on one partition, in order.
"""

import json
from typing import Any

from app.db.outbox import OutboxEvent

# Topic per aggregate type. Unlisted aggregate types go to the catch-all topic.
TOPICS: dict[str, str] = {
    "batch_member": "identity.batch-members.v1",
    "video_progress": "learning.progress.v1",
}
DEFAULT_TOPIC = "platform.events.v1"


def topic_for(event: OutboxEvent) -> str:
    return TOPICS.get(event.aggregate_type, DEFAULT_TOPIC)


def envelope(event: OutboxEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "type": event.event_type,
        "version": int(event.headers.get("version", 1)),
        "occurred_at": event.occurred_at.isoformat(),
        "organization_id": str(event.organization_id) if event.organization_id else None,
        "aggregate": {"type": event.aggregate_type, "id": str(event.aggregate_id)},
        "data": event.payload,
    }


def encode(event: OutboxEvent) -> tuple[bytes, bytes, list[tuple[str, bytes]]]:
    """(key, value, headers) for the Kafka record."""
    value = json.dumps(envelope(event), separators=(",", ":"), default=str).encode()
    headers = [("event_type", event.event_type.encode()), ("event_id", str(event.id).encode())]
    return str(event.aggregate_id).encode(), value, headers
