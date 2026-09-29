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
| `course` | `courses.v1` |
| `enrollment` | `learning.enrollments.v1` |
| `video_progress` | `learning.progress.v1` |
| anything else | `platform.events.v1` |

The mapping lives in `apps/api/app/events/envelope.py`. Add a row here whenever you add one there.

**Conventions:**
- **The topic follows the aggregate type**, and the message key is the aggregate id. Every event
  about one aggregate is then on one topic and partition, in the order it was written. So
  `lesson_completed` (aggregate `enrollment`) is on `learning.enrollments.v1`, ordered with that
  enrollment's `enrollment_created` and `enrollment_version_changed`.
- **Topic names** are `<domain>.<aggregate-plural>.v<n>`. The topic version changes only if the
  envelope or keying changes. Payload changes use the envelope's `version`.
- **Versioning rules** for `data`:
  - Additive changes keep the version: a new optional field, or a new enum value that consumers
    are told to ignore when unknown.
  - Removing or renaming a field, changing a type or meaning, or making a field required bumps
    the version. During the transition the producer documents both schemas, and consumers accept
    both.
  - Schemas below use `additionalProperties: false`, so producers can't add fields silently.
    `tests/test_event_schemas.py` validates real payloads against them.
- **Consumers must be idempotent and dedupe on the envelope `id`.** Delivery is at-least-once,
  and ordering holds only per key.

## Event catalogue

| Type | Topic | Emitted when |
|---|---|---|
| `batch_member_added` | `identity.batch-members.v1` | A user joins a batch: added by an admin, through an invitation, or through a CSV import |
| `batch_member_removed` | `identity.batch-members.v1` | A user leaves a batch: removed by an admin, removed from the organization, or their invitation was revoked or expired |
| `course_published` | `courses.v1` | A course version is published. Drives the outline-cache pointer and the public catalog revalidation |
| `enrollment_created` | `learning.enrollments.v1` | A student is newly enrolled (a batch assignment covers them) |
| `lesson_completed` | `learning.enrollments.v1` | A student completes a lesson, once per lesson: marked done, or a video watched past its threshold |
| `enrollment_version_changed` | `learning.enrollments.v1` | An org_admin opted an enrollment into a newer major version |
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

`lesson_completed` is emitted once, when the threshold is reached. Existing completions survive
video replacement.

### `course_published` (version 1)

Keyed by course ID. Emitted in the publish transaction. `organization_id` in the envelope is the
course's owner org.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "course_published.v1 data",
  "type": "object",
  "required": ["course_id", "version_id", "major", "minor", "release_type", "is_public_catalog",
               "published_by"],
  "additionalProperties": false,
  "properties": {
    "course_id": { "type": "string", "format": "uuid" },
    "version_id": { "type": "string", "format": "uuid" },
    "major": { "type": "integer", "minimum": 1 },
    "minor": { "type": "integer", "minimum": 0 },
    "release_type": { "enum": ["major", "minor"] },
    "is_public_catalog": { "type": "boolean" },
    "published_by": { "type": ["string", "null"], "format": "uuid" }
  }
}
```

### `enrollment_created` (version 1)

Keyed by enrollment ID. `organization_id` is the student's org. `assignment_id` is the batch
assignment that enrolled them (null when the enrollment was re-created without one).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "enrollment_created.v1 data",
  "type": "object",
  "required": ["enrollment_id", "user_id", "course_id", "major_version", "assignment_id"],
  "additionalProperties": false,
  "properties": {
    "enrollment_id": { "type": "string", "format": "uuid" },
    "user_id": { "type": "string", "format": "uuid" },
    "course_id": { "type": "string", "format": "uuid" },
    "major_version": { "type": "integer", "minimum": 1 },
    "assignment_id": { "type": ["string", "null"], "format": "uuid" }
  }
}
```

### `lesson_completed` (version 1)

Keyed by enrollment ID. `version_id` is the version the student was shown, and
`progress_percent` is the course progress after this completion (100 only when complete).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "lesson_completed.v1 data",
  "type": "object",
  "required": ["enrollment_id", "user_id", "course_id", "lesson_id", "lesson_type", "version_id",
               "progress_percent"],
  "additionalProperties": false,
  "properties": {
    "enrollment_id": { "type": "string", "format": "uuid" },
    "user_id": { "type": "string", "format": "uuid" },
    "course_id": { "type": "string", "format": "uuid" },
    "lesson_id": { "type": "string", "format": "uuid" },
    "lesson_type": { "enum": ["video", "notes", "pdf", "quiz", "lab", "assignment"] },
    "version_id": { "type": "string", "format": "uuid" },
    "progress_percent": { "type": "integer", "minimum": 0, "maximum": 100 }
  }
}
```

### `enrollment_version_changed` (version 1)

Keyed by enrollment ID. One per moved enrollment when an org_admin opts their org, or chosen
batches, into a newer major version.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "enrollment_version_changed.v1 data",
  "type": "object",
  "required": ["enrollment_id", "user_id", "course_id", "from_major", "to_major",
               "progress_percent", "actor_user_id"],
  "additionalProperties": false,
  "properties": {
    "enrollment_id": { "type": "string", "format": "uuid" },
    "user_id": { "type": "string", "format": "uuid" },
    "course_id": { "type": "string", "format": "uuid" },
    "from_major": { "type": "integer", "minimum": 1 },
    "to_major": { "type": "integer", "minimum": 2 },
    "progress_percent": { "type": "integer", "minimum": 0, "maximum": 100 },
    "actor_user_id": { "type": "string", "format": "uuid" }
  }
}
```
