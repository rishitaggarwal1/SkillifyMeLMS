# Phase 2.5 — Demo-ready portal (plan)

**Status (2026-10-03): approved; steps 1–5 (platform admin, role home, assignments, progress reports and college-admin screens) committed, see
[Implementation status](#3a-implementation-status).** Phase 2 is
complete and tagged `v0.2.0` ([phase-2.md](phase-2.md)).

**Goal:** a demo-ready portal where four roles work end to end on `make dev`, with demo logins,
and a stack that a later phase can deploy by changing only `.env` values.

**Not in this phase:**
- No deployment and no provisioning of any kind.
- Not Phase 3 (assessments): rubrics, late policy, attempt history, plagiarism and AI feedback
  are hooks only.
- Not Phase 5 (analytics): the progress view is an OLTP stopgap.

If something seems to need more scope, stop and ask.

**Done when:**
- All four roles work end to end on `make dev` with `make seed-demo`.
- `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` validates with a
  server-style `.env`.

## 1. Current state (verified 2026-10-02 at `651fe97`)

### Per role

| Role | Screens today | APIs today | Gaps in this brief that are real |
|---|---|---|---|
| `platform_admin` | None of its own. `/admin` and `/teach` work if an org is picked in the switcher | `POST/GET /organizations`, `GET/PATCH/DELETE /organizations/{id}`; `GET /audit-log` (filters: `action`, `actor_user_id`, `target_type`, `target_id`; all orgs when no org is active) | All of section A: `/platform` UI, cross-org users list, user disable/enable, cross-org courses list, counts. `proxy.ts` doesn't cover `/platform`. `users.status = disabled` is already enforced (`403 account_disabled`), but nothing sets it, and Keycloak is never told |
| `org_admin` | `/admin/batches`, `/admin/batches/[id]`, `/admin/members`, `/admin/imports`; granted courses are distributed from `/teach/courses` ("Assigned courses") and `/teach/courses/[id]` | Batches, members, invitations, imports, audit; `POST /courses/{id}/assignments` (narrowing); `DELETE /course-assignments/{id}` | **B is partly real.** Distribution exists, but only under `/teach`; there is no `/admin/courses`. No per-batch view of enrolled students and progress, and no API lets staff read enrollments (`GET /enrollments` returns only your own) |
| `instructor` | `/teach/courses`, `/teach/courses/[id]`, `/teach/courses/[id]/lessons/[lessonId]`, `/teach/videos` | Full builder, publish, assignments, videos, files | All of C (assignment lessons are placeholders that show "coming soon", `is_required` forced false) and all of D (no progress view or API) |
| `student` | `/learn`, `/learn/enrollments/[id]`, `/learn/enrollments/[id]/lessons/[lessonId]`, resolver routes `/learn/courses/...` | Enrollments, visit, complete, playback, resume, heartbeat, pdf-access, images | Submitting and seeing grades (C) |
| Everyone | `/` is a static landing page with a health widget. The header shows an "Admin" link (with `batch.manage`), the org switcher and Sign out | `GET /me` (memberships, roles, permissions, `is_platform_admin`) | All of E: no role-aware redirect, no chooser, and no role shown in the header |

**Other findings:**
- **`users.last_login_at` is never written.** "Active today" can't use it as is (see decision 9).
- **Platform admins already have RLS branches** (`PLATFORM_ADMIN`) on organizations, users,
  memberships, batches, courses, enrollments, lesson_progress and audit_log. Section A therefore
  needs **no policy changes**, only platform-admin-only endpoints and service checks.
- **Instructors can't write `lesson_progress`.** Its write policy allows platform admins, the
  student, and the org's `org_admin`, so "complete when graded" needs a new write path
  (decision 1).
- **Students can't upload files.** `files` is writable only by owner-org course editors, so file
  submissions need a new file kind and policy branch (decision 3).
- **Seed and fixtures:** `make seed` links the realm's dev users to orgs and batches. Fixtures
  exist: `apps/api/tests/fixtures/video.mp4` (12 s) and `apps/web/e2e/fixtures/handout.pdf`.
- **Images and runtime config:** CI's `docker` job builds both `runtime` images but doesn't push
  them. The web image is the standalone server and has no `NEXT_PUBLIC_*` build-time values, so
  runtime `.env` is enough.

### Keycloak realm JSON: what `.env` can't change today

`infra/local/keycloak/realm/skillifyme-realm.json` already uses Keycloak's import-time
placeholders for `KEYCLOAK_REALM`, `WEB_ORIGIN` (redirect URIs, web origins, post-logout URIs)
and the three client secrets. Everything else is fixed:

| # | Item | Why it matters on a server |
|---|---|---|
| 1 | 12 dev users with **pinned ids**, `.local` emails, and the literal password `Local-Dev-Only-1` | Dev-only accounts with a known password would exist on any server. Nothing in the code references the pinned ids; the password is used by `e2e/helpers.ts`, `test_keycloak_integration.py` and the README |
| 2 | `smtpServer` is `mailpit:1025`, `from: no-reply@skillifyme.local`, "SkillifyMe (local)", with no auth or TLS fields | Invitation emails can't go anywhere else. The `SMTP_HOST`/`SMTP_PORT` keys already in `.env.example` are **unused** |
| 3 | `bruteForceProtected: false` | Must be on outside local (`docs/access-control.md`) |
| 4 | `skillifyme-test` client (direct password grants) | Must not exist on a server. `OIDC_ALLOWED_CLIENTS` names it |
| 5 | `sslRequired: "external"`, token and session lifespans, display name | Workable on a server, but fixed values |
| 6 | Service-account roles of `skillifyme-admin` (`manage-users`...) | Fine, but fixed |
| 7 | **Import runs only when the realm doesn't exist yet** (`--import-realm` skips an existing realm) | Editing the template never updates a running server's realm |
| 8 | The Keycloak service runs `start-dev`, with `KC_HOSTNAME=http://localhost:${KEYCLOAK_PORT}` and Keycloak's built-in dev database (no volume) | A server needs production mode, HTTPS hostnames and a real database |
| 9 | Google IdP: added by `configure-google.sh`; its documented redirect URI is localhost | Needs the public Keycloak URL |

**Hardcoded externally visible URLs elsewhere:**
- `docker-compose.yml` builds these from `localhost`:
  - `WEB_ORIGIN` (api, keycloak and web services)
  - `S3_PUBLIC_ENDPOINT_URL`
  - `MINIO_API_CORS_ALLOW_ORIGIN`
  - `KC_HOSTNAME`
  - Redpanda's external advertised address
- Code falls back to `localhost` in `app/core/config.py:129`, `apps/web/src/server/config.ts:46`,
  `features/catalog/api.ts:14` and `lib/api/client.ts:17`.

The following are container-internal and can stay: service DNS names (`api:8000`,
`minio:9000`...), health probes on `127.0.0.1` inside containers, and CI runner addresses.

**Cookies:** cookies use the `__Host-` prefix, which forbids a `Domain` attribute and requires
`Secure` on every host. The brief's "cookie domain and Secure flag" settings would weaken that, so
this plan doesn't add them (decision 11).

## 2. Design

### A. Platform admin (`/platform`)

**API.** Platform-admin-only endpoints. Each module adds them to its own router, under a
`/platform` prefix, gated by `require_platform_admin` in its service. RLS is unchanged; the
existing `PLATFORM_ADMIN` branches already allow these reads.

| Endpoint | Module | Notes |
|---|---|---|
| `GET /platform/users?q=&role=&organization_id=&status=` | identity | Cursor pagination. `q` searches name and email through the existing trigram indexes |
| `GET /platform/users/{id}` | identity | The user plus every membership (org, roles) and batches |
| `POST /platform/users/{id}/disable`, `/enable` | identity | Sets `users.status`, sets Keycloak `enabled`, and on disable ends the user's Keycloak sessions. Audit row. A platform admin can't disable themselves |
| `POST /platform/organizations/{id}/admins` | identity | Invites an `org_admin` into that org, wrapping the existing invitation service. The browser can't set `X-Organization-Id` per request for another org, so this is a dedicated endpoint |
| `GET /platform/courses?organization_id=&status=` | courses | Owner, status, latest version, assignment count. Read-only; batched counts, no N+1 |
| `GET /platform/summary` | new `platform` module (router and service only, **no tables**) | Calls each module's service for counts: orgs (active and archived), users by role, courses by status, enrollments by status, active today |

Organizations use the existing endpoints. I'll add `q=` to `GET /organizations` (a small
addition). The audit log uses `GET /audit-log` plus `organization_id` and `from`/`to` filters,
for platform admins only.

**Web.**
- `/platform` is the dashboard of counts.
- `/platform/organizations` and `/platform/organizations/[id]`: create, edit, archive, invite
  the admin.
- `/platform/users` and `/platform/users/[id]`: memberships, disable and enable.
- `/platform/courses` is read-only.
- `/platform/audit` is filterable by org, action, actor and date.
- `PlatformShell` gates on `me.is_platform_admin`, and `proxy.ts` gains `/platform/:path*`.
- **No impersonation.**

### B. College admin

- **`/admin/courses`:** courses granted to the active org (`GET /courses?owned=false`). Each row
  expands to the batch checkboxes, reusing `assignments-panel.tsx`'s distribution part. The API
  doesn't change; narrowing exists already.
- **`/admin/batches/[id]`** gains a "Courses and progress" section: for each course assigned to
  the batch, the students' progress. It uses the D endpoint, so there is one query path.

### C. Assignments, thin slice (new module `assignments`)

**Tables:**
- **`assignments`** belongs to the **course owner org**. It is authored inside the course builder,
  like lessons:
  - columns: `id`, `organization_id`, `course_id`, `lesson_id` (unique), `title`,
    `instructions` (Tiptap JSON, validated by `courses/notes.py`), `due_at`, `max_marks`
    (1–1000), `submission_kinds` (a subset of `{file, text}`, at least one)
  - RLS is the same as the draft tables: editors only
- **`assignment_submissions`** belongs to the **student's org**. It holds one row per
  `(assignment_id, enrollment_id)`, the "active submission":
  - columns: `id`, `organization_id`, `assignment_id`, `version_id`, `lesson_id`,
    `enrollment_id`, `user_id`, `kind`, `text_body` (≤ 20 KB) or `file_id`, `status`
    (`submitted` | `graded`), `submitted_at`, `revision`
  - resubmitting replaces the content until it is graded; after that it is `409 already_graded`
- **`assignment_grades`** belongs to the student's org:
  - columns: `id`, `organization_id`, `submission_id`, `score` (0..max_marks), `feedback`
    (≤ 10 KB, plain text), `graded_by`, `graded_at`
  - `UNIQUE (submission_id)` now. Phase 3 can drop that constraint to keep history, and add
    `submission_attempts` (one row per attempt) referencing `assignment_submissions`. No renames
    are needed.

**Versioning:**
- At publish, the snapshot stores each assignment lesson's title, rendered instructions HTML,
  `due_at`, `max_marks` and `submission_kinds`. Students and graders read the version the
  student was shown.
- Changing `max_marks` or `submission_kinds` is **structural**: it blocks a minor release, like
  `completion_threshold`. Title, instructions and due date are minor-safe.
- Assignment lessons become **required**: they leave `PLACEHOLDER_LESSON_TYPES`, while quiz and
  lab stay placeholders. Existing versions keep their snapshotted `is_required`.

**Endpoints** (MATRIX rows for all; 404-not-403 across orgs and for other students):

| Endpoint | Who |
|---|---|
| `GET`, `PUT /courses/{id}/lessons/{lesson_id}/assignment` | Owner-org editors. `PUT` requires `If-Match` (course revision; 428 when missing, 409 when stale) |
| `GET /enrollments/{id}/lessons/{lesson_id}/assignment` | The enrolled student: the assignment, their submission, and the grade with feedback once graded |
| `PUT /enrollments/{id}/lessons/{lesson_id}/submission` | The enrolled student. `If-Match` is the submission revision, or `0` for the first submission (428 when missing) |
| `GET /courses/{id}/lessons/{lesson_id}/submissions?status=&batch_id=` | Grading staff of the student's org. Ungraded first, then oldest submitted. Cursor pagination |
| `GET /assignment-submissions/{id}` | Grading staff. Includes a signed file URL |
| `PUT /assignment-submissions/{id}/grade` | Grading staff. `If-Match` is the submission revision. Writes the grade, audits `assignment.graded`, completes the lesson and recomputes course progress in the **same transaction** |

**File submissions** reuse the presigned POST and confirm flow with a new `files.kind =
'submission'` (decision 3). RLS: the uploading student writes and reads their own; grading staff
of the student's org read them; the course owner org doesn't see them.

**Events** (on `learning.enrollments.v1`, keyed by enrollment, with schemas in `docs/events.md`
and `test_event_schemas` coverage): `assignment_submitted` and `assignment_graded`. The existing
`lesson_completed` follows the grade.

**Module boundaries:**
- `assignments` → `enrollments.service` to authorize the student and record completion.
- `assignments` → `courses.service` for the pinned version's lesson.
- `assignments` → `media.service` for files.
- Nothing imports `assignments` except the `reports` module (D).

### D. Progress view (new module `reports`, read-only, no tables)

- **API:** `GET /courses/{id}/progress?batch_id=` returns one page of students: name, email,
  progress %, last activity, completion per lesson of their version, and assignment status per
  assignment lesson. `GET /courses/{id}/progress.csv?batch_id=` returns the same data for the
  whole batch, streamed in pages.
- **Who:** `org_admin` and `instructor` of the active org, for batches of **their own org** that
  have an assignment for the course. RLS on enrollments already limits staff to their own org.
- **How it reads:**
  1. Identity service pages the batch's student ids.
  2. One batched enrollments query plus one `lesson_progress` query per page.
  3. One assignments-status query per page.
  4. Course version lessons come from the version cache.

  No N+1, and a test counts the statements.
- **Stopgap:** a code comment says Phase 5 replaces this with ClickHouse.
- **Web:** `/teach/courses/[id]/progress` is a batch picker plus a table that scrolls
  horizontally at 360px, with a sticky name column, ticks per lesson, and Download CSV.

### E. Role-aware home and header

- **`/`** is a server component that reads `/me` with the session:
  - signed out: the current landing page
  - one destination: a redirect
  - several: a chooser
- **Destinations:**
  - `platform_admin` → `/platform`
  - `org_admin` in the active org → `/admin`
  - `instructor` → `/teach`
  - `student` → `/learn`
  - `lab_author`-only users see the chooser with "nothing here yet"
- **Chooser:** it lists (org, area) pairs. Choosing one sets the org through the existing
  `/auth/org` route and goes to that area.
- **Header:** shows the active org and the **active role**, derived from the area you are in
  (decision 8). It adds links to the areas you can use.

### F. Demo seed (`make seed-demo`)

- **Script:** `python -m app.cli.seed_demo` in a compose service, the same in local and server
  modes. It talks to Keycloak and Postgres only through `.env` settings.
- **Idempotent "ensure" semantics:** it creates whatever is missing and never reverts what demo
  users changed. `--reset` restores the demo state and `--rotate-passwords` issues new passwords.
- **Orgs and batches:** it ensures SkillifyMe (publisher), Demo College and CSE 2026 itself, so it
  doesn't depend on `make seed`.
- **Users:** created in Keycloak through the admin API (`enabled`, `emailVerified`, a password,
  no email sent) and linked in Postgres.
  - `demo.platform-admin@skillifyme.co.in` (realm role `platform_admin`)
  - `demo.author@` (SkillifyMe instructor, owns the course)
  - `demo.admin@` (Demo College `org_admin`)
  - `demo.instructor@` (Demo College instructor, grades)
  - 8 students `demo.student@`, `demo.student2@` … `demo.student8@` (decision 7), all in
    CSE 2026, with realistic Indian names
- **Passwords:** 20 random characters each, from `secrets`. They are written only to
  `${DEMO_CREDENTIALS_FILE}` (default `.secrets/demo-credentials.txt`, created with mode 0600 and
  gitignored), never printed or logged. The script prints only the file path.
- **Course "Python Foundations"**, owned by SkillifyMe, built through the services:
  - Module 1 "Getting started":
    - "Why Python" (video, bundled MP4 fixture)
    - "Variables and types" (notes with code blocks)
    - "Cheat sheet" (PDF fixture)
  - Module 2 "Control flow":
    - "Loops" (notes with code)
    - "Functions" (video)
    - "Mini project: FizzBuzz" (assignment, max 10 marks, text or file)
  - It is published as 1.0, granted to Demo College and assigned to CSE 2026, and fan-out
    enrolls the students.
- **Progress, written through the enrollments and assignments services as each user:**
  - 2 students at 0%
  - 3 partial (17–67%)
  - 2 who submitted the assignment, one of them graded
  - 1 who completed everything (100%, with the graded assignment)

  That gives 3 submissions, 1 graded.
- **Dev-only domains:** `.local` and `.test` addresses never appear in the demo seed.

### G. Deployment readiness (nothing is deployed)

- **URLs from `.env`:**
  - `WEB_ORIGIN`, `KEYCLOAK_PUBLIC_URL`, `KEYCLOAK_INTERNAL_URL`, `S3_PUBLIC_ENDPOINT_URL`,
    `S3_ENDPOINT_URL`
  - `API_PUBLIC_URL`, used only for the Bunny webhook URL, so optional
  - Derived from these: `KC_HOSTNAME`, issuer, JWKS, realm redirect URIs, MinIO CORS origins
  - Compose stops building any of them from `localhost`. `.env.example` keeps the local values,
    and the code drops its `localhost` fallbacks (required settings instead).
- **Realm template** `infra/keycloak/realm.template.json`:
  - It is rendered at startup by a one-shot `keycloak-realm` service. The renderer is a small
    Python script with `string.Template` that fails on any unset variable.
  - Its output goes to a volume that Keycloak imports.
  - It contains no users. The dev client `skillifyme-test` is a separate fragment, merged only
    when `KEYCLOAK_DEV_CLIENTS=true`; it is true in `.env.example` and false on a server.
  - `smtpServer` comes from `SMTP_*`; `bruteForceProtected` comes from
    `KEYCLOAK_BRUTE_FORCE=true|false`.
  - **Existing realms:** a `keycloak-sync` one-shot (`kcadm`, idempotent) re-applies the
    templated settings: client redirect URIs and web origins, SMTP, brute force (decision 4).
- **Dev users move from the realm JSON to `make seed`:** created through the admin API, only when
  `SEED_DEV_USERS=true` (`.env.example` true; a server false). The password comes from
  `DEV_USER_PASSWORD` in `.env.example`; existing tests read it from there.
- **`docker-compose.prod.yml`** (an override):
  - images `${API_IMAGE}` and `${WEB_IMAGE}`: the CI-built `runtime` targets, so the standalone
    web server and the API without reload
  - no bind mounts, and `ports: !reset []` on every service
  - one `edge` network shared with a `caddy` service, the only one publishing 80 and 443
  - Keycloak `start --optimized`, with its own `keycloak` database on the same Postgres
    (decision 5), `KC_PROXY_HEADERS=xforwarded`, and brute-force detection on
  - Redis: AOF, `noeviction` and `maxmemory` from `.env`
  - Mailpit kept behind Caddy basic auth
  - restart policies, health checks and log rotation (`json-file` max-size)
- **`infra/caddy/Caddyfile`** (template from env):
  - `{$WEB_HOST}` → web
  - `{$AUTH_HOST}` → keycloak, with `/admin` blocked except from `{$ADMIN_ALLOW_CIDR}`
  - `{$FILES_HOST}` → minio
  - `{$MAIL_HOST}` → mailpit, with `basic_auth` from `MAILPIT_BASIC_AUTH_HASH`

  Hostnames are only `.env` values. The intended `dev.skillifyme.co.in`, `auth.dev…`,
  `files.dev…` and `mail.dev…` appear only in `.env.dev-server.example` and the runbook.
- **`.env.dev-server.example`:** every key that changes on a server, with `<DOMAIN>` and
  `<SECRET>` placeholders and no real values. `SMTP_*` is added to `.env.example`, and Mailpit
  stays the default.
- **`tests/test_config_ports.py` becomes `test_config_hosts.py`:**
  - it fails on `localhost`, `127.0.0.1`, `:3000`, `:8000`, `:8080`, `:9000`, `:9001`, `:8025`
    or `:1025` in app code, compose files, the Caddyfile or the realm template
  - it allows an explicit, commented list of exceptions: container-internal health probes and
    service-DNS URLs
- **CI:**
  - builds the `runtime` images the override references
  - runs `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` with a
    server-style env rendered from `.env.dev-server.example`, using example values on
    `example.test`
  - runs `caddy validate` (the official image)
  - no pushes (decision 10)
- **`infra/dev-vm/README.md`:** the runbook, marked **"not yet executed"**. It covers provision,
  DNS records, first deploy, backups (`pg_dump` and MinIO mirror), rotating secrets, restore and
  tear down. It states that this is a dev/demo environment, not the `docs/architecture.md`
  production topology.

### H. Tests

- **MATRIX rows** for every new endpoint, plus `docs/access-control.md` updates.
- **RLS tests for assignments:**
  - a student sees only their own submission
  - only the student's org staff can grade
  - the owner org can't see other orgs' submissions
  - an unassigned org sees nothing
- **Grading:** a grade completes the lesson and recomputes progress (100% when last). A
  re-grade, if allowed, doesn't double-emit.
- **Reports:** a statement-count test for the progress page, and a CSV test.
- **Vitest:** the grade form (bounds, `If-Match`, 409) and the role home (redirect per role, the
  chooser).
- **Playwright**, at desktop and 360px:
  - the platform admin creates a college and invites its admin (checked in Mailpit)
  - the college admin assigns a course to a batch from `/admin/courses`
  - the instructor grades a submission
  - the student sees the grade and 100%
  - a read-only demo smoke spec signs in as each demo user after `make seed-demo`
    (decision 6)
- **CI config checks:** images, `compose config`, `caddy validate`.

## 3. Build steps

Every step ends with:
1. `make lint` and `make test`.
2. MATRIX rows and `make gen-api`.
3. Commit, push, and a **green GitHub Actions run, with its URL in the summary**.
4. Deviations recorded here, then wait for "continue".

1. **Platform admin API:**
   - `/platform/users*` (list, detail, disable/enable with Keycloak), `/platform/courses`,
     `/platform/organizations/{id}/admins`, `/platform/summary` (`platform` module)
   - `q=` on organizations; org and date filters on the audit log
   - MATRIX and `docs/access-control.md`
2. **Platform UI, role-aware home and header (A and E):**
   - `/platform/*`, `PlatformShell`, `proxy.ts`, the `/` redirect and chooser, the header
   - Vitest for the role home; Playwright for the platform admin creating a college and its admin
3. **Assignments backend (C):**
   - migration 0010 (three tables, `files.kind` `submission`, RLS, the completion write path)
   - service, router, events and `docs/events.md`
   - versioning (snapshot fields, the structural check) and the required lesson type
   - tests
4. **Assignments UI:**
   - teach: assignment lesson editor, submissions list, grade form
   - learn: submission form (text or file), status, score and feedback
   - Vitest for the grade form; Playwright for the instructor grading and the student seeing the
     grade and 100%
5. **Progress (D) and the college-admin screens (B):**
   - the `reports` module with progress and CSV, `/teach/courses/[id]/progress`
   - `/admin/courses`, and progress on `/admin/batches/[id]`
   - Playwright for the college admin assigning from `/admin/courses`
6. **Demo seed (F):** `app.cli.seed_demo`, `make seed-demo`, the credentials file, the bundled
   fixtures, and a demo smoke spec.
7. **Deployment readiness (G):**
   - the realm template and renderer, `keycloak-sync`, dev users moved into `make seed`
   - URL settings, with the localhost fallbacks removed
   - `docker-compose.prod.yml`, the Caddyfile, `.env.dev-server.example`, `SMTP_*`
   - `test_config_hosts.py`, CI validation
   - `infra/dev-vm/README.md`
8. **Close-out:**
   - the four-role walkthrough on `make dev` with the demo seed, at 360px
   - `compose config` with a server-style `.env`
   - plan status, deviations and follow-ups
   - tag `v0.2.5` (if you want a tag)

## 3a. Implementation status

### Step 1: platform admin API (2026-10-02)

- **Endpoints** (all `require_platform_admin`, MATRIX rows, `docs/access-control.md`):
  - `GET /platform/summary` (new `platform` module)
  - `GET /platform/users`, `GET /platform/users/{id}`, `POST /platform/users/{id}/disable`,
    `POST /platform/users/{id}/enable`, `POST /platform/organizations/{id}/admins` (identity)
  - `GET /platform/courses` (courses)
  - `GET /platform/audit-log` (audit)
  - `q=` on `GET /organizations`
- **Migration `0010_platform_admin_indexes`:** a trigram index on `lower(organizations.name)`, plus
  `users.status`, `courses.status` and `enrollments.last_accessed_at`. No policy changes.
- **Disable and enable:**
  - Disabling sets `users.status` and Keycloak's `enabled`, and ends the user's Keycloak
    sessions. The Keycloak call is the last step before commit, so a Keycloak failure rolls the
    status back (`503 identity_provider_unavailable`). The cached principal is dropped after
    commit.
  - Tested against the fake and against real Keycloak: login refused, refresh token revoked,
    login works again after enabling.
- **Tests:** `app/modules/platform/tests/test_platform_api.py` (10),
  `test_keycloak_integration.py::test_disable_blocks_password_login_and_refresh`, and the role
  matrix.

**Deviations in step 1:**
1. **The platform audit log is its own endpoint.** I planned to extend `GET /audit-log`, but
   `GET /platform/audit-log` was added instead. The existing endpoint scopes platform admins to
   their active org, while the platform viewer must cover every org whatever is active. Date
   filters (`since`/`until`, time-zone-aware) become UUIDv7 id bounds (`uuid7_floor`), so the
   primary key and `(organization_id, id)` serve them with no new index.
2. **The `platform` module has no `models.py` or `repository.py`.** It owns no tables and only
   composes `identity`, `courses` and `enrollments` service interfaces.
3. **Enabling restores the right status.** It returns a never-signed-in user to `invited` (they
   have a pending invitation, so their setup link still applies), and everyone else to `active`.
4. **Organization search matches the name only, not the slug.** Only the name has a substring
   index.
5. **Platform admins aren't in `users_by_role`.** They are a Keycloak realm role, not a
   membership, and the summary says so.
6. **A Phase 2 watch-tracking flake fixed** (found by `make test`, not caused by step 1):
   - The done-when e2e failed again with bitmap `0x60` (segment 0 never complete). The first
     interval was `[0.0036, 5]`: the browser reported the first position a few milliseconds
     after 0, and the server required coverage to within 1 ms.
   - The Lua write script now accepts gaps up to `SEGMENT_GAP_SECONDS = 0.25` (the tracker's own
     slack) at a segment's edges and between its intervals.
   - A new test shows a 0.2 s hole still completes a segment and a 1 s skip doesn't.

### Step 2: platform UI, role-aware home and header (2026-10-02)

- **`/platform`** (`PlatformShell`, `is_platform_admin`; added to the `proxy.ts` matcher):
  - the dashboard, where the per-role counts are titled **"Users by organization role"** with a
    note that platform admins aren't included (requested at step 1 review)
  - `/platform/organizations`: search, status filter, create (the slug is derived from the name
    and can be edited)
  - `/platform/organizations/[id]`: invite the org admin, the org's admins, settings
    (name, publisher), archive and restore, links to its members, courses and audit log
  - `/platform/users` (search; org-role, status and org filters) and `/platform/users/[id]`
    (memberships, batches, disable/enable with confirmation, never yourself)
  - `/platform/courses` (read-only; owner and status filters)
  - `/platform/audit` (action, target type and IST date filters; org and actor from links)
- **Role-aware home:** `/` sends a single-role user to their area, switching the active org
  first if needed, and shows a chooser for several (org, area) pairs. Signed-out visitors keep
  the landing page.
- **Header:** a second row shows the **active role and org** (the role comes from the area of
  the current path) and links to the areas available in the active org.
- **Tests:**
  - Vitest: `roles`/`RoleHome` (redirect per role, org switch first, chooser, failure, nothing for
    lab-author-only), slugify, IST day bounds, UUID search params
  - Playwright `e2e/platform.spec.ts`, at 360px on the mobile project and checked for no sideways
    scroll: each role's home redirect and header; the multi-role chooser; a platform admin
    creates a college, invites its admin (the email arrives in Mailpit) and finds them in users
    and the audit log; non-platform admins are refused

**Deviations in step 2:**
1. **The home redirect runs in the browser** (`RoleHome`), not in a server component. Deciding
   needs `/me` with a possibly refreshed access token, and only route handlers can write the
   refreshed cookies. A server-side redirect would fail for anyone whose access token just
   expired. The cost is a brief skeleton before the redirect.
2. **The header's old "Admin" link is replaced** by the area links row (Platform admin, Org
   admin, Instructor, Student), filtered to what the active org allows.
3. **Users with only `lab_author`** see "Nothing here yet" at `/`. Coding labs arrive in Phase 4.
4. **Audit rows link to the actor and organization rather than naming them.** There is no batched
   name lookup across orgs yet, and adding one wasn't worth it for this screen.

### Step 3: assignments backend (2026-10-02)

- **Migration `0011_assignments`:**
  - three tables, `assignments`, `assignment_submissions` and `assignment_grades`, each with
    per-operation RLS
  - `files.kind` gains `submission`, with policy branches so students write and read only their
    own uploads
  - helpers `app.lesson_graded` and `app.enrollment_graded`, which add a grader branch to the
    `lesson_progress` and `enrollments` write policies (decision 1)
  - the trigger `app.enrollments_grader_guard`, which lets such a grader change only
    `progress_percent`, `completed_at` and `updated_at`
- **Module `assignments`:**
  - `models`, `schemas`, `repository`, `events`, `service`, `router`
  - tests: 10 API and 4 RLS (raw SQL)
- **Endpoints** (MATRIX rows; `docs/access-control.md`):
  - authoring: `GET` and `PUT /courses/{id}/lessons/{lesson_id}/assignment`
  - students: `GET /enrollments/{id}/lessons/{lesson_id}/assignment`, `POST .../submission-upload`,
    `PUT .../submission`
  - graders: `GET /courses/{id}/lessons/{lesson_id}/submissions` (ungraded first, then oldest;
    `status` and `batch_id` filters; keyset cursor), `GET /assignment-submissions/{id}`,
    `PUT /assignment-submissions/{id}/grade`
- **New permission** `assignment.grade`, held by `org_admin` and `instructor`.
- **Events** `assignment_submitted` and `assignment_graded`, on `learning.enrollments.v1` and keyed
  by enrollment. Their schemas are in `docs/events.md`, and `test_event_schemas` produces them
  through a real flow.
- **Courses changes:**
  - `assignment` is no longer a placeholder lesson type: it is required by default and counts
    toward progress. Its completion rule is `graded`, so `.../complete` answers
    `409 completed_by_grading`.
  - Publishing is blocked (`assignment_not_ready`) until the definition is saved.
  - The snapshot stores `{assignment_id, title, instructions_html, due_at, max_marks,
    submission_kinds}`. Changing `max_marks` or `submission_kinds` makes a release structural
    (decision 12).
  - Lesson `PATCH` can't set an assignment's content
    (`422 assignment_content_managed_separately`).
- **New setting** `SUBMISSION_UPLOAD_MAX_BYTES` (10 MB).
- **Folded in from the step 2 review:**
  - `/` now shows an `AreaSkeleton` shaped like the destination page (tabs, title, tiles or rows)
    before the redirect
  - the lab-author message, comments and docs say coding labs arrive in **Phase 4**
  - `test_config_ports` is one test listing every offender, so the API test count no longer grows
    with web files (1410 → 1190)

**Deviations in step 3:**
1. **A content-source hook instead of a direct call.** `courses` defines `content_sources`
   (`LessonContentSource`), and `assignments` registers itself at startup
   (`app.main.create_app`). Publishing asks the hook for assignment lessons' content. The
   reason: `assignments` must call `courses` (editor checks, versions) and `enrollments`
   (student checks, completion), so `courses` calling `assignments` would create an import
   cycle. **Any other entry point that publishes, such as the step 6 demo seed, must also call
   `register_content_source()`.**
2. **Submissions don't reference the draft rows.** `assignment_submissions.assignment_id` and
   `lesson_id` aren't foreign keys: the draft rows may be deleted while the published version
   and the student's work remain.
3. **Uniqueness:** one submission per `(enrollment_id, assignment_id)`, and one grade per
   `(submission_id, organization_id)`. The second is the composite foreign-key target; Phase 3
   drops it to keep grade history.
4. **Re-grading is allowed.** A grader can correct a grade, which is audited with before and
   after, and emits `assignment_graded` with `regrade: true`. `lesson_completed` is still
   emitted only once.
5. **Scores are decimals.** They have two places (`8.50`) and are sent as strings, in the API and
   in events. Graders can't grade their own submission (`409 cannot_grade_own`).
6. **Instructions can't contain images yet** (`422 instructions_images`). Images would need
   signed URLs per reader, like notes, and that isn't worth it for the thin slice.
7. **Due dates are shown, never enforced.** There is no late policy (out of scope).
8. **The web UI is unchanged in this step.** The builder and player still label assignment
   lessons "Coming soon" until step 4, and a course with an assignment lesson can't be published
   from the UI until its definition is saved (step 4 adds the editor).

### Step 4: assignments UI (2026-10-02)

- **Authors:** the lesson page of an assignment lesson has an "Assignment details" form (title
  students see, maximum marks, due date in IST, allowed kinds) and an "Instructions" editor (the
  notes editor without the image tool). Saving is an outline edit: the course revision is sent
  as `If-Match`, edits are serialized, and a 409 refetches. The publish dialog names lessons
  that are `assignment_not_ready`.
- **Graders:** the course page (owned or assigned) lists "Assignments to grade" from the current
  version for anyone with `assignment.grade`.
  - `/teach/courses/[id]/assignments/[lessonId]` is the queue: "To grade" first, then graded,
    all; filtered by batch.
  - `/teach/submissions/[id]` shows the work (text, or a signed file link) and the `GradeForm`.
    The score is 0 to max marks with two decimals, checked before the API; the submission
    revision is sent as `If-Match`, and a 409 explains and reloads. Saving returns to the queue.
- **Students:** the player renders assignment lessons with the instructions (server-sanitized
  HTML), marks and due date. The student types an answer or uploads a PDF/PNG/JPEG (presigned
  POST, then submit), and can replace the submission until it's graded. Once graded they see the
  score, the feedback and the completion tick.
- **Shared code:** `lib/upload.ts` (presigned POST and safe file names; teach and learn both use
  it, so student pages don't import teach code) and `lib/ist.ts` (IST input and display).
  `assignment` is no longer in the web placeholder sets.
- **Tests:** Vitest for `GradeForm` and `gradeSchema` (bounds, If-Match revision, 409, other
  errors, correcting a grade) and for IST dates. Playwright `e2e/assignments.spec.ts`: define,
  publish, grant, assign to CSE 2026, the student submits at 360px, the instructor grades at
  360px (an over-max score is refused in the form), then the student sees "8.5 / 10", the
  feedback, completion and 100%.
- **Folded in from the step 3 review:**
  - **The publish hook is wired in one place**, `app/wiring.py`.
    `courses.content_sources.required_source` loads it on first use, so every entry point (API,
    workers, seeds, tests) gets it; `create_app` also calls `wire()` at boot.
  - **Publishing fails closed.** With assignment lessons and no registered source, preview and
    publish return `500 content_source_not_registered` (logged as an error), and nothing is
    published. Tested in `test_publish_hook.py`, together with lazy loading for entry points
    that never call `wire()`.
  - **`scripts/ci_status.py` checks the CI run.**
    - It resolves the full sha through git; the API matches it exactly, so short shas found
      nothing.
    - It retries network errors, 5xx responses, rate limits, and empty or non-JSON bodies with
      backoff, and honours `Retry-After` and `X-RateLimit-Reset`.
    - It polls every 60 s, within the anonymous 60 requests/hour, and exits 0 only when every
      job passed.
    - `tests/test_ci_status_script.py` covers the retries.

**Deviations in step 4:**
1. **Grading has its own pages** (`/teach/courses/[id]/assignments/[lessonId]` and
   `/teach/submissions/[id]`) rather than a panel inside the course page. The review page works
   on its own link and on phones.
2. **The instructions editor saves the whole definition.** "Save instructions" sends the details
   form's current values too, because the API takes the full definition.
3. **A student sees a new grade on reload or when the window regains focus.** Nothing pushes it
   live; realtime is Phase 4 (Centrifugo).

### Step 5: progress reports and college-admin screens (2026-10-03)

- **Module `reports`** (no tables; router, schemas, service; a STOPGAP comment says Phase 5
  replaces it with ClickHouse while keeping the API shape):
  - `GET /courses/{id}/progress?batch_id=`: a page of the batch's students by name. Each row has
    progress %, last activity, version, a cell per lesson (completed, in progress, not started,
    not in this student's version) and assignment status or score. The columns are the latest
    version's lessons.
  - `GET /courses/{id}/progress.csv?batch_id=`: the whole batch, fetched in pages of 500, with
    cells neutralized against formula injection.
  - `GET /batches/{id}/courses`: the batch's courses with enrolled, completed and average
    progress.
  - Access: `course.read` staff of the active org, for **their own** batches that have the
    course; everyone else gets 404. MATRIX rows and `docs/access-control.md` are updated.
- **Interfaces added:**
  - identity: `batch_students_page`, keyset by (lowercased name, id)
  - enrollments: `course_progress_for_users`, `course_summaries_for_users`
  - assignments: `submission_states`
  - courses: `outline_lessons`
  - The shared CSV helper moved to `app/core/csv_safety.py`, and identity's import errors CSV
    uses it.
- **Queries per page are constant:** `test_queries_per_page_dont_grow_with_students` counts the
  SQL for a 1-student and a 13-student batch, and they are equal.
- **Web:**
  - `/teach/courses/[id]/progress`: a batch picker (only batches that have the course) and a
    table with a sticky name column that scrolls sideways at 360px while the page doesn't, Load
    more and Download CSV
  - a "Student progress" link on course pages
  - `/admin/courses`: granted courses, each with "Choose batches" (the existing distribution
    panel) and "Progress"; added as the first Admin tab
  - `/admin/batches/[id]`: "Courses and progress"
- **Tests:** API (6 report tests, MATRIX), Vitest (`ProgressGrid` cells, assignment scores,
  column titles), Playwright `e2e/admin-courses.spec.ts`. In that spec the college admin assigns
  a granted course to ECE 2026 from `/admin/courses` on a phone; the batch page shows it; the ECE
  student gets it; the instructor's progress table and CSV work at 360px.

**Deviations in step 5:**
1. **`GET /batches/{id}/courses` is new** (not in the plan). The per-batch view needed the
   batch's courses with a summary in one call, rather than a request per course.
2. **Students are listed by name**, with keyset paging on (lowercased name, id), rather than by
   id.
3. **The CSV is built in memory from 500-row pages** rather than streamed. That is fine for
   batches of a few thousand. The session ends with the request, so streaming would need its
   own session; Phase 5 replaces the source anyway.
4. **Columns follow the latest version.** A student on an older major sees "n/a" for lessons
   their version doesn't have.
5. **`outline_lessons` reads a snapshot defensively** (missing `modules` or `lessons` keys),
   like the web player does.
6. **`/admin` still opens Batches.** Courses is the first tab, but the existing landing (and its
   tests) is unchanged.
7. **Two web bugs found by the 360px checks, both fixed:**
   - The table's screen-reader labels are absolutely positioned and escaped the scroll box,
     widening the page to 440px. The scroll box is now `relative`.
   - The batch picker only knew the first page of 25 batches. It now loads just the batches
     that have the course.

## 4. Decisions (approved 2026-10-02)

All 13 recommendations were approved as written. Additions are marked **Added**.

1. **Completion on grade:** a narrow RLS write branch on `lesson_progress` (and the enrollment's
   progress columns). The student's org staff may write only where a graded submission exists for
   that enrollment and lesson, checked by a `SECURITY DEFINER` helper. Instructors' policy is not
   widened otherwise.
2. **Graders:** `instructor` and `org_admin` of the **student's** org. The course owner org
   neither sees nor grades other orgs' submissions.
3. **Student uploads:** `files.kind = 'submission'`. PDF, PNG and JPEG (signature-checked), up to
   10 MB. Readable by the uploading student and by their org's grading staff.
4. **Realm:** our own template, rendered at startup and fail-closed on unset variables. No users;
   dev-only parts behind settings. An idempotent `keycloak-sync` updates existing realms, and the
   dev users move into `make seed` behind `SEED_DEV_USERS`.
   **Added:** `keycloak-sync` never rotates client secrets, never resets brute-force counters, and
   never deletes anything. A test runs it twice against the same realm and asserts no diff.
5. **Keycloak's database on a server:** a separate `keycloak` database on the same Postgres.
   **Added:** it has its own Postgres role and password (`KEYCLOAK_DB_USER`,
   `KEYCLOAK_DB_PASSWORD`), never the app or migration role.
6. **E2E data:** the functional flows build their own data with the `.local` users, so they can be
   repeated. A read-only smoke spec signs in as each demo user after `make seed-demo`. Only CI
   reads the credentials file.
7. **Demo users:** `demo.student@` and `demo.student2@` … `demo.student8@`. Reruns only create
   what is missing; `--reset` and `--rotate-passwords` are explicit flags.
   **Added:** both flags refuse to run unless the environment is local, or
   `--i-know-this-is-not-local` is given. The brief said `APP_ENV`, but the existing setting is
   `ENVIRONMENT` (`ENVIRONMENT=local` in `.env.example`). The check uses that setting rather than
   adding a second one.
8. **Active role in the header:** derived from the area you're in (`/platform`, `/admin`,
   `/teach`, `/learn`). No new server state.
9. **Active today:** distinct users with learning activity (`enrollments.last_accessed_at`) since
   midnight Asia/Kolkata. `users.last_login_at` already exists from Phase 1 but is never written.
   This counter deliberately doesn't use it, and nothing in this phase starts writing it.
10. **CI images:** built and validated, not pushed. A GHCR push waits for the deployment phase.
11. **No cookie-domain or Secure-flag settings.** The `__Host-` cookies forbid `Domain` and always
    require `Secure`.
12. **`max_marks` and `submission_kinds` changes are structural** (they block a minor release).
    Title, instructions and due date are minor-safe.
13. **Tag `v0.2.5`** after the close-out commit, once its CI run is green (same as `v0.2.0`).
