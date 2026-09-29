# Phase 2 — Courses and learning content (plan)

**Status (2026-09-30):** steps 1–5 are complete and committed. Step 3 (video) also made
`If-Match` required on outline and lesson edits. Step 6 (instructor UI) is next. Steps 6–8 are
not started. See
[Implementation status](#implementation-status). Phase 1 (identity, organizations, roles, RLS) is
complete; see [What Phase 1 provides](#what-phase-1-provides). Resume by re-reading `CLAUDE.md`
and `docs/access-control.md`, then present this plan for approval before coding.

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

## Implementation status

Checked against `git log` and the module code on 2026-09-29.

### Step 1 — data model, migrations and RLS (`41dd7d5`)

- **Migrations:** `0004_learning_content` (enables `ltree`; tables, the `lesson_type` enum,
  RLS helpers `app.course_readable` and `app.is_org_grant`, per-operation policies) and
  `0005_seed_skills` (starter taxonomy).
- **Tables:** `skills`, `lesson_skills`, `courses`, `course_modules`, `lessons`,
  `course_versions`, `course_version_lessons`, `course_assignments`, `catalog_entries`,
  `enrollments`, `lesson_progress`, `video_assets`, `files`.
- **RLS tests:** `courses/tests/test_course_rls.py`, `enrollments/tests/test_enrollment_rls.py`,
  `skills/tests/test_skills_rls.py`.

### Step 2 — services and APIs (`878985b`)

All under `/api/v1`, each with a row in `MATRIX` (`tests/test_endpoint_roles.py`).

| Area | Endpoints |
|---|---|
| Skills | `GET /skills`, `POST /skills`, `PATCH /skills/{skill_id}` |
| Courses | `POST /courses`, `GET /courses`, `GET /courses/{course_id}`, `PATCH /courses/{course_id}`, `DELETE /courses/{course_id}` (archives) |
| Draft builder | `GET /courses/{course_id}/draft`; `POST /courses/{course_id}/modules`; `PUT /courses/{course_id}/modules/order`; `PATCH`, `DELETE /courses/{course_id}/modules/{module_id}`; `POST /courses/{course_id}/modules/{module_id}/lessons`; `PUT /courses/{course_id}/modules/{module_id}/lessons/order`; `GET`, `PATCH`, `DELETE /courses/{course_id}/lessons/{lesson_id}`; `PUT /courses/{course_id}/lessons/{lesson_id}/skills` |
| Publishing | `GET /courses/{course_id}/publish-preview`, `POST /courses/{course_id}/versions`, `GET /courses/{course_id}/versions`, `GET /courses/{course_id}/versions/{version_id}` |
| Assignments | `GET /courses/{course_id}/assignments`, `POST /courses/{course_id}/assignments`, `DELETE /course-assignments/{assignment_id}` |
| Enrollments | `GET /enrollments`, `GET /enrollments/{enrollment_id}`, `POST /enrollments/{enrollment_id}/lessons/{lesson_id}/visit`, `POST /enrollments/{enrollment_id}/lessons/{lesson_id}/complete`, `POST /courses/{course_id}/enrollment-upgrades` |

Also committed: the enrollment Celery jobs, the Kafka batch-member consumer
(`app/cli/enrollment_consumer.py`, a compose service), outbox events `course_published`,
`enrollment_created`, `lesson_completed` and `enrollment_version_changed`, and the tests
`test_builder_api`, `test_versioning`, `test_assignments_api`, `test_progress` and
`test_versions_and_upgrades`.

### Step 3 — video

Contents:

- **Backend:** `media/providers.py` (the `VideoProvider` protocol, `LocalVideoProvider`,
  `BunnyStreamProvider`), `mp4.py`, `tasks.py`, `router.py` and `repository.py`; the
  `enrollments/video_buffer.py` and `video_flush.py` modules; migration `0006_video_playback`;
  and tests `test_providers.py` and `test_video_progress.py`.
- **Endpoints:** `POST`, `GET /videos`, `GET /videos/{video_id}`,
  `POST /videos/{video_id}/uploaded`, `GET /videos/{video_id}/playback`,
  `POST /progress/heartbeat`, `GET /enrollments/{enrollment_id}/lessons/{lesson_id}/playback`,
  `GET /enrollments/{enrollment_id}/lessons/{lesson_id}/resume`, and
  `POST /webhooks/video/bunny/{secret}` (hidden from the schema).
- **Web:** `/teach/videos`, `/learn/enrollments/[enrollmentId]/video/[lessonId]`,
  `features/video/*`, and `e2e/video.spec.ts`.

See the step 3 notes under [Build order](#build-order).

### Step 4 — notes and PDFs

- **Backend:** `courses/notes.py` (the allow-list validator and the renderer plus nh3), the
  `media` file service, migration `0007_lesson_files`, and new settings `PDF_UPLOAD_MAX_BYTES`,
  `IMAGE_UPLOAD_MAX_BYTES`, `FILE_UPLOAD_TTL_SECONDS` and `FILE_DOWNLOAD_TTL_SECONDS`.
- **Endpoints:**
  - `POST`, `GET /files`
  - `GET /files/{file_id}`, `POST /files/{file_id}/confirm`, `GET /files/{file_id}/download`
  - `GET /courses/{course_id}/lessons/{lesson_id}/preview`
  - `POST /enrollments/{enrollment_id}/lessons/{lesson_id}/pdf-access`
  - `GET /enrollments/{enrollment_id}/lessons/{lesson_id}/images`
- **Tests:** `test_notes` (allow-list, XSS, highlighting), `test_notes_api`, `test_files` (real
  MinIO: POST policy, size, content type, magic bytes, signed downloads) and `test_pdf_access`
  (opened-before-complete, RLS, minor replacement).
- No web UI in this step. The editors come in step 6 and the player in step 7.

### Step 5 — caching, catalog and events

- **Backend:** `courses/cache.py`, `enrollments/heartbeat_cache.py`, the `courses/tasks.py`
  revalidation task, and `RequestContext.redis`.
- **Endpoints:** `GET /catalog`, `GET /catalog/{slug}` (public).
- **Web:** `/catalog`, `/catalog/[slug]`, `POST /api/revalidate`.
- **Tests:** `test_caching`, `test_catalog` (respx for the revalidation call), `test_event_schemas`,
  the `revalidate` Vitest suite, and `e2e/catalog.spec.ts`.

### Deviations between this plan and the committed code

1. **Skills are global.** The code has no `skills.organization_id`, following `CLAUDE.md`
   ("global in this phase"; writes only by `platform_admin` and publisher staff). The
   [Skills](#skills) section above still describes org-scoped skills and is out of date.
2. **More publish blockers.** Besides `empty_course` and `video_not_ready`, publishing also
   rejects `pdf_not_ready` and `course_archived`.
3. **Catalog and `course_published` arrived early.** Step 2 already writes and deletes
   `catalog_entries` (on publish and archive) and emits `course_published`. Step 5 still owns
   the Redis outline cache, the Next.js static catalog and its revalidation, and the topic
   routing.
4. **Topics not registered yet.** At HEAD, `TOPICS` maps only `batch_member`, so Phase 2 events
   go to the default `platform.events.v1`. The working tree adds `video_progress` →
   `learning.progress.v1`. `learning.enrollments.v1` and `courses.v1` remain for step 5, as do
   the Phase 2 event schemas in `docs/events.md`.
5. **The consumer reconciles instead of deduping (accepted 2026-09-29).** The batch-member
   consumer keeps no event-`id` dedupe record. Each event runs `reconcile_student`, which
   recomputes that student's enrollments from the current batch memberships and batch
   assignments. Reasons:
   - It is idempotent by construction: a replayed event recomputes the same state.
   - It is safe under reordering. Kafka orders events only per `batch_id` key, so an
     `added`/`removed` pair for one student across two batches can arrive in either order.
     Applying event deltas could then leave the wrong result, but reading current state cannot.
   - It self-heals. A missed or failed event is repaired by the next event or course
     reconciliation for that student, and there is no dedupe table to grow or expire.
   - The cost is one read of the student's memberships and assignments per event. That is small,
     and batch-member events are low-volume.
6. **`If-Match` was optional; it is now required (decided 2026-09-29).** At `878985b`, an omitted
   header skipped the revision check. It is now required on every endpoint that changes the
   course outline or lessons: modules, lessons, both reorders and skill tags. A missing header
   returns `428 precondition_required`, and a stale one returns `409`. This landed with step 3.
7. **`DELETE /courses/{id}` archives** the course and removes its catalog entry. It does not
   hard-delete.
8. **Endpoints not named in the plan:** `GET /courses/{id}/draft`,
   `GET /courses/{id}/publish-preview` (backs the publish dialog's structural diff),
   `GET /courses/{id}/lessons/{lesson_id}` and `POST /enrollments/{id}/lessons/{lesson_id}/visit`
   (feeds `last_lesson_id` for "Continue learning").
9. **All open points are settled.** All four
   [open points](#open-points-to-confirm-when-resuming) are now recorded in `CLAUDE.md`. The
   video-replaced-in-a-minor-release rule was added on 2026-09-29.

All other deviations were accepted as documented on 2026-09-29. Deviation 1 is resolved by
rewriting the [Skills](#skills) section.
10. **Notes are not validated yet.** `NotesContent.doc` is still an unvalidated `dict`. The
    allow-list validation, rendering and 200 KB cap are step 4, as planned.

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
  It must be idempotent. It gets there by **reconciling from current state**, not by deduping on
  the event `id`: see deviation 5 under
  [Implementation status](#deviations-between-this-plan-and-the-committed-code).

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
  - Clients must send the revision they started from (`If-Match`) on every endpoint that changes
    the course outline or lessons.
    - A missing header gets `428 precondition_required`.
    - A stale revision gets `409 conflict`.
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

- The taxonomy is **global** in this phase (see `CLAUDE.md`, "Content ownership & sharing"):
  `skills (id, parent_id, name, slug, path ltree, description)` has **no `organization_id`**.
  `path` is unique and has a GiST index. Subtree queries use `path <@ 'dsa.arrays'`.
- **Who can do what:**
  - Everyone can read every skill.
  - Only `platform_admin` and staff (`org_admin`, `instructor`, `lab_author`) of a
    content-publisher org can create or edit skills.
  - This is enforced by per-operation RLS policies and by the service layer.
- `lesson_skills (lesson_id, skill_id)`. Questions and problems get tagged the same way in later
  phases.
- The migration enables the `ltree` extension, and `0005_seed_skills` seeds a starter tree.

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
- <a id="heartbeat-validation-cache-step-5"></a>**Heartbeat validation cache (step 5):**
  - Today each heartbeat makes about 4 Postgres queries: the enrollment, the pinned version, the
    lesson in that version, and the baseline progress row. At 100k concurrent students that is
    roughly 6.7k heartbeats per second.
  - Cache the validation result in Redis with a short TTL (about 60 s), keyed by
    `(enrollment_id, lesson_id)`. The cached value is the enrollment's org, user, status and
    major, plus the lesson's `video_asset_id` and duration. Read Postgres only on a miss.
  - Skip the baseline read when the Redis buffer entry already exists.
  - **Revocation is still immediate for playback.** `playback` and `resume` keep reading through
    RLS. A heartbeat accepted from cache within the TTL after revocation only buffers watch data
    for an enrollment the student already had. The flush re-checks the enrollment status before
    writing.
  - Invalidation:
    - delete the key on enrollment revocation and on upgrade
    - a publish changes the version pointer, so version-dependent fields also expire by TTL
  - Tests:
    - a cache hit makes no Postgres query
    - a revoked enrollment stops being credited at flush
    - a replaced asset gets `409 video_changed` within one TTL (until then, the buffer key
      includes the old asset id, so its data never merges into the new asset's progress)
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

These are the Phase 2 prompt's 8 steps. After each step:

1. Run lint, type-check and all tests (the `CLAUDE.md` workflow), and fix failures.
2. Add every new endpoint to `MATRIX` and run `make gen-api`.
3. Commit, **push**, summarize, and wait for "continue".

Step 3 implementation notes:
- Local uploads use signed MinIO PUTs; content type is signed, and size/MP4 duration are checked
  before marking an asset ready. Bunny uses signed TUS uploads and mocked HTTP tests.
- Playback URLs expire after five minutes by default; the player renews them before expiry.
- `/teach/videos` exposes the reusable uploader. The focused video route is
  `/learn/enrollments/[enrollmentId]/video/[lessonId]`; the full course UIs remain steps 6–7.
- Heartbeats include `video_asset_id` and `playback_rate`. A stale asset gets `409 video_changed`.
- **Watch credit is bounded by real time.** One heartbeat earns at most
  `HEARTBEAT_INTERVAL_SECONDS × 1.5 × playback_rate` (22.5 s at 1×). It also earns at most 1.5×
  the server wall-clock time since that entry's previous heartbeat (× rate), so replaying
  heartbeats quickly earns nothing extra. The interval setting must match the web player
  (`heartbeat.ts`).
- **Rejected uploads are deleted.** A local upload that fails processing (oversize or not an MP4)
  is marked `failed`, and its object is deleted after commit.
- **Webhook URLs aren't traced.** `api/v1/webhooks/` is excluded from OpenTelemetry, so the
  secret in the path never reaches spans. The request log already redacts it.
- **The flush drain continues** while batches come back full (counting sampled entries, not
  changed rows).
- Redis stores 5-second watched-segment bitmaps and partial intervals; seeking grants no watch time.
  Flushes sample up to 500 dirty entries without removing them, acknowledge only after commit,
  and compare revisions so crashes and concurrent writes are safe. This replaces destructive
  `SPOP` claiming. A Postgres advisory lock serializes flushes, and database work is batched per org.
- Clean buffer entries expire after seven days; dirty entries have no expiry. Redis must use
  persistent storage (AOF) and `noeviction` with a `maxmemory` cap, so acknowledged heartbeats
  survive a Redis outage and a full Redis rejects writes instead of evicting unflushed progress.
  Compose sets this locally (256 MB). Production Redis needs the same.
- The manual real-credential Bunny smoke script remains in step 8, as listed below.
- **Deferred:**
  - The Bunny playback-token digest is checked only for structure and expiry. Real verification
    is the step 8 smoke script.
  - ~~Each heartbeat re-validates against Postgres~~: **scheduled into step 5**. See
    [Heartbeat validation cache](#heartbeat-validation-cache-step-5).
  - **The enrollment and resume GETs write.** They reset progress when a video was replaced in a
    minor release (`reset_replaced_videos`). Constraints on them:
    - **Idempotent.** The UPDATE only touches rows whose `video_asset_id IS DISTINCT FROM` the
      current one, so a repeat or concurrent call changes nothing. Keep it that way: no counters
      or events on this path.
    - **Never assume the primary.** `docs/architecture.md` plans Aurora read replicas. When
      read routing is added, these endpoints must either run their write on the writer
      explicitly or move it off the read path (for example into the heartbeat or the flush).
      They must not rely on the read session happening to be the primary. A replica may lag, so
      the response must be correct even if it reads a pre-reset row.
  - The player shows a generic error on `409 video_changed` instead of reloading the lesson. It is
    reworked in the step 7 course player.
  - Event-payload validation against `docs/events.md` is a step 5 test.

Step 4 implementation notes:
- **Notes allow-list** (`courses/notes.py`): the Tiptap node set is the plan's list plus
  `hardBreak` (Shift+Enter; renders `<br>`). Everything else is rejected with a JSON path:
  - unknown nodes, marks, keys or attributes
  - non-absolute or non-http(s) links
  - headings other than 2–4
  - code languages outside a fixed list of 16
  - nesting deeper than 16, more than 50 images, and documents over 200 KB
- **Rendering:** HTML is built from the validated tree with every text and attribute escaped,
  then sanitized by nh3. The sanitizer allows only the tags and attributes the renderer emits,
  and only Pygments token classes for `class`.
  - Links get `target="_blank" rel="noopener noreferrer nofollow"`.
  - Code is highlighted by Pygments with class-based output. The stylesheet comes from
    `notes.stylesheet()` and ships with the step 7 player.
- **Versions store only the HTML.** Readers get `{"html", "image_file_ids"}` for notes, never
  the editor JSON. Editors preview drafts through `GET /courses/{id}/lessons/{lesson_id}/preview`.
- **Notes images** are `image` files, 5 MB. There is an allow-list of PNG, JPEG, WebP and GIF,
  each checked by signature. Everything else is refused (SVG, BMP, TIFF, HEIC...). In HTML they are `<img data-file-id>` without `src`. Readers fetch short-lived signed
  URLs from `GET /enrollments/{id}/lessons/{lesson_id}/images`, because a signed URL can't live
  in an immutable snapshot. Images must be this org's confirmed image files.
- **Files:** `POST /files` issues a presigned POST pinned to one key (never the user's file
  name), the exact Content-Type and the size limit (PDF 25 MB).
  - `POST /files/{id}/confirm` checks the stored size and the leading bytes (`%PDF-`, or the
    PNG, JPEG or WebP signature).
  - A mismatch is `422 file_rejected` with the reason in `details`. The rejected status is
    committed first, in a separate transaction with the caller's own RLS context
    (`independent_transaction`), then the object is deleted and the error raised, so the file
    can never be attached. A missing object is `409` and stays pending.
  - Only confirmed PDFs can be attached to lessons (`422 file_not_ready`).
  - File names are validated because they go into `Content-Disposition` (no quotes, control
    characters or path separators), and the header uses an ASCII fallback.
- **Downloads** are signed GETs valid for `FILE_DOWNLOAD_TTL_SECONDS` (300 s).
  `POST /enrollments/{id}/lessons/{lesson_id}/pdf-access` is a POST because it records
  `pdf_opened_at` (first opening kept), which completing a pdf lesson requires.
- **File RLS** (migration 0007): `course_version_lessons.file_ids` (GIN-indexed) lists each
  published lesson's files, backfilled for existing pdf lessons. `app.file_readable` mirrors
  `app.video_readable`. A PDF replaced in a minor release: students can read only the latest
  minor's file.
- **Backfill in 0007:** for every existing `course_version_lessons` row of type `pdf`, `file_ids`
  becomes `[content.file_id]` of the lesson with the same id in that row's own version snapshot
  (any module). Other rows keep `{}`: pdf lessons without a file, and notes and video lessons.
  Notes images only exist from 0007 on, so there is nothing older to backfill. Tested in
  `tests/test_migrations.py` by seeding rows at 0006 and upgrading.
- **Step 4 follow-up (2026-09-30):**
  - **Read access means the content** (migration 0008). Staff (`org_admin`, `instructor`) of an
    org that reads a course may play its videos and open its files in any published version,
    through `GET /courses/{id}/versions/{version_id}/lessons/{lesson_id}/playback`, `/pdf` and
    `/images`. RLS: `app.video_readable` and `app.file_readable` gain a staff branch using
    `app.course_readable`. Editing stays owner-org only.
  - `GET /batches` gains an exact, case-insensitive `name` filter, backed by
    `uq_batches_org_name`. The e2e tests find seeded batches with it instead of depending on the
    first page.

Step 5 implementation notes:
- **Version cache** (`courses/cache.py`):
  - `course:v:{version_id}` holds a version's snapshot plus its lesson rows (immutable; 7 days).
  - `course:{id}:major:{n}:latest` is the pointer (5 minutes). It is deleted after the publish
    commits. Its TTL bounds the race with a reader that resolved the old version just before
    the commit.
  - This replaces the plan's `course:v:{id}:outline` / `course:{id}:current`: enrollments pin a
    major, so the pointer is per major.
  - **Authorization isn't cached.** Each hit runs one query that evaluates the `course_versions`
    SELECT policy (editor, platform admin, or `app.course_readable`) for the courses involved.
    Removing an assignment still hides the course immediately.
  - Request-path student reads (enrollment detail, player, playback, resume, visit, complete)
    use the cache through `RequestContext.redis`. Background jobs keep reading Postgres.
- **Heartbeat validation cache** (`enrollments/heartbeat_cache.py`, TTL 60 s):
  - A steady heartbeat now makes no Postgres reads. `test_caching` counts the statements.
  - A hit is used only by the same user and org, and only while the version pointer still names
    the version it was validated against. So a new release, such as a replaced video, gives
    `409 video_changed` on the very next heartbeat.
  - The progress baseline is read only when the Redis entry doesn't exist yet. The Lua write
    returns "needs baseline" instead of creating the entry empty, so an expiry between the check
    and the write can't wipe stored progress.
  - What a stale entry could still do within the TTL is bounded by the flush, which skips
    non-active enrollments and assets that no longer match.
  - Known limit: after an assignment is removed, and before reconciliation marks the enrollment
    revoked, up to 60 s of watching can still be credited. Playback itself is refused at once.
- **Public catalog:**
  - `GET /catalog` and `GET /catalog/{slug}` are public (no sign-in; RLS `USING (true)`) and
    return public fields only. The role matrix gained a `public` flag.
  - Web: `/catalog` and `/catalog/[slug]` use the pre-Cache-Components model (the project
    doesn't enable `cacheComponents`), following the bundled docs:
    - fetches tagged `catalog`, and `revalidate = 300`
    - `generateStaticParams` for the first page
    - `next build` prerenders empty when the API is unreachable (Docker build); at runtime
      errors are thrown, so Next keeps serving the last good page
  - Revalidation: after a publish or archive commits, the Celery task
    `courses.revalidate_catalog` POSTs `{"tags": ["catalog"]}` to `WEB_INTERNAL_URL/api/revalidate`
    with the `x-revalidate-secret` header, retried with backoff on failure.
    - The route compares the secret in constant time, accepts only allow-listed tags, and calls
      `revalidateTag(tag, { expire: 0 })`, which the docs give for calls from another service.
    - New env: `REVALIDATE_SECRET` (API, worker, web) and `WEB_INTERNAL_URL` (API/worker).
  - Compose runs the web app with `next dev`, which never caches, so Playwright checks the
    content and the secret. ISR itself was verified with a production `next build` while the
    API was unreachable.
- **Events:**
  - Topics follow the aggregate type: `courses.v1` (`course_published`),
    `learning.enrollments.v1` (`enrollment_created`, `lesson_completed`,
    `enrollment_version_changed`) and `learning.progress.v1` (`video_progress`).
  - **Deviation:** the plan put `lesson_completed` on `learning.progress.v1`. Its aggregate is
    the enrollment, so it shares that enrollment's topic and ordering.
  - `docs/events.md` now has conventions, versioning rules and a JSON Schema for every event.
    `tests/test_event_schemas.py` produces every documented event through real flows and
    validates the payloads and topics against the document.

1. Data model, migrations and RLS (skills, courses and draft tree, versions, assignments,
   enrollments, progress, media tables).
2. Services and APIs (builder, publish minor/major, assignments and narrowing, enrollments and the
   batch-event consumer, upgrades, progress rules).
3. Video (`VideoProvider`, local and Bunny, heartbeat → Redis → Celery flush, resume).
4. Notes and PDFs.
5. Caching and events (including the heartbeat validation cache).
6. Instructor UI.
7. Student UI.
8. End-to-end tests and `scripts/smoke_test_bunny.py`.

## Open points to confirm when resuming

All confirmed, and recorded in `CLAUDE.md` under "Content ownership & sharing" (the last one on
2026-09-29):

- **Publisher-made batch assignments:** an org_admin **cannot remove** batch assignments the
  publisher created directly for their org's batches; they can remove only rows their own org
  created.
- **Assigned-org instructors** can read the published course but can't distribute it to batches.
- **Major-version opt-in** applies to a whole org, or to a chosen subset of its batches; never to
  individual students.
- **A video replaced in a minor release:** completions are kept, and partially watched progress for
  that lesson restarts.
