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
| anything else | `platform.events.v1` |

The mapping lives in `apps/api/app/events/envelope.py`. Add a row here whenever you add one there.

## Event catalogue

The schemas are added by the phase that starts emitting each event.

| Type | Topic | Emitted when | Schema |
|---|---|---|---|
| `batch_member_added` | `identity.batch-members.v1` | A user joins a batch | Phase 1, step 3 |
| `batch_member_removed` | `identity.batch-members.v1` | A user leaves a batch, or is removed from the org | Phase 1, step 3 |
