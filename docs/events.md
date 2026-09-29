# Domain events

Services write domain events to the `outbox_events` table **in the same transaction** as the state
change they describe (`app.db.outbox.add_outbox_event`). The `outbox-relay` service
(`python -m app.cli.outbox_relay`) publishes them to Kafka, which is Redpanda locally.

## Delivery guarantees

- **At-least-once.** The relay publishes a batch, waits for the broker to acknowledge it, then marks
  those events as published. If it crashes in between, the events are published again.
  **Consumers must be idempotent and dedupe on the envelope `id`.**
- **Per-aggregate ordering.** The message key is the aggregate ID, so all events for one aggregate
  go to the same partition, in the order they were written.
- The relay connects as `skillify_relay`, a database role that can only read outbox events and set
  `published_at`.

## Envelope

Every message value is JSON:

```json
{
  "id": "0192…",
  "type": "batch_member_added",
  "version": 1,
  "occurred_at": "2026-09-26T06:16:32.795657+00:00",
  "organization_id": "0192…",
  "aggregate": { "type": "batch_member", "id": "0192…" },
  "data": { }
}
```

| Field | Meaning |
|---|---|
| `id` | Event ID (UUIDv7). Use it to dedupe. |
| `type` | Event type (snake_case, past tense). |
| `version` | Schema version of `data` for this `type`. Additive changes keep the version. Breaking changes bump it; producers then emit the new version, and consumers must handle both during the transition. |
| `occurred_at` | When the change was committed, in ISO-8601 UTC. |
| `organization_id` | Tenant the event belongs to. `null` for platform-level events. |
| `aggregate` | The entity the event is about. Its `id` is also the Kafka message key. |
| `data` | Event-specific payload (schemas below). |

Kafka headers: `event_type` and `event_id` (UTF-8), so consumers can route without parsing the
body.

## Topics

| Aggregate type | Topic |
|---|---|
| `batch_member` | `identity.batch-members.v1` |
| `video_progress` | `learning.progress.v1` |
| anything else | `platform.events.v1` |

The mapping lives in `apps/api/app/events/envelope.py`. Add a row here whenever you add one there.

## Event catalogue

| Type | Topic | Emitted when |
|---|---|---|
| `batch_member_added` | `identity.batch-members.v1` | A user joins a batch: added by an admin, through an invitation, or through a CSV import |
| `batch_member_removed` | `identity.batch-members.v1` | A user leaves a batch: removed by an admin, removed from the organization, or their invitation was revoked or expired |
| `video_progress` | `learning.progress.v1` | A buffered lesson's video progress is flushed to Postgres |

Both events are keyed by `batch_id`, so all changes to one batch arrive in order. Phase 2
(enrollments) consumes them to enroll and unenroll students in the courses assigned to the batch.

### `batch_member_added` (version 1)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "batch_member_added.v1 data",
  "type": "object",
  "required": ["batch_id", "user_id", "organization_id", "actor_user_id", "reason"],
  "additionalProperties": false,
  "properties": {
    "batch_id": { "type": "string", "format": "uuid" },
    "user_id": { "type": "string", "format": "uuid" },
    "organization_id": { "type": "string", "format": "uuid" },
    "actor_user_id": {
      "type": ["string", "null"], "format": "uuid",
      "description": "Who made the change; null for system jobs"
    },
    "reason": { "enum": ["added", "invitation", "import"] }
  }
}
```

### `batch_member_removed` (version 1)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "batch_member_removed.v1 data",
  "type": "object",
  "required": ["batch_id", "user_id", "organization_id", "actor_user_id", "reason"],
  "additionalProperties": false,
  "properties": {
    "batch_id": { "type": "string", "format": "uuid" },
    "user_id": { "type": "string", "format": "uuid" },
    "organization_id": { "type": "string", "format": "uuid" },
    "actor_user_id": { "type": ["string", "null"], "format": "uuid" },
    "reason": { "enum": ["removed", "left_organization", "invitation_revoked"] }
  }
}
```

A user in several batches gets one event per batch. Consumers should treat `added` for an existing
membership, or `removed` for an absent one, as a no-op; together with deduping on the envelope `id`,
that makes replays safe.

### `video_progress` (version 1)

Keyed by enrollment ID. One event per changed lesson per flush, in the same transaction as its
progress update. A persisted buffer revision prevents duplicate emission when Redis acknowledgement
is retried after a committed flush. Relay delivery remains at-least-once; consumers dedupe by `id`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "video_progress.v1 data",
  "type": "object",
  "required": ["enrollment_id", "lesson_id", "video_asset_id", "position_seconds", "watched_ratio"],
  "additionalProperties": false,
  "properties": {
    "enrollment_id": { "type": "string", "format": "uuid" },
    "lesson_id": { "type": "string", "format": "uuid" },
    "video_asset_id": { "type": "string", "format": "uuid" },
    "position_seconds": { "type": "integer", "minimum": 0 },
    "watched_ratio": { "type": "number", "minimum": 0, "maximum": 1 }
  }
}
```

The existing `lesson_completed` event is emitted once when the threshold is reached; existing
completions survive video replacement. The remainder of the Phase 2 event catalogue is step 5.
