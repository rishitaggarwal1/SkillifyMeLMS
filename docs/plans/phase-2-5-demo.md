# Phase 2.5 — Demo-ready portal (plan)

**Status: complete (2026-10-04).** Steps 1–8 are implemented, all local checks
pass and both repair commits have green CI. The final documentation close-out
commit must also have green CI before `v0.2.5` is created. See
[Release record](#release-record) and
[Implementation status](#3a-implementation-status). Phase 2 is
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

## Release record

The tables below record what shipped, including failed historical CI runs rather than
retrospectively describing them as green. Release tagging requires green CI on the
close-out commit; check it with `python scripts/ci_status.py <full-sha>`.
Record that final run URL in the annotated `v0.2.5` tag and the release summary;
the table records the implementation and repair commits.

| Step | Commit | GitHub Actions |
|---|---|---|
| 1. Platform admin API | `1713652` | [37025158943](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37025158943) — green |
| 2. Platform UI, role home and header | `d570a13` | [37033530343](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37033530343) — green |
| 3. Assignments backend | `1740ccf` | [37039263527](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37039263527) — green |
| 4. Assignments UI, publish hook and CI checker | `a58adec` | [37046381301](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37046381301) — green |
| 5. Reports and college-admin screens | `f448209` | [37050553924](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37050553924) — green |
| 6. Demo seed, paginated pickers and CSV cap | `de0c652` | [37054584596](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37054584596) — green |
| 7. Deployment readiness | `321bbfc` | [37062261105](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37062261105) — red: web build and smoke failures |
| 7. Build and paged smoke follow-up | `6ebb234` | [37066623603](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37066623603) — red: catalog E2E |
| Handover: root instructions (file-only commit) | `160fc45` | [37193462779](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37193462779) — red: inherited catalog E2E |
| Close-out repairs: catalog paths and prefetch login | `fc87c29` | [37194725913](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37194725913) — green |
| Close-out repairs: demo paging and commit-before-response | `9217b03` | [37197780394](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37197780394) — green |

### Post-handover repairs

- **Catalog invalidation — `fc87c29ee41d84b4e273fd854bc77302ac583d76`:**
  `apps/web/src/app/api/revalidate/route.ts` now calls `revalidatePath("/catalog")`
  and `revalidatePath("/catalog/[slug]", "page")` alongside the existing tag
  expiration. A build without `API_INTERNAL_URL` prerendered an empty catalog
  before making a tagged fetch, so that fallback had no `catalog` data-tag
  dependency to invalidate after publication. This caused both projects of
  `e2e/catalog.spec.ts:27` to fail the published-course link visibility assertion
  (2,000 ms inside a 45,000 ms retry). The app repair preserves the assertion,
  timeouts and setup data.
- **Login prefetch race — `fc87c29ee41d84b4e273fd854bc77302ac583d76`:**
  `apps/web/src/proxy.ts` excludes `next-router-prefetch` and `purpose: prefetch`
  requests from the login-redirect matcher in all four areas. Background
  prefetches had started concurrent OIDC flows and overwritten the active PKCE
  state cookie during role switching. Actual navigation still redirects to
  login, page shells expose no private data and API authorization still applies.
  `apps/web/src/proxy.test.ts` adds 12 navigation/prefetch regression checks;
  `docs/access-control.md` records the contract. No E2E assertions changed.
- **Handover support — `fc87c29ee41d84b4e273fd854bc77302ac583d76`:**
  `.gitignore` adds `.claude/`; the Step 8 implementation notes record the app
  repairs and their causes. Root `AGENTS.md` was committed separately in `160fc45`.
- **Demo paging helper — `9217b03bcb7690d87ec855d745b7999baa0a4c0a`:**
  `apps/web/e2e/demo-smoke.spec.ts` waits for the first course row before checking
  whether the demo course or Load more is visible, then uses its existing paging
  loop. Previously the instructor region rendered before the query finished;
  the helper exited without paging and then waited for a course on a later page.
  The user approved this synchronization correction. All existing assertions,
  timeouts and the 20-page limit are unchanged; all four demo roles pass in the
  full suite at 360px.
- **Commit before response — `9217b03bcb7690d87ec855d745b7999baa0a4c0a`:**
  `apps/api/app/db/session.py` changes the shared `DbSession` dependency to
  `Depends(get_db_session, scope="function")`. Its previous request scope sent
  the HTTP response before exiting `session.begin()` and committing. In the
  failing `e2e/done-when.spec.ts:41` trace, POST `/modules` returned 201 for Arrays,
  then immediate course/draft refetches saw revision 1 and no modules; the test
  timed out at line 35 waiting for the Add lesson form. Every DB write through
  `DbSession`/`TenantSession` shared this risk, including normal CRUD endpoints;
  the Bunny webhook and CLI/background jobs already use explicit transactions.
  Function scope commits and runs after-commit hooks before sending success
  headers, allowing failed commits to reach the existing error middleware.
  `apps/api/tests/test_request_transactions.py` adds exactly two real-Postgres
  regressions: a separate connection sees the API write at response-header
  delivery, and a deferred constraint failure returns the standard 500 envelope,
  writes nothing and runs neither synchronous nor asynchronous after-commit
  hooks. Both tests failed before the source repair and passed afterward. No
  endpoint-specific fix, migration, API schema or E2E assertion change was made.

### Final endpoints

All API routes are under `/api/v1`. Every new method has a `MATRIX` row in
`apps/api/tests/test_endpoint_roles.py` and is documented in `docs/access-control.md`.
Phase 1 and 2 routes remain available; this table lists the Phase 2.5 additions and changes.

| Area | Endpoints and contracts |
|---|---|
| Platform summary | `GET /platform/summary`: platform admins only; active today is learning activity since midnight Asia/Kolkata |
| Platform users | `GET /platform/users`, `GET /platform/users/{id}`, `POST /platform/users/{id}/disable`, `POST /platform/users/{id}/enable`: platform admins only; disable ends Keycloak sessions, refuses self-disable, rolls back on provider failure |
| Platform organizations | `POST /platform/organizations/{id}/admins`: platform admin invites an org admin; existing `GET /organizations` gains name search `q=` |
| Platform courses and audit | `GET /platform/courses`, `GET /platform/audit-log`: platform-wide, whatever org is active; audit filters include `organization_id`, actor, target, action and aware `since`/`until` |
| Assignment authoring | `GET`, `PUT /courses/{id}/lessons/{lesson_id}/assignment`: owner-org editors; `PUT` requires the course revision in `If-Match` (428 missing, 409 stale) |
| Student assignments | `GET /enrollments/{id}/lessons/{lesson_id}/assignment`, `POST .../submission-upload`, `PUT .../submission`: enrolled student only; submission `If-Match` is 0 initially, then its revision; graded work cannot be replaced |
| Grading | `GET /courses/{id}/lessons/{lesson_id}/submissions`, `GET /assignment-submissions/{id}`, `PUT /assignment-submissions/{id}/grade`: instructors and org admins of the student's org; grade uses submission `If-Match`, is audited and completes the lesson in the same transaction |
| Reports | `GET /courses/{id}/progress?batch_id=`, `GET /courses/{id}/progress.csv?batch_id=`, `GET /batches/{id}/courses`: staff of the student's org, own assigned batches only; CSV refuses more than 10,000 students |

Lists are cursor-paginated. Cross-org and other-student resource access returns 404.
Shared request transactions commit before success headers are sent; commit failures
roll back and return the standard error envelope, without running after-commit hooks.
Submission files reuse the existing file confirmation/download routes with PDF/PNG/JPEG
signature checks and a 10 MB cap. No new HTTP routes were added by the seed or close-out.

Web routes added: `/platform` and its organizations, users, courses and audit pages;
`/admin/courses`; `/teach/courses/[id]/progress`,
`/teach/courses/[id]/assignments/[lessonId]`, `/teach/submissions/[id]`.
Existing builder/player routes gained assignment forms; the home and header gained role-aware
destinations. The existing authenticated `POST /api/revalidate` now invalidates the catalog
paths as well as the data tag; protected page prefetches do not start extra login flows.

### Final tables and database changes

| Migration or operator | Changes |
|---|---|
| `0010_platform_admin_indexes` | Organization-name trigram index; `users.status`, `courses.status` and `enrollments.last_accessed_at` indexes. No RLS policy changes |
| `0011_assignments`: `assignments` | Owner-org draft definitions: course/lesson, title, instructions, due date, maximum marks and allowed submission kinds; one definition per lesson; per-operation RLS |
| `0011_assignments`: `assignment_submissions` | Student-org active submission, pinned version, text or file, status and revision; unique `(enrollment_id, assignment_id)`; draft assignment/lesson IDs deliberately have no FK |
| `0011_assignments`: `assignment_grades` | Student-org score (`numeric`, two decimals), feedback, grader and timestamp; unique `(submission_id, organization_id)` and a composite submission FK; re-grades update this row |
| `0011_assignments`: existing tables | `files.kind` gains `submission` with student/grader policies. `app.lesson_graded` and `app.enrollment_graded` permit completion writes for graded work; `app.enrollments_grader_guard` restricts a grader to progress columns |
| `db_roles` on servers | A separate Keycloak login role and owned database, only when `KEYCLOAK_DB_PASSWORD` is configured; not an app migration or a new LMS table |

`platform` and `reports` own no tables. Assignment content is published in immutable course
snapshots; `assignment_submitted` and `assignment_graded` use the enrollment outbox topic.
The demo seed and close-out add no migration.

### Full deviation list

This consolidates every numbered step deviation; the implementation notes below retain the
context and tests. Later repairs supersede earlier interim behavior where stated.

1. Platform audit has its own `/platform/audit-log` endpoint; UUIDv7 time bounds reuse existing indexes.
2. The tableless `platform` module omits models/repository and composes service interfaces.
3. Enabling restores `invited` for never-signed-in invitees and `active` for everyone else.
4. Organization substring search matches names, not slugs.
5. Platform admins are excluded from membership-role counts and the UI explains why.
6. A Phase 2 watch-tracking flake was repaired with 0.25 s segment-gap tolerance; larger skips remain uncredited.
7. Role-home redirects run in the browser so refreshed cookies can be written by route handlers.
8. Area links replace the old Admin header link.
9. Lab-author-only users see "Nothing here yet" until Phase 4.
10. Audit screens link to actors/orgs rather than adding a cross-org name lookup.
11. Assignment publishing uses a content-source hook, centrally wired and lazily loaded; missing registration fails closed.
12. Submission assignment/lesson IDs do not reference draft rows, preserving work after draft deletion.
13. There is one submission per enrollment/assignment and one grade per submission/org; the composite grade target can change for Phase 3 history.
14. Re-grading is allowed, audited and emits a re-grade event; completion still emits once.
15. Scores are two-place decimal strings; graders cannot grade themselves.
16. Assignment instructions do not support images in this slice.
17. Due dates are displayed, not enforced; late policy is deferred.
18. Assignment UI remained unchanged during the backend step and arrived in the following UI step.
19. Grading uses dedicated queue/review pages instead of an inline course panel.
20. Saving instructions saves the current whole assignment definition.
21. Students see grades on reload/focus; no realtime push until Phase 4.
22. `GET /batches/{id}/courses` was added for a batched course-summary view.
23. Reports page students by `(lower(name), id)` instead of id alone.
24. CSV exports are built in memory from 500-row pages, with the later 10,000-student cap and a surfaced download error.
25. Report columns use the latest outline; students on older majors see "n/a" for missing lessons.
26. `outline_lessons` tolerates missing snapshot keys, like the player.
27. `/admin` still lands on Batches although Courses is the first tab.
28. The 360px review also fixed escaping screen-reader labels and the first-page-only progress batch picker.
29. The demo seed calls services with each demo user's context instead of signing in over HTTP.
30. Seed video watch credit uses a guarded CLI helper; normal heartbeat credit remains bounded by real time.
31. Credentials have Linux mode 0600; Windows bind mounts retain NTFS permissions.
32. The demo seed ensures its own orgs and CSE batch instead of depending on `make seed`.
33. Keycloak render/sync uses standard-library Python instead of `kcadm`.
34. Server Keycloak uses `start`, not `start --optimized`; no custom pre-built image was added.
35. Container-internal service-DNS URLs remain fixed; host-side URLs come from `.env`.
36. No `API_PUBLIC_URL` was added because the browser uses the BFF and Bunny needs a separate public route first.
37. Local brute-force protection stays off for automated shared-user logins; the server override forces it on.
38. Local Keycloak retains its dev store; servers use Postgres. Seed reruns repair account links/passwords after local container recreation.
39. `KEYCLOAK_PORT` is compose-only; app settings use `KEYCLOAK_PUBLIC_URL`.
40. The host sweep excludes local tooling where local addresses belong.
41. Read-only configuration mounts remain on servers; application-source mounts are removed.
42. Runtime images are built on the VM until a deployment phase enables CI registry pushes.
43. Close-out adds root handover instructions and ignores `.claude/` alongside `.secrets/`.
44. Close-out repairs catalog path invalidation for empty build-time fallback pages, preserving the E2E visibility assertion and timeout.
45. Close-out excludes background prefetches from login redirects, preventing concurrent flows from overwriting PKCE state; actual navigation and API authorization remain protected.
46. The approved demo smoke helper waits for initial course rows before checking pagination; existing assertions, timeouts and the paging limit are unchanged.
47. The shared API session dependency uses function scope so commit and after-commit hooks finish before response delivery; failed commits can return the standard error envelope. This repairs the read-after-write race exposed during full E2E verification, without endpoint-specific changes.

The first-page audit in step 6 also widened existing pickers to all pages (with a documented
20-page/2,000-item cap) and added Load more to member candidates. It did not add feature scope.
Local production verification uses the documented Windows host-web fallback, with the
worker's revalidation URL pointed at the host; containerised `next dev` on this bind mount
is not the production E2E check.

### Open follow-ups / carried forward

- **Phase 3 assignments:** rubrics, late policy, attempt/grade history, plagiarism and AI feedback are deferred. Instructions images are also deferred.
- **Phase 4:** coding labs for lab authors and Centrifugo grade notifications; current grade reads refresh on focus/reload.
- **Phase 5 reports:** replace the OLTP stopgap with ClickHouse, preserving its API shape; revisit in-memory CSV and its 10,000-student limit.
- **Large directories/pickers:** move beyond the documented 2,000-item cap when needed. Platform organization details still show only the first 25 org admins, acceptable for a handful.
- **Bunny:** the real-credential smoke script remains unrun; no library credentials are available. Mocked HTTP tests and local MinIO video are the verified paths.
- **Publisher directory:** revisit global active-org name discovery before granting a second org `is_content_publisher`.
- **Skills:** replace the whole-taxonomy name cache with a server-side `ids=` lookup beyond about 500 skills.
- **Read replicas:** enrollment/resume GETs reset replaced-video progress; route those writes to the primary or move them off the read path before enabling replicas.
- **Heartbeat revocation window:** playback is denied immediately, but up to 60 s of watch credit may survive until reconciliation/cache expiry.
- **Deployment:** nothing is provisioned or deployed. `infra/dev-vm/README.md` remains "not yet executed"; CI builds but does not publish images. Keycloak's non-optimized boot and server backup/restore operations remain operational follow-ups.
- **Windows:** WSL2 remains the recommended checkout location; host web is the fallback. Credentials on a Windows bind mount retain host NTFS permissions.

The earlier Redis persistence requirement is addressed in the server override (AOF,
`noeviction`, configured `maxmemory`); it is not an unfinished Phase 2.5 item.

### Close-out verification (2026-10-04)

- `make dev` passed after starting Docker Desktop. GNU make is installed outside
  PATH on this Windows machine; verification adds its installation directory to
  the process PATH rather than changing the repository's commands.
- `make lint`: passed (ruff, format, mypy, ESLint, Prettier and TypeScript).
- `make test`: passed in one invocation: 1,259 API tests against the real compose
  backing services, 157 web unit tests (including 12 proxy navigation/prefetch
  checks), and the full Playwright suite with **41 passed, 7 intentional skips,
  0 failures** using the unchanged local two-worker configuration.
- `make gen-api`: passed; generated API types and notes CSS are unchanged.
- `make seed-demo`: passed without reset or password rotation; the existing
  credentials were reused and never printed.
- The read-only `e2e/demo-smoke.spec.ts` passed all four roles in the full suite
  at 360px: platform admin landed in `/platform`, college admin in `/admin`,
  instructor in `/teach`, student in `/learn`. No demo work was submitted or graded.
- The earlier full-suite failures (smoke paging, then commit-before-response)
  are recorded under Post-handover repairs. Both catalog projects and the
  previously failing course-completion flow pass in the final full run.
- `docker compose --env-file .env.server -f docker-compose.yml -f
  docker-compose.prod.yml config --quiet`: passed, also with tools/debug profiles.
  `.env.server` was generated by `scripts/server_env_for_ci.sh` with fake
  `example.test` values and is ignored. No server was deployed.
- `caddy validate`: passed in the official Caddy image with that server-style
  environment and a throwaway password hash. The first invocation hit a Windows
  quoting error; the identical CI command through Git Bash passed.
- Production builds without runtime settings passed. The unchanged catalog
  spec failed before the path repair and passed in both projects afterward.
- Repair commit `fc87c29` has green CI across all six jobs:
  [37194725913](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37194725913).
- Repair commit `9217b03` has green CI across all six jobs:
  [37197780394](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37197780394)
  (verified with `scripts/ci_status.py`, exit 0). The final documentation close-out
  commit is checked with the same script; `v0.2.5` is created only after that run
  is green, and its annotated tag records the final commit's CI run URL.

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

### Step 6: demo seed (2026-10-03)

- **`make seed-demo`** (`app/cli/seed_demo.py`; compose service `seed-demo`, profile `tools`):
  - Logins `demo.<role>@skillifyme.co.in`: platform-admin, author, admin, instructor, student,
    student2 … student8.
  - "Python Foundations": 2 modules and 6 lessons (two videos from the bundled 12 s MP4, notes
    with Python code, a PDF, the FizzBuzz assignment). Published 1.0, granted to Demo College,
    assigned to CSE 2026.
  - Progress 100, 83, 66, 50, 33, 16, 0 and 0%, with 3 submissions and 1 graded (9/10).
  - Built **through the services as each demo user** (RLS, assignment, grade and completion
    rules all apply). Enrollment fan-out runs inline after commit; the publish hook loads
    through `app.wiring`.
- **Passwords:**
  - 20 random characters from `secrets`, with all four character classes.
  - Written only to `DEMO_CREDENTIALS_FILE` (compose: `./.secrets/demo-credentials.txt`; mode
    0600 in Linux containers; `.secrets/` is gitignored). Never printed or logged.
  - A rerun reuses them. `--rotate-passwords` replaces them.
  - `--reset` and `--rotate-passwords` refuse unless `ENVIRONMENT=local` or
    `--i-know-this-is-not-local`. Nothing runs in production.
- **Operator interfaces** (no HTTP routes): `media.import_local_video` and `media.import_file`
  (the same size and signature checks as uploads), `enrollments.mark_video_watched` (real
  watching is bounded by wall-clock time), and Keycloak `prepare_login` and `grant_realm_role`
  (looked up through the user's assignable roles, so the service account needs nothing new).
- **Tests:**
  - `tests/test_seed_demo.py`: the guard table, password strength, the credentials file, and a
    full run against the test database and real Keycloak on a throwaway domain. It runs twice
    (file and data unchanged), then `--reset` restores the plan and `--rotate-passwords`
    replaces every password.
  - Keycloak integration for the new admin calls.
  - `e2e/demo-smoke.spec.ts`: read-only, tracing off. Each demo role signs in at 360px and lands
    where it works, with the demo data present.
  - CI's E2E job runs the seed twice, then the smoke spec.
- **Folded in from the step 5 review:**
  - **CSV cap.** `GET .../progress.csv` refuses batches over 10,000 students (counted first,
    and re-checked while paging) with `422 export_too_large` and the counts in `details`.
    "Download CSV" is now a button that fetches the file and shows that error rather than
    saving it as a file.
  - **First-page-only audit.** Everything checked, with what was found:

    | Place | Finding | Fix |
    |---|---|---|
    | Grading: batch filter | first page (25) | all pages (`allBatchesQuery`) |
    | Imports: target batch | first page | all pages |
    | Members: invite dialog batches | first page | all pages |
    | Lesson page: "Ready videos" select | first page | all pages (`allReadyVideosQuery`) |
    | Course assignment rows (distribution panel; progress page picker) | first page of 100 | all pages (`allAssignmentsQuery`) |
    | Batch page: "Add members" candidates | first 25, no Load more | Load more added |
    | Progress page batch picker | fixed in step 5 | (per-batch lookups) |
    | Assignments panel "Your batches" | Load more | fine |
    | Org grant search, skills picker | search-driven | fine |
    | Skills name lookup, learner dashboard | read all pages (20-page cap, documented) | fine |
    | Platform users, orgs, courses, audit; admin lists; submissions queue | lists with Load more | fine |
    | Platform org page "Org admins" | first 25 | fine for a handful of admins; noted |
    | Course "Published versions" | the latest 10 by design | fine |

    `lib/api/all-pages.ts` stops at 20 pages (2,000 items) and the pickers then say "Showing
    the first 2,000".

**Deviations in step 6:**
1. **The seed calls services directly** with a request context per demo user, rather than going
   through HTTP. Keycloak password grants exist only in the dev realm, so HTTP sign-in wouldn't
   work on a server, and the services apply the same rules.
2. **Videos are marked fully watched by an operator function.** Real heartbeats earn at most
   real time, so a seed can't produce watching quickly.
3. **The credentials file is 0600 inside Linux** (the container, a server). On a Windows bind
   mount the host shows NTFS defaults.
4. **The seed ensures its own orgs and the CSE 2026 batch,** so it doesn't depend on
   `make seed`.

### Step 7: deployment readiness, nothing deployed (2026-10-03)

- **URLs from `.env`:**
  - `WEB_ORIGIN`, `KEYCLOAK_PUBLIC_URL` and `S3_PUBLIC_ENDPOINT_URL` are required wherever they
    are used (API settings, the web app's zod config, compose). The `localhost` fallbacks are
    gone, including `API_INTERNAL_URL` in the web app and `KAFKA_BOOTSTRAP_SERVERS` in the API.
  - Compose derives `KC_HOSTNAME`, MinIO's CORS origin and Redpanda's external address
    (`KAFKA_EXTERNAL_HOST`) from them. The API derives the issuer, JWKS, token and admin URLs.
  - `.env.example` writes `KEYCLOAK_PUBLIC_URL=http://localhost:${KEYCLOAK_PORT}`, so changing
    the port still changes one line (compose, pydantic-settings and make all expand it).
- **Realm template** (`infra/keycloak/`):
  - `realm.template.json` has no users except the admin client's service account. The
    `skillifyme-test` client lives in `realm.dev-clients.json`, merged only with
    `KEYCLOAK_DEV_CLIENTS=true`.
  - Display name, SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_FROM_NAME`, optional
    `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_STARTTLS`, `SMTP_SSL`) and brute force
    (`KEYCLOAK_BRUTE_FORCE`, default on; 10 failures, 60 s steps, 15 min maximum, 12 h reset)
    come from `.env`.
  - `realm.py render` (the `keycloak-realm` one-shot) fails closed and names every missing
    setting. Keycloak imports the result from a volume.
  - `realm.py sync` (the `keycloak-sync` one-shot, after Keycloak is healthy) makes an existing
    realm match: realm options, SMTP, brute-force settings, the `platform_admin` role, client
    redirect URIs, web origins, flags and attributes, missing protocol mappers, missing clients,
    and the service account's roles. It never deletes, never sends or regenerates a client secret
    (an existing client's PUT omits `secret`), never calls attack detection, and preserves the
    stored SMTP password unless `--update-smtp-password`.
- **Dev users moved to `make seed`:** created through the admin API only with
  `SEED_DEV_USERS=true`, all with `DEV_USER_PASSWORD` (both in `.env.example`). Otherwise the
  seed does nothing. The tests and Playwright read the password from settings and `.env`.
- **Keycloak's own database:** `db_roles` creates the `keycloak` login role (no superuser,
  createdb, createrole or bypassrls) and a `keycloak` database it owns, with `PUBLIC` access
  revoked, only when `KEYCLOAK_DB_PASSWORD` is set (servers).
- **`docker-compose.prod.yml`:**
  - API and web from `${API_IMAGE}` and `${WEB_IMAGE}` (the CI-built `runtime` targets), with no
    source mounts.
  - `ports: !reset []` everywhere; only `caddy` publishes 80 and 443, on the `edge` network with
    web, Keycloak, MinIO and Mailpit.
  - Keycloak runs `start` on Postgres with its own role, with `KC_PROXY_HEADERS=xforwarded`.
    Brute force is forced on and dev clients forced off.
  - Redis AOF (`everysec`), `noeviction`, `maxmemory` from `REDIS_MAXMEMORY`.
  - `unless-stopped` restarts and `json-file` log rotation. Redpanda Console sits behind a
    `debug` profile.
- **`infra/caddy/Caddyfile`:** routes `WEB_HOST`, `AUTH_HOST` (`/admin` 403 outside
  `ADMIN_ALLOW_CIDR`), `FILES_HOST` and `MAIL_HOST` (basic auth). Every hostname is `.env`.
- **`.env.dev-server.example`:** every key that changes, with `<DOMAIN>`, `<SECRET>` and other
  placeholders and no real values. `scripts/server_env_for_ci.sh` turns it into a fake
  `example.test` env for CI and fails on an unknown placeholder.
- **Runbook:** `infra/dev-vm/README.md`, marked **not yet executed**. It covers provisioning, DNS,
  first deploy, upgrades, backups (both databases and the bucket), rotating each secret, restore
  and tear down, and states that this is not the `docs/architecture.md` topology.
- **CI:**
  - The API job runs `db_roles`, migrations and `make seed` (dev users) before pytest, and
    type-checks `realm.py`.
  - The new `server-config` job checks `compose config` for base plus override (all profiles),
    that only Caddy publishes ports and no source is bind-mounted, that the realm renders for a
    server (brute force on, no test client, no `localhost`), and runs `caddy validate`.
  - The `docker` job still builds both runtime images.
- **Tests:**
  - `test_config_hosts.py` replaces `test_config_ports.py`. One sweep over app code (not tests),
    both compose files, both Dockerfiles, `next.config.ts`, the Caddyfile and
    `infra/keycloak/**` for `localhost`, `127.0.0.1`, `:3000`, `:8000`, `:8080`, `:9000`,
    `:9001`, `:8025`, `:1025` and the old realm path. Service-DNS addresses and container-side
    port mappings are allowed; in-container health probes are listed, and a test fails if a
    listed exception goes stale. A third test checks that the server example holds only
    placeholders and has the dev switches off.
  - `test_keycloak_realm.py`:
    - render: server defaults, fail-closed, bad booleans, dev clients, SMTP auth
    - sync against a recording fake: drift repaired, then a second run changes nothing and makes
      only GETs; no DELETE; no secret or attack-detection path; no `secret` in a client PUT;
      the SMTP password is kept
    - sync run twice against the real Keycloak: no changes, realm representation unchanged
  - `test_db_roles.py`: Keycloak's database is owned by its role and closed to `skillify_app`,
    idempotent. `test_seed.py`: the seed does nothing without `SEED_DEV_USERS`.
- **Folded in from the step 6 review:** the seed-only "mark videos watched" helper moved to
  `app/cli/demo_progress.py`. It raises `SeedOnlyError` unless it runs inside the demo seed's
  entry point (`seed_context()`) or with `ENVIRONMENT=local`. The enrollments service keeps only
  `complete_watched_video`, the normal completion rule for stored watch progress.
  `tests/test_cli_boundaries.py` fails if anything under `app/api` or `app/modules` (apart from
  tests) imports `app.cli`.
- **Found while testing locally:** when Keycloak loses demo accounts (its local dev store was
  recreated), `make seed-demo` failed on the existing user rows, and its recreated accounts had
  no password. The seed now re-links users by email to their new Keycloak id and sets the
  file's password on any account it had to create. A test deletes an account and checks the
  file's password signs in after a rerun.
- **Fixed after the first CI run (37062261105, red):**
  - `next build` failed in the Docker and E2E jobs. Client components are prerendered without
    the runtime environment, and the shared API client now required `API_INTERNAL_URL` when
    imported. It now fails the first server-side request instead, with the same clear error. A
    Vitest covers it.
  - The demo smoke spec looked for "Python Foundations" on the first page of the course lists.
    Courses that other specs grant to Demo College can push it further down, so it now loads
    more pages until the course shows.
- `check-yaml` (pre-commit) runs with `--unsafe` (syntax only) for Compose's `!reset` and
  `!override` tags; CI's `docker compose config` validates the file itself.

**Deviations in step 7:**
1. **`keycloak-sync` is Python (standard library, `infra/keycloak/realm.py`), not `kcadm`.** One
   script renders and syncs from the same template, and a recording fake can prove what it
   never calls.
2. **Keycloak `start`, not `start --optimized`.** Optimized mode needs a custom pre-built image;
   `start` builds at boot, which is slower but needs no image of our own.
3. **Containers keep service-DNS internal URLs** (`http://keycloak:<port>`, `http://minio:9000`,
   `http://web:3000`) instead of reading `KEYCLOAK_INTERNAL_URL` and `S3_ENDPOINT_URL` from
   `.env`. Those `.env` values are for processes on the host. Service names resolve the same on
   any machine, and the hosts test allows only those.
4. **No `API_PUBLIC_URL`.** Nothing needs it: the browser reaches the API only through the web
   app's `/backend`, and the dev server uses the local video provider. Bunny's webhook would need
   a public API route first.
5. **Local brute force stays off** (`KEYCLOAK_BRUTE_FORCE=false` in `.env.example`): parallel test
   logins as shared dev users would lock them. The renderer defaults to on, and the server
   override forces it.
6. **Local Keycloak keeps `start-dev` and its built-in store;** only servers use Postgres and the
   `keycloak` role. Recreating the local Keycloak container therefore re-imports the realm, and
   `make seed` recreates the dev users.
7. **`KEYCLOAK_PORT` left the API and web settings.** Only compose uses it (the container port);
   the apps read `KEYCLOAK_PUBLIC_URL`.
8. **The hosts sweep skips local tooling:** the Makefile, `.env.example`, Playwright config and
   e2e helpers. Local addresses belong there.
9. **Read-only config mounts remain on a server** (the realm template, the Caddyfile, the Google
   script, `.secrets`), because the server runs from a checkout of the release tag. Only
   application-source mounts are removed, and CI checks that.
10. **Images are built on the VM until CI pushes them** (decision 10: no pushes in this phase);
    the runbook says so.

### Step 8: close-out (2026-10-04)

- Root `AGENTS.md` records the binding decisions and verification workflow in its
  own commit. `.claude/` is now ignored alongside `.secrets/`.
- **Inherited CI failure:** run
  [37066623603](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37066623603)
  failed the mobile and desktop `e2e/catalog.spec.ts` visibility assertion.
  Reproduced locally with an unchanged production build and the unchanged spec:
  a build without `API_INTERNAL_URL` prerenders an empty catalog before any
  tagged fetch, so its route has no `catalog` data-tag dependency. Publish-time
  tag revalidation alone cannot invalidate that fallback page. The authenticated
  revalidation handler now also invalidates `/catalog` and `/catalog/[slug]`.
  The visibility assertion, timeout and setup data are unchanged.
- **Local login regression:** the unchanged platform role test exposed background
  page prefetches following proxy redirects into `/auth/login` after cookies were
  cleared. The trace showed multiple concurrent login flows overwriting the PKCE
  state cookie, so the real callback failed. The proxy now uses the bundled
  Next.js documentation's prefetch matcher exclusions; actual page navigation
  remains protected and API authorization is unchanged. Matcher regression
  tests cover all four areas and both prefetch headers. No E2E assertions changed.
- **Full-suite repairs:** the approved demo helper waits for its initial course
  rows before checking pagination. A subsequent run exposed the shared API
  session's commit-after-response race; function scope now commits before success
  headers and permits failed commits to use the existing error middleware. Two
  real-Postgres regressions verify separate-connection visibility and commit-failure
  rollback, including synchronous/asynchronous after-commit hook behavior.
- **Deviation:** the cache, login, paging and shared transaction repairs and the
  handover instructions were added during close-out to complete the existing
  checks and release workflow. Commit SHAs are listed under Post-handover repairs.
  No feature scope was added.

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
