# Phase 2 — Courses and learning content (plan)

**Status:** ready to start. Phase 1 (identity, organizations, roles, RLS) is complete; see
[What Phase 1 provides](#what-phase-1-provides). Resume by re-reading `CLAUDE.md` and
`docs/access-control.md`, then present this plan for approval before coding.

First written 2026-09-26 against the foundation commit. Updated at the end of Phase 1 to use
Phase 1's real names and to follow the 8 build steps from the Phase 2 prompt.

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

## What Phase 1 provides

All of this is built and tested. Use it; don't rebuild it.
Full reference: [`docs/access-control.md`](../access-control.md).

### Data (identity module: `apps/api/app/modules/identity`)

- `organizations` with `is_content_publisher` (SkillifyMe is seeded as a publisher) and `status`
  (active or archived; archived orgs grant nothing).
- `users`: global, keyed by the Keycloak `sub`.
- `memberships` `(user_id, organization_id, role)`.
  - Roles: `org_admin`, `instructor`, `lab_author`, `student`. They are **per organization**, and a
    user may hold several in one org.
  - The platform role is **`platform_admin`** (a Keycloak realm role in the token), not
    `super_admin`.
- `batches` with **`UNIQUE (id, organization_id)`**. `course_assignments` can therefore use the
  composite foreign key `(batch_id, organization_id) → batches(id, organization_id)`, the same way
  `batch_members` and `import_jobs` already do.
- `batch_members` `(batch_id, organization_id, user_id)`.
- Every table has RLS with per-operation policies. Schema guards (`tests/test_schema_guards.py`)
  fail if a new table lacks RLS, uses a `FOR ALL` policy, or leaves a foreign key unindexed.

### SQL helpers for Phase 2's RLS policies

All are `SECURITY DEFINER` with a fixed `search_path`, and executable by the app role only:

| Function | Use in Phase 2 |
|---|---|
| `app.current_org_id()`, `app.current_user_id()` | Tenant and user context |
| `app.current_user_is_platform_admin()` | Platform override |
| `app.current_user_has_role(org, roles[])` | Owner-org editors and draft preview (`{instructor,org_admin}`); an assigned org's `org_admin` narrowing to batches or opting in to a major version |
| `app.current_user_is_member(org)` | Any role in an org |
| `app.current_user_in_batch(batch)` | Batch-level student visibility |
| `app.org_is_content_publisher(org)` | Only publishers may assign to *other* orgs |

Policy expression constants (`PLATFORM_ADMIN`, `ORG_ADMIN_HERE`, `STAFF_HERE`...) and the
`enable_rls()` / `policy()` migration helpers are in `apps/api/app/db/rls.py`.

### Request context and authorization

- Route dependencies: `CurrentPrincipal`, `TenantSession` (RLS context already set) and
  `AuditActorDep`, in `identity/dependencies.py`.
- `Principal` carries `user_id`, `organization_id` (the active org), `roles`, `memberships` and
  `is_platform_admin`.
- Service-layer checks: `require_permission`, `require_org_permission`, `require_role`. Add new
  course permissions (e.g. `course.edit`, `course.assign`) to `ROLE_PERMISSIONS` and to
  `docs/access-control.md`.
- Every new endpoint needs a row in `MATRIX` in `tests/test_endpoint_roles.py`; the meta-test
  enforces it.
- Admin actions: `app.modules.audit.service.record()`.
- **Web:** the Next.js BFF handles login and cookies, `useMe()` (`features/auth/queries.ts`) gives
  the current user and permissions, and `proxy.ts` already protects `/learn` and `/teach`. Browser
  calls go through `/backend/*` with `X-Organization-Id` set from the org switcher.

### Service interface (for enrollments)

In `app.modules.identity.service`:

- `iter_batch_student_ids(session, batch_id, page_size=500)`: keyset pages of **student** user ids
  in a batch, for enrollment fan-out.
- `batch_belongs_to_org(session, batch_id, org_id)` and `list_org_batches(session, org_id, params)`.
- **Batch join/leave events** (the membership-change signal):
  - `batch_member_added` / `batch_member_removed` on Kafka topic `identity.batch-members.v1`,
    keyed by `batch_id`
  - the schemas are in `docs/events.md`
  - reasons include `added`, `invitation`, `import`, `removed`, `left_organization` and
    `invitation_revoked`

  Phase 2 adds a consumer (a Kafka consumer group in a new worker service) that enrolls or revokes.
  It must be idempotent: dedupe on the event `id`, and treat an `added` for an existing enrollment
  as a no-op.

### Platform pieces already running

- Outbox → Kafka relay (`outbox-relay` service, role `skillify_relay`). Add Phase 2 topics to
  `TOPICS` in `app/events/envelope.py`.
- Celery `beat` (schedule in `app/worker.py`), a `worker`, and after-commit hooks
  (`run_after_commit` / `run_after_commit_async` in `app/db/session.py`).
- S3: `app.core.storage.ObjectStorage` (boto3; MinIO locally). Extend it with presigned URLs rather
  than adding aiobotocore.
- Redis sliding-window `RateLimiter` (`app/core/ratelimit.py`).

### Seed data (`make seed`) and test support

- **Orgs:** SkillifyMe (publisher), Demo College (batches **CSE 2026** and **ECE 2026**), Other
  College (MECH 2026).
- **Users** (password `Local-Dev-Only-1`):
  - SkillifyMe: `platform.admin@`, `content.admin@` (org_admin), `author@` (instructor),
    `lab.author@`, all `@skillifyme.local`
  - Demo College: `admin@`, `instructor@`, `cse.student@`, `ece.student@`, all
    `@demo-college.local`
  - Other College: `admin@`, `instructor@`, `student@`, all `@other-college.local`
  - `multi@skillifyme.local`: instructor in SkillifyMe and Demo College
- **pytest:**
  - `factory` (orgs, users, memberships, batches, invitations, import jobs)
  - `tenant_session(org=..., user=..., platform_admin=...)` for RLS tests
  - `auth_headers(user, org=..., platform_admin=...)` with locally signed JWTs
  - `org_setup` (one user per role plus a batch)
- **Playwright:** `e2e/helpers.ts` `signIn(page, email, path)` goes through the real Keycloak login
  page.

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

- **Outbox relay:** already running (Phase 1). Add `learning.enrollments.v1`,
  `learning.progress.v1` and `courses.v1` to `TOPICS` in `app/events/envelope.py`.
- **Events:**
  - `enrollment_created`
  - `lesson_completed`
  - `video_progress`
  - `course_published` (used for cache and catalog invalidation)
  - `enrollment_version_changed` (an org_admin opted enrollments into a new major version)
- **`docs/events.md`** (the envelope and relay are already documented; add):
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

- **Compose:** `beat` and `outbox-relay` already exist. Add a Kafka consumer service for the batch
  membership events.
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
- **New API dependencies:** `nh3` and `pygments`. `aiokafka`, `boto3`, `httpx` and `respx` are
  already installed.
- **New web dependencies:** `hls.js`, `tus-js-client`, `@tiptap/react` and extensions, `@dnd-kit/core`
  and `@dnd-kit/sortable`.

## Tests

- **Versioning (decision 6):**
  - publishing takes a snapshot, and later draft edits don't change an enrolled student's outline or
    content
  - the first publish is 1.0; major bumps to `(n+1).0`; minor bumps to `n.(m+1)`
  - a minor is rejected (`409 minor_not_allowed`) when modules or lessons were added, removed,
    reordered or moved, or when a lesson's type, `is_required` or `completion_threshold` changed
  - a **minor** release reaches existing enrollments automatically, across orgs, and their progress
    percentages are unchanged
  - a **major** release reaches only new enrollments
  - an org_admin's opt-in upgrade moves only their own org's enrollments, carries over completions
    for surviving lesson ids, recomputes percentages, and emits `enrollment_version_changed`;
    nobody else can perform it
  - a lesson moved between modules keeps its id and its progress
  - publishing is rejected for an empty course or when a video isn't ready
- **Access control (RLS and service layer):**
  - every RLS test listed [above](#visibility-and-rls-decisions-35), plus every new endpoint in the
    role matrix
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
- **Playwright (the done-when flow, from the Phase 2 prompt):**
  1. The publisher (`author@skillifyme.local`) builds a course with video (MP4 fixture, local
     provider), notes and PDF lessons, and publishes it.
  2. They assign it to Demo College.
  3. The Demo College admin assigns it to CSE 2026.
  4. `cse.student@` completes it with correct progress.
  5. `ece.student@` cannot see it.
  6. The student UI is also checked on a 360px viewport.

## Build order

These are the Phase 2 prompt's 8 steps. After each: lint, type-check, all tests, fix, commit and
push, summarize, and wait for "continue".

1. Data model, migrations and RLS (skills, courses and draft tree, versions, assignments,
   enrollments, progress, media tables).
2. Services and APIs (builder, publish minor/major, assignments and narrowing, enrollments and the
   batch-event consumer, upgrades, progress rules).
3. Video (`VideoProvider`, local and Bunny, heartbeat → Redis → Celery flush, resume).
4. Notes and PDFs.
5. Caching and events.
6. Instructor UI.
7. Student UI.
8. End-to-end tests and `scripts/smoke_test_bunny.py`.

## Open points to confirm when resuming

Decisions 4–6 settled the original questions. These smaller assumptions remain; confirm them before
implementing:

- **Publisher-made batch assignments:** an org_admin **cannot remove** batch assignments the
  publisher created directly for their org's batches; they can remove only rows their own org
  created.
- **Assigned-org instructors** can read the published course but can't distribute it to batches.
- **Major-version opt-in** applies to a whole org, or to a chosen subset of its batches; never to
  individual students.
- **A video replaced in a minor release:** completions are kept, and partially watched progress for
  that lesson restarts.
