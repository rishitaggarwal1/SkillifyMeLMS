# Phase 2 — Courses and learning content (plan)

**Status:** planned, **blocked on Phase 1** (identity, organizations, roles, RLS).
Resume by re-reading `CLAUDE.md`, confirming every item in [Prerequisites from Phase 1](#prerequisites-from-phase-1)
exists, adjusting this plan to Phase 1's actual names, and presenting the updated plan for approval.

Plan written 2026-09-26 against foundation commit `1ea3650`.

## Scope (as requested)

- Courses → modules → lessons. Lesson types: video, notes, pdf, quiz, lab, assignment (quiz, lab
  and assignment are placeholders filled in by later phases). Drag-and-drop ordering.
- Course versioning: draft vs published. Publishing snapshots a version so enrolled students are not
  affected by later edits.
- Hierarchical skills taxonomy (e.g. DSA > Arrays > Two Pointers). Lessons are tagged now;
  questions and problems are tagged in later phases.
- Assign courses to organizations and batches; `enrollments` table; students see only courses
  assigned to them.
- Video behind a `VideoProvider` interface, with a Bunny Stream implementation:
  - direct signed uploads from the browser
  - processing-status webhook
  - signed, expiring playback URLs
  - hls.js player
- Video progress: heartbeat every 15 s, buffered in Redis, flushed to Postgres in batches by Celery.
  Resume from the last position.
- Notes:
  - edited in Tiptap and stored as JSON
  - rendered as sanitized HTML with syntax-highlighted code blocks
- PDFs: uploaded to S3 via presigned URLs and served through signed URLs.
- Completion rules per lesson type, and a course progress percentage.
- Caching:
  - published course outlines cached in Redis and invalidated on publish
  - the public catalog statically generated with revalidation
- Domain events (`lesson_completed`, `video_progress`, `enrollment_created`) go through the outbox
  to Kafka. Schemas are documented in `docs/events.md`.
- Web:
  - instructor course builder (outline editor and lesson editors)
  - student course player (outline sidebar, resume, progress)
  - student dashboard with "Continue learning"

**Done when:** an instructor builds and publishes a course with video, notes and PDF lessons, and a
student in an assigned batch completes it with correct progress. Tests cover versioning, access
control, and progress calculation.

## Decisions (2026-09-26)

1. **Identity is Phase 1's job.** Phase 2 builds no identity pieces of its own.
2. **Video provider:**
   - The Bunny Stream credentials aren't available yet.
   - `make dev` and CI use `LocalVideoProvider`, backed by MinIO.
   - `BunnyStreamProvider` is covered by mocked-HTTP tests (`respx`).
   - **Deliverable:** `scripts/smoke_test_bunny.py`, run manually once real Bunny credentials are in
     `.env` (see [Media](#media-video-pdf-notes)).
3. **Course ownership and cross-organization sharing** is recorded in `CLAUDE.md` under "Content
   ownership & sharing":
   - Every course has an owner organization.
   - `organizations.is_content_publisher` marks publishers, such as SkillifyMe's own org.
   - A course is visible to its owner org, and to any org it's assigned to through
     `course_assignments (course_id, organization_id, batch_id NULL)`.
   - Only owner-org members with the `instructor` or `org_admin` role can edit. Assigned orgs get
     read and enroll access only.
   - Any org can author private courses that are visible only to itself.
   - All of this is enforced by RLS policies, with tests proving that:
     - an assigned org cannot edit
     - an unassigned org cannot see
     - a batch-level assignment limits visibility to that batch's students
4. **Owner-org students see nothing until their batch is assigned.**
   - Students in the owning org cannot see a course until it is assigned to their batch.
   - Instructors and org_admins of the owning org can see and preview both drafts and published
     versions.
5. **Two-level assignment:**
   - The publisher assigns a course to an organization, or directly to specific batches.
   - That organization's org_admin chooses which of their batches receive it.
   - An org_admin can only narrow within what was assigned to them, never widen it.
   - Batch-level visibility is enforced by RLS.
   - See [Visibility and RLS](#visibility-and-rls-decisions-3-5).
6. **Minor and major versions:**
   - When publishing, the author marks the release as **minor** (corrections only: no lessons added,
     removed or reordered) or **major**.
   - Minor versions apply automatically to all existing enrollments.
   - Major versions apply only to new enrollments. An org_admin can opt their org's existing
     enrollments into the new major version. Progress carries over for lessons whose stable lesson
     ID still exists.
   - Lessons have stable IDs across versions.
   - See [Versioning](#versioning-decision-6).

## Prerequisites from Phase 1

Phase 2 assumes Phase 1 delivers the following. If a name differs, adapt this plan; don't duplicate
Phase 1's work.

### Data (owned by the identity module)

- `organizations`: `id`, `name`, `slug`, and **`is_content_publisher boolean NOT NULL DEFAULT false`**.
  Phase 1 should add the flag so Phase 2 doesn't need an identity migration.
- `users`: `id`, Keycloak `sub`, `email`, `name`.
- `org_memberships`:
  - `organization_id`, `user_id`, `role`
  - roles used by Phase 2: `org_admin`, `instructor`, `student`
  - plus a platform-level `super_admin`
- `batches (id, organization_id, name)` and `batch_members (batch_id, user_id, organization_id)`.
  - `batches` needs a **`UNIQUE (id, organization_id)`** constraint, so `course_assignments` can use
    the composite foreign key `(batch_id, organization_id) → batches(id, organization_id)`. That key
    guarantees an assigned batch belongs to the assigned org (decision 5).
- All of the above covered by tenant RLS, per `CLAUDE.md`.
- Roles are **per organization**: one user can be an `instructor` in SkillifyMe and hold no role in
  Demo College. The role helpers below must evaluate against a specific org, not "any org".

### SQL helpers that RLS policies can call

Other modules' RLS policies must not query identity tables directly (the module-boundary rule). So
identity should expose `STABLE`, `SECURITY DEFINER` functions in the `app` schema, which act as its
database-level interface:

- `app.current_org_id()` and `app.current_user_id()` already exist (migration `0001`).
- `app.current_user_has_role(org uuid, roles text[]) → boolean`. Used for:
  - owner-org editing and draft preview (`instructor`, `org_admin`), decision 4
  - an assigned org's `org_admin` narrowing the assignment to batches, decision 5
  - opting enrollments into a new major version, decision 6
- `app.current_user_in_batch(batch uuid) → boolean`. Used for batch-level student visibility.
- `app.org_is_content_publisher(org uuid) → boolean`. Used by the RLS rule that only publishers may
  assign courses to *other* orgs.
- Optionally `app.current_user_is_super_admin() → boolean`.
- These functions must be `SECURITY DEFINER` with a fixed `search_path`, and must not recurse into
  the RLS on the tables they read.

### Request context and auth

- A FastAPI dependency, for example `CurrentPrincipal`, carrying `user_id`, the active
  `organization_id`, and the user's roles in that org. It must call `set_tenant_context(...)` inside
  the request transaction.
- An httpOnly session cookie, plus CSRF protection for changing requests (POST, PUT, etc.).
  Reachable through the web `/backend` proxy.
- Role checks available to service layers, e.g. `identity.service.require_role(principal, {...})`.
  The service layer does the same checks as RLS so users get clear errors. RLS is the backstop.

### Service interface (used by the enrollments module)

- A streaming or paginated list of the student user IDs in a batch, for enrollment fan-out.
  Enrollment happens per batch only, so there's no org-wide student list (decisions 4 and 5).
- A way to list an org's batches (for the org_admin's batch picker), and to check that a batch
  belongs to an org.
- A membership-change signal (`batch_member_added` / `batch_member_removed`), either as an outbox
  event or through an in-process subscriber hook, so enrollments can react when a student joins a
  batch after a course was assigned to it. **Phase 1 should choose the mechanism; Phase 2 consumes
  it.**

### Platform pieces (Phase 1 builds these if it needs them; otherwise Phase 2 does)

- The outbox → Kafka relay service (see [Events](#events-outbox-relay-caching-catalog)).
- A Celery beat service in `docker-compose.yml`.

### Seed data and test support

- `make seed`, creating:
  - **SkillifyMe**: `is_content_publisher = true`, with an instructor.
  - **Demo College** with two batches, **CSE 2026** and **ECE 2026**, and these users:
    - an org_admin
    - an instructor
    - a student in CSE 2026
    - a student in ECE 2026 (needed for the batch-visibility test)
  - **Other College**: an unrelated org with a student, an instructor and an org_admin, needed for
    the unassigned-org test and the "an org_admin of another org can't upgrade" test.
  - The Demo College org_admin is needed for the narrowing and major-upgrade flows (decisions 5
    and 6).
- The matching Keycloak dev-realm users, so Playwright can log in through the real login page.
- pytest helpers:
  - factories for orgs, users, memberships and batches
  - an authenticated test client for a given principal, without going through Keycloak

### Web

- Login and logout, and a `useMe()` query.
- Route protection in `proxy.ts` for `/learn` and `/teach`.

## Backend design

Each module follows the `CLAUDE.md` layout and talks to other modules only through their
`service.py`.

| Module | Owns |
|---|---|
| `skills` | `skills` taxonomy, `lesson_skills` tags |
| `courses` | `courses`, `course_modules`, `lessons` (draft tree); `course_versions`, `course_version_lessons`; `course_assignments`; `catalog_entries` |
| `enrollments` | `enrollments`, `lesson_progress`, the video heartbeat buffer, completion rules |
| `media` | `video_assets` (behind `VideoProvider`), `files` (PDFs in S3) |
| events (`app/core`) | outbox → Kafka relay, unless Phase 1 already built it |

### Courses, lessons, ordering

- **`courses`:**
  - `organization_id` is the **owner** org
  - `title`, `slug`, `description`, `is_public_catalog`
  - `revision` (optimistic concurrency counter) and `current_version_id`
- **Lesson content:** `lessons.content` is JSONB, validated by a Pydantic model per lesson type:
  - video → `{video_asset_id, completion_threshold}`
  - notes → a Tiptap document
  - pdf → `{file_id}`
  - quiz, lab, assignment → `{}`, with `is_required = false` so they don't count toward progress
    until their phases land
- **Drag-and-drop ordering:** `PUT /courses/{id}/modules/order` and `PUT /modules/{id}/lessons/order`
  take the full ordered list of IDs.
  - Every edit bumps the course's `revision`.
  - Clients send the revision they started from (`If-Match`). A stale save gets `409 conflict`.
  - Moving a lesson between modules is one call.

### Versioning (decision 6)

- **Publishing** creates an immutable `course_versions` row containing:
  - `major`, `minor` (unique per course) and `release_type` (`major` | `minor`)
  - `snapshot` (JSONB: the full outline, lesson metadata, and notes pre-rendered to sanitized HTML)
  - `published_by`, `published_at`, and optional `release_notes`

  It also writes **`course_version_lessons`** rows (`version_id`, `lesson_id`, `module_id`,
  `position`, `type`, `is_required`, `completion_threshold`, `video_asset_id`,
  `video_duration_seconds`), so progress is computed with SQL instead of by parsing JSON.
- **The first publish is always 1.0.** After that the author picks the release type:
  - **Major** → `(major + 1).0`.
  - **Minor** → `major.(minor + 1)`. A minor is **rejected by the server (`409 minor_not_allowed`)**
    if, compared with the latest version of the same major, anything structural changed:
    - modules added, removed or reordered
    - lessons added, removed, reordered or moved between modules
    - a lesson's `type`, `is_required` or `completion_threshold` changed

    Content corrections are allowed: titles, notes text, a replacement PDF, a replacement video
    asset, and skill tags. The publish dialog shows this structural diff and disables "minor" when
    it isn't allowed.
- **Stable lesson IDs:** a lesson's ID never changes while it exists in the draft, including when it
  moves between modules. A deleted lesson that gets re-created gets a new ID. Progress is keyed by
  `(enrollment_id, lesson_id)`.
- **Enrollments pin a major version (`enrollments.major_version`), not a specific version.**
  - The version a student sees is the latest published minor within their major. It's resolved at
    read time from a cached pointer, `course:{id}:major:{n}:latest`, which is invalidated on
    publish.
  - That makes minor releases apply to all existing enrollments automatically, with **no mass
    update** across other orgs' enrollments. RLS would block that update anyway, because
    enrollments belong to the students' orgs.
  - Minor releases can't change the required lesson set, so stored progress percentages stay
    correct.
  - A video asset replaced in a minor release:
    - lessons already completed stay completed
    - in-progress watch bitmaps are reset on their next read, because `lesson_progress` stores the
      `video_asset_id` it was recorded against, and a mismatch triggers the reset
- **New enrollments** get the course's latest major version.
- **Opting into a new major version (org_admin):**
  - `POST /api/v1/courses/{course_id}/enrollment-upgrades {to_major, batch_ids?}`, allowed only for
    the `org_admin` of the enrollments' org. RLS limits it to their own org's enrollments.
  - A Celery job moves matching enrollments (`major_version < to_major`, optionally filtered by
    batch) in chunks, and recomputes each enrollment's percentage against the new major's required
    lessons. Completed lessons whose stable ID still exists carry over. Completions for removed
    lessons are kept in `lesson_progress` but no longer count.
  - `completed_at` is cleared if the new major adds required lessons the student hasn't done.
  - Each moved enrollment writes an `enrollment_version_changed` outbox event.
- Publishing is rejected when the course has no lessons, or when a video lesson's asset isn't
  `ready`.

### Skills

- `skills (id, organization_id NULL, parent_id, name, slug, path ltree)` with a GiST index on `path`.
  Subtree queries use `path <@ 'dsa.arrays'`.
- `organization_id IS NULL` means a platform-wide skill, which is read-only for org users. The RLS
  policy lets users see global skills plus their own org's.
- `lesson_skills (lesson_id, skill_id)`. Questions and problems get tagged the same way in later
  phases.
- The migration enables the `ltree` extension.

### Visibility and RLS (decisions 3–5)

Policies are written separately for each operation (`FOR SELECT`, `FOR INSERT`, `FOR UPDATE`,
`FOR DELETE`) and use the Phase 1 helpers.

**`course_assignments`** (two levels):

| Column | Meaning |
|---|---|
| `course_id` | the course |
| `organization_id` | the org receiving the course |
| `batch_id` NULL | NULL = an **org grant**: an entitlement the org_admin distributes, which gives students no visibility. Set = a **batch assignment**: visible to that batch's students. |
| `assigned_by_org_id` | the owner org (publisher-made) or the receiving org (org_admin-made) |
| `parent_assignment_id` NULL | for org_admin-made batch rows, the org grant they narrow. `ON DELETE CASCADE`. |

A composite foreign key `(batch_id, organization_id) → batches(id, organization_id)` guarantees the
batch belongs to the receiving org. Uniqueness is `(course_id, organization_id, batch_id)`, with
`NULLS NOT DISTINCT` so there's at most one grant per org.

**Who can do what:**

- **Course editors** are owner-org members with the `instructor` or `org_admin` role:
  `organization_id = app.current_org_id() AND app.current_user_has_role(organization_id, '{instructor,org_admin}')`.
  - Only editors may INSERT, UPDATE or DELETE on `courses`, `course_modules`, `lessons` and
    `lesson_skills`.
  - Only editors can SELECT the draft tables (`course_modules`, `lessons`). They can preview drafts
    and every published version (decision 4).
- **Publisher-made assignments** (`assigned_by_org_id = owner org`), created and deleted only by the
  course's editors:
  - To **their own** org's batches: any owner org.
  - To **another** org, either as an org grant or directly to that org's batches: only if
    `app.org_is_content_publisher(owner org)`.
- **Org_admin narrowing (decision 5).** The receiving org's `org_admin` may INSERT a batch row only
  when all of these hold:
  - `organization_id = app.current_org_id()`
  - `app.current_user_has_role(organization_id, '{org_admin}')`
  - `batch_id IS NOT NULL`
  - an org grant exists for `(course_id, organization_id)`, and `parent_assignment_id` points to it
  - `assigned_by_org_id = organization_id`

  They may DELETE only batch rows their own org created. They can **never** create an org grant,
  touch another org's rows, or assign a course their org wasn't granted, so they can narrow but never
  widen.
- **Reading `courses`, `course_versions` and `course_version_lessons`**, any of:
  - an editor of the owner org (drafts and all versions)
  - `org_admin` or `instructor` of an org holding a grant or batch assignment for the course, to
    read and manage it
  - a user in a batch with a batch assignment for the course
    (`app.current_user_in_batch(batch_id)`). **Students get access only this way, including students
    of the owner org (decision 4).**
  - Readers see published versions only. Draft tables stay editor-only.
- **Reading `course_assignments`:**
  - owner-org editors see every assignment of their courses
  - the receiving org's `org_admin` sees their org's rows
  - students see nothing; they don't need to
- **`enrollments` and `lesson_progress`:**
  - `organization_id` is the **student's** org, and tenant RLS applies.
  - A student sees only their own rows. Instructors and org_admins see their org's rows.
- **Nothing extra for deep links:** anything a user may not see returns 404 (via RLS), never 403, so
  course existence isn't revealed.
- **Required RLS tests:**
  - an assigned org cannot edit (UPDATE and DELETE affect 0 rows, and the API returns 404)
  - an unassigned org cannot see the course (SELECT returns 0 rows, even with a raw query)
  - a batch-level assignment shows the course to CSE 2026 students but not to ECE 2026 students in
    the same org
  - an org grant alone shows the course to that org's org_admin, but to **no** students
  - owner-org students can't see a course until their batch is assigned; owner-org instructors and
    org_admins can preview drafts (decision 4)
  - an org_admin can narrow a grant to their own batches, but can't:
    - create an org grant
    - assign a course their org wasn't granted
    - assign another org's batch (the composite foreign key and RLS both block it)
    - delete publisher-made rows
  - deleting an org grant cascades to the batch rows narrowed from it
  - a private course is invisible to every other org
  - a non-publisher org cannot assign to another org

### Enrollment fan-out

- Enrollment happens **per batch assignment** only; org grants enroll nobody.
- Creating a batch assignment enqueues a Celery job. It pages through that batch's student IDs using
  the identity service and bulk-inserts enrollments at the course's latest major version, with
  `ON CONFLICT DO NOTHING` so it's safe to re-run.
- A `batch_member_added` signal (from Phase 1) enrolls the new member in the batch's assigned
  courses.
- **Removing** a batch assignment, directly or by cascade, revokes access through RLS immediately.
  - Enrollments are marked `revoked` unless another batch assignment still covers the student (a
    student can be in several batches).
  - Progress is kept, so re-assigning restores it.
- Each new enrollment writes an `enrollment_created` outbox event.

### Media (video, PDF, notes)

- **`VideoProvider` protocol:**
  - `create_upload(title) → UploadTicket {asset_id, protocol: "tus" | "s3_put", endpoint, headers, expires_at}`
  - `handle_webhook(request) → asset_id | None`
  - `refresh_status(asset) → status, duration_seconds, thumbnail`
  - `playback(asset, ttl) → {url, kind: "hls" | "mp4", expires_at}`
- **`BunnyStreamProvider`:**
  - **Upload:** creates the video through the Bunny API, then returns a TUS endpoint with Bunny's
    presigned headers: `AuthorizationSignature = sha256(library_id + api_key + expiration + video_id)`,
    `AuthorizationExpire`, `VideoId`, and `LibraryId`. The browser uploads directly with
    `tus-js-client`.
  - **Webhook:** `POST /api/v1/webhooks/video/bunny/{secret}` compares the secret in constant time.
    It never trusts the body: it re-fetches the video's status from the Bunny API before updating the
    asset.
  - **Playback:** Bunny CDN token-authenticated HLS URLs (signed with the token key, short expiry).
- **`LocalVideoProvider`** (`VIDEO_PROVIDER=local`, used by `make dev` and CI):
  - the browser uploads with a presigned MinIO PUT
  - "processing" is a Celery task that marks the asset ready and reads its duration with a small
    MP4 header parse, simulating the webhook
  - playback is a signed MinIO GET URL for the MP4 (`kind: "mp4"`)
- **Player:** native `<video>` for MP4 and for browsers that play HLS natively. Otherwise the
  `hls.js` light build is dynamically imported, on video lessons only.
- **Deliverable, `scripts/smoke_test_bunny.py`:** a manual script that uses real credentials from
  `.env` (`BUNNY_LIBRARY_ID`, `BUNNY_API_KEY`, `BUNNY_CDN_HOSTNAME`, `BUNNY_TOKEN_KEY`,
  `BUNNY_WEBHOOK_SECRET`). It exercises **our** `BunnyStreamProvider`:
  1. `create_upload`, then upload a small bundled MP4 fixture over TUS using the returned signed
     headers.
  2. Poll `refresh_status` until the video is `ready`, with a timeout. Then POST a Bunny-shaped
     webhook payload to the local API's webhook endpoint and check that the asset becomes `ready`
     through the webhook path.
  3. Request a signed playback URL. Check that it returns HTTP 200 with an HLS manifest, and that a
     tampered or expired token is rejected (403).
  4. Delete the test video (cleanup), and print a pass/fail summary. The script exits non-zero on
     failure.

  It refuses to run without all the variables set, and is never run in CI.
- **PDF uploads (`files` table):**
  - the API issues a presigned **POST** that enforces `Content-Type: application/pdf` and a
    `content-length-range` (default maximum 25 MB)
  - `POST /files/{id}/confirm` checks the stored object's size and its `%PDF-` magic bytes before the
    file can be attached to a lesson
  - downloads use signed GET URLs that expire in 5 minutes
  - buckets stay private
  - `S3_PUBLIC_ENDPOINT_URL` (browser-reachable) is separate from `S3_ENDPOINT_URL` (in-network)
  - MinIO CORS allows the web origin
- **Notes:**
  - The Tiptap document is validated against an allow-list of nodes and marks (paragraph, heading
    2–4, lists, code block with language, blockquote, link with http(s) links only, bold, italic,
    code, image by `file_id` only), capped at 200 KB.
  - A server-side renderer produces HTML, then `nh3` sanitizes it as a second layer.
  - Code blocks are highlighted by Pygments (class-based CSS), so no highlighting JavaScript runs on
    phones.
  - Rendering happens at publish time into the snapshot. Draft previews render on demand.

### Progress

- **Heartbeat:** `POST /api/v1/progress/heartbeat` with `{enrollment_id, lesson_id, position_seconds, played_seconds}`.
  - Sent every 15 s, and also on pause, when the tab is hidden, and on unload (`fetch` with
    `keepalive`).
  - `played_seconds` is capped at 15 s × 1.5 × playback rate.
- **Redis buffer**, per lesson:
  - the last position
  - a **bitmap of watched 5-second segments** (`SETBIT`), so seeking ahead isn't counted as watching
  - `SADD` of the key to a `progress:dirty` set
- **Flush:** a Celery beat task every 30 s:
  - `SPOP`s up to 500 dirty keys at a time
  - bulk-upserts `lesson_progress` (`position_seconds`, `watched_bitmap bytea`, `watched_ratio`) with
    `ON CONFLICT DO UPDATE`
  - is safe to run twice
  - emits one `video_progress` event per lesson per flush
- **Resume** reads Redis first, then falls back to Postgres. `enrollments.last_lesson_id` and
  `last_accessed_at` drive "Continue learning".
- **Completion rules:**
  - video: `watched_ratio` ≥ the lesson's `completion_threshold` (default 0.9)
  - notes: explicit "Mark complete"
  - pdf: the file has been opened (a signed URL was issued) **and** "Mark complete"
  - quiz, lab, assignment: excluded until their phases land
- **Course progress:**
  - = completed required lessons ÷ required lessons **in the enrollment's pinned version**,
    rounded down to a whole percent
  - 100% is reserved for full completion
  - recomputed in the same transaction as each lesson completion, and stored on the enrollment
  - `completed_at` is set at 100%
  - each completion emits a `lesson_completed` event

### Events, outbox relay, caching, catalog

- **Outbox relay (`outbox-relay` compose service, aiokafka):**
  - claims up to 500 unpublished rows at a time with `FOR UPDATE SKIP LOCKED`, so several relays can
    run safely
  - publishes keyed by aggregate ID to `learning.enrollments.v1`, `learning.progress.v1` and
    `courses.v1`
  - then stamps `published_at`
  - topics are created automatically in dev
- **Events:**
  - `enrollment_created`
  - `lesson_completed`
  - `video_progress`
  - `course_published` (used for cache and catalog invalidation)
- **`docs/events.md`:**
  - the shared envelope: `id`, `type`, `version`, `occurred_at`, `organization_id`, `aggregate`,
    `data`
  - a JSON Schema for each event
  - topic and key conventions
  - versioning rules
  - "consumers must be idempotent and dedupe on `id`"
- **Outline cache:**
  - each version's outline is immutable, so it's cached in Redis as `course:v:{version_id}:outline`
    with a long TTL
  - the pointer `course:{id}:current` is deleted on publish
  - a student's progress is merged on top with a single query per request
- **Public catalog:**
  - `catalog_entries` is a non-tenant table with public fields only, written on publish for courses
    with `is_public_catalog`
  - `/catalog` and `/catalog/[slug]` are statically generated with time-based revalidation
  - after the publish transaction commits, a Celery task calls a secret-protected Next.js route that
    revalidates those pages
  - **Read the bundled Next 16 caching docs** (`node_modules/next/dist/docs/01-app/…/caching`,
    `revalidateTag`) before implementing.

## Web

- **Instructor course builder** (`/teach/courses`, `/teach/courses/[id]`):
  - **Outline editor:** drag-and-drop with `@dnd-kit`. Saves optimistically; a `409` rolls back and
    refetches.
  - **Lesson editors:**
    - video: upload with progress (`tus-js-client` for Bunny, PUT for local) and processing status
    - notes: Tiptap editor, lazy-loaded
    - PDF: upload
    - quiz, lab, assignment: placeholder cards
  - Skills tag picker.
  - Publish dialog showing what changed since the last version.
  - Assignment panel: organizations for content publishers; own batches for everyone.
  - Heavy libraries load only on `/teach` routes.
- **Student course player** (`/learn/courses/[id]`):
  - outline sidebar, which becomes a drawer on mobile
  - resumes at the last lesson and video position
  - completion ticks per lesson and a course progress bar
  - notes render the pre-sanitized HTML
  - PDFs open through a signed URL
- **Student dashboard** (`/learn`): "Continue learning" (most recently accessed and unfinished
  first), then the other enrolled courses, all with progress.
- **Public catalog** (`/catalog`).
- All forms use React Hook Form with Zod. Types come from `make gen-api`.

## Infra changes

- **New compose services:** `beat` and `outbox-relay` (unless Phase 1 added them).
- **Config changes:**
  - MinIO CORS for the web origin
  - the Postgres `ltree` extension
- **New `.env.example` keys:**
  - `S3_PUBLIC_ENDPOINT_URL`, `S3_BUCKET_*`
  - `VIDEO_PROVIDER=local`
  - `BUNNY_LIBRARY_ID`, `BUNNY_API_KEY`, `BUNNY_CDN_HOSTNAME`, `BUNNY_TOKEN_KEY`,
    `BUNNY_WEBHOOK_SECRET` (left empty)
  - `REVALIDATE_SECRET`
  - `PROGRESS_FLUSH_INTERVAL_SECONDS`
- **New API dependencies:** `aiokafka`, `aiobotocore`, `nh3`, `pygments`, and `respx` (dev only).
- **New web dependencies:** `hls.js`, `tus-js-client`, `@tiptap/react` and extensions, `@dnd-kit/core`
  and `@dnd-kit/sortable`.

## Tests

- **Versioning:**
  - publishing takes a snapshot, and later draft edits don't change an enrolled student's outline or
    content
  - new enrollments get the latest version, and upgrading is explicit
  - version numbers increase
  - publishing is rejected for an empty course or when a video isn't ready
  - progress survives a republish
- **Access control (RLS and service layer):**
  - all five RLS tests listed [above](#visibility-and-rls-decision-3)
  - students can't edit
  - a revoked assignment removes access
  - unauthorized requests return 404, not 403
  - webhook secret check
  - presigned uploads reject the wrong content type or size, and a non-PDF payload fails the confirm
    step
- **Progress:**
  - each lesson type's completion rule
  - placeholders are excluded
  - percentage and rounding (100% only when complete)
  - seeking ahead doesn't count as watching
  - the heartbeat → Redis → flush path works and running the flush twice doesn't double-count
  - resume position
  - `completed_at` is set at 100%
- **Events:**
  - outbox rows are written in the same transaction (and rolled back with it)
  - the relay publishes to real Redpanda
  - the event payloads validate against the `docs/events.md` schemas
- **Caching:**
  - the outline cache is hit, and the pointer is invalidated on publish
  - catalog revalidation is triggered after commit
- **Notes:**
  - XSS payloads (script tags, `javascript:` links, event handlers, `<iframe>`) are stripped
  - code blocks are highlighted
- **Bunny (`respx`):**
  - upload signature and headers
  - webhook → status re-fetch
  - playback token format and expiry
- **Web unit tests (Vitest):**
  - the reorder logic
  - the heartbeat hook (fake timers, visibility changes, keepalive)
  - the progress and outline components
- **Playwright (the done-when flow):**
  1. The instructor logs in through the real Keycloak login page.
  2. They build a course with video (MP4 fixture, local provider), notes and PDF lessons.
  3. They publish it and assign it to CSE 2026.
  4. The CSE 2026 student sees it under "Continue learning", watches the video, marks the notes and
     PDF complete, and ends at 100%.
  5. An ECE 2026 student and an Other College student don't see the course.

## Build order

Run `make lint` and `make test` after each step.

1. Check the Phase 1 prerequisites. Build the relay and beat if Phase 1 didn't.
2. `skills`, and the `courses` draft tree plus builder API (with RLS).
3. Versioning, `course_assignments`, and enrollments with fan-out.
4. `media`: video providers, PDF files, notes rendering, and `scripts/smoke_test_bunny.py`.
5. Progress (heartbeat, flush, completion rules) and events plus `docs/events.md`.
6. Web instructor builder.
7. Web student player and dashboard.
8. Public catalog, caching, and the end-to-end Playwright test.

## Open points to confirm when resuming

- Students of the owner org see a course only once it's assigned to them (see
  [Visibility](#visibility-and-rls-decision-3)).
- Whether an assigned org's `org_admin` may narrow an org-wide assignment down to their own batches.
- Whether new enrollments default to the latest version, with "upgrade" only for existing
  enrollments (planned: yes).
