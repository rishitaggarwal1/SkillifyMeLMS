# Access control

How SkillifyMe decides who can do what. Three layers, each covered by tests:

1. **Authentication:** Keycloak (OIDC). The web app's backend-for-frontend (BFF) holds the tokens;
   the API validates the JWT on every request.
2. **Authorization in the service layer:** `require_permission` / `require_role` give clear 403s.
3. **PostgreSQL Row-Level Security:** the backstop. Even a missing check in code cannot leak or
   change another organization's rows.

## Roles

| Role | Scope | Source |
|---|---|---|
| `platform_admin` | Platform-wide (SkillifyMe operators) | Keycloak realm role, carried in the access token |
| `org_admin` | One organization | `memberships` table |
| `instructor` | One organization | `memberships` table |
| `lab_author` | One organization | `memberships` table |
| `student` | One organization | `memberships` table |

- Roles are **per organization**: someone can be an instructor in SkillifyMe and a student in Demo
  College. A person can hold several roles in the same org.
- Each request acts in one **active organization**, chosen with the org switcher. The web app sends
  it as `X-Organization-Id`.
  - The API accepts it only if the user is a member of that org, or is a platform admin and the org
    exists.
  - A user with exactly one membership gets that org by default.
- Archived organizations grant nothing.

## Permissions

Defined once, in `apps/api/app/modules/identity/authz.py` (`ROLE_PERMISSIONS`).

| Permission | org_admin | instructor | lab_author | student | Meaning |
|---|:-:|:-:|:-:|:-:|---|
| `org.read` | ✓ | ✓ | ✓ | ✓ | Read the active organization |
| `batch.read` | ✓ | ✓ | | | List and view batches and their members |
| `batch.manage` | ✓ | | | | Create, update and archive batches; add and remove batch members |
| `member.read` | ✓ | ✓ | | | Search and view members |
| `member.manage` | ✓ | | | | Change roles; remove people from the organization |
| `member.invite` | ✓ | | | | Invite, resend, revoke |
| `member.import` | ✓ | | | | CSV imports |
| `audit.read` | ✓ | | | | Read the audit log |
| `lab.author` | | | ✓ | | Author coding labs (used from Phase 4) |
| `course.read` | ✓ | ✓ | | | See courses the org owns or was assigned, and their assignments |
| `course.edit` | ✓ | ✓ | | | Author the org's own courses: drafts, publishing |
| `course.assign` | ✓ | ✓ | | | Assign the org's own courses (publisher-made assignments) |
| `course.distribute` | ✓ | | | | Narrow an org grant to the org's own batches |
| `enrollment.upgrade` | ✓ | | | | Opt the org's enrollments into a newer major version |
| `skill.manage` | ✓ | ✓ | ✓ | | Edit the skills taxonomy; the active org must also be a content publisher |
| `assignment.grade` | ✓ | ✓ | | | Grade assignment submissions of the org's own students (Phase 2.5) |
| `org.manage` | — | — | — | — | Platform only: create, update and archive organizations |

`platform_admin` has every permission, in any organization.

## Endpoints

| Endpoint | Who |
|---|---|
| `GET /api/v1/me` | Any signed-in user |
| `POST /organizations`, `GET /organizations`, `GET/PATCH/DELETE /organizations/{id}` | Platform admin |
| `GET /organizations/current` | Any member of the active org |
| `GET /batches`, `GET /batches/{id}`, `GET /batches/{id}/members` | org_admin, instructor |
| `POST /batches`, `PATCH/DELETE /batches/{id}`, `POST /batches/{id}/members`, `DELETE /batches/{id}/members/{user_id}` | org_admin |
| `GET /members`, `GET /members/{user_id}` | org_admin, instructor |
| `PATCH /members/{user_id}`, `DELETE /members/{user_id}` | org_admin |
| `POST /invitations`, `GET /invitations`, `DELETE /invitations/{id}`, `POST /invitations/{id}/resend` | org_admin |
| `POST /imports`, `GET /imports`, `GET /imports/{id}`, `GET /imports/{id}/errors.csv` | org_admin |
| `GET /audit-log` | org_admin (platform admins with no active org see all orgs) |
| `GET /skills` | Any signed-in user |
| `POST /skills`, `PATCH /skills/{id}` | Staff of a content-publisher org; platform admin |
| `POST /courses`, `GET /courses` | org_admin, instructor (the list covers owned courses and published courses assigned to the org) |
| `GET /courses/{id}`, `GET /courses/{id}/versions`, `GET /courses/{id}/versions/{version_id}`, `GET /courses/{id}/assignments` | org_admin, instructor of the owner org, or of an org the course is assigned to (published courses only) |
| `PATCH/DELETE /courses/{id}`, `GET /courses/{id}/draft`, module and lesson routes, `PUT .../order`, `PUT /courses/{id}/lessons/{lesson_id}/skills`, `GET /courses/{id}/publish-preview`, `POST /courses/{id}/versions` | org_admin, instructor of the **owner** org (others get 404) |
| `POST /courses/{id}/assignments` | Owner org: org_admin, instructor (`course.assign`). Receiving org: org_admin only (`course.distribute`), narrowing its grant to its own batches |
| `DELETE /course-assignments/{id}` | The org that created the row: owner-org editors for publisher-made rows, the receiving org_admin for rows their org created |
| `POST /courses/{id}/enrollment-upgrades` | org_admin of the enrollments' org |
| `GET /enrollments` | Any signed-in user (their own enrollments) |
| `GET /enrollments/{id}`, `POST /enrollments/{id}/lessons/{lesson_id}/visit`, `.../complete` | The enrolled student only (everyone else gets 404) |
| `POST /videos`, `GET /videos`, `GET /videos/{id}`, `POST /videos/{id}/uploaded`, `GET /videos/{id}/playback` | Owner-org course editors (`course.edit`); item routes return 404 across orgs |
| `GET /enrollments/{id}/lessons/{lesson_id}/playback`, `.../resume`, `POST /progress/heartbeat` | The active enrollment's student, with a current batch assignment; video must be in their pinned major's latest minor |
| `POST /webhooks/video/bunny/{secret}` | Provider webhook secret (constant-time check); status is fetched from Bunny, never trusted from the body |
| `POST /files`, `GET /files`, `GET /files/{id}`, `POST /files/{id}/confirm`, `GET /files/{id}/download` | Owner-org course editors (`course.edit`); item routes return 404 across orgs |
| `GET /courses/{id}/lessons/{lesson_id}/preview` | org_admin, instructor of the **owner** org (draft notes preview; others get 404) |
| `GET /organizations/directory` | Platform admins, and org_admin or instructor of an active content-publisher org (id and name of active orgs only; enforced by `app.organization_directory`, migration 0009). Cursor-paginated by name, with `q=` (name substring) and `ids=`. Everyone else gets `403 content_publisher_staff_required`. **Caveat:** see below |
| `GET /catalog`, `GET /catalog/{slug}` | Everyone, signed in or not (public catalog fields only; no ids or organization) |
| `GET /courses/{id}/versions/{version_id}/lessons/{lesson_id}/playback`, `.../pdf`, `.../images` | Course readers' staff: org_admin, instructor of the owner org or of an org the course is assigned to (any published version). Students get 403 (they use their enrollment routes) |
| `POST /enrollments/{id}/lessons/{lesson_id}/pdf-access`, `GET .../images` | The active enrollment's student, with a current batch assignment; the file must be used by that lesson in their pinned major's latest minor |

| `GET /organizations?q=` | Platform admin (name contains `q`) |
| `GET /platform/summary` | Platform admin. Counts: organizations, users (by status and by role), courses, enrollments, and students active today (a lesson opened since midnight IST; `users.last_login_at` is deliberately not used) |
| `GET /platform/users`, `GET /platform/users/{id}` | Platform admin. Users across every organization (`q`, `role`, `organization_id`, `status`), with every membership; the detail adds batches |
| `POST /platform/users/{id}/disable`, `.../enable` | Platform admin, never on themselves (`409 cannot_disable_self`). Sets `users.status` (the API refuses disabled accounts with `403 account_disabled`) **and** Keycloak's `enabled` flag; disabling also ends the user's Keycloak sessions. If Keycloak can't be reached the change is rolled back (`503 identity_provider_unavailable`). Audited as a platform-level entry (`organization_id` null) |
| `POST /platform/organizations/{id}/admins` | Platform admin. Invites an `org_admin` into that (active) organization, exactly like an org admin's invitation, recorded in that organization's audit log |
| `GET /platform/courses` | Platform admin. Every organization's courses, read-only: owner, status, current version, org-grant and batch-assignment counts |
| `GET /platform/audit-log` | Platform admin. Every organization's entries plus platform-level ones, whatever organization is active; filters `organization_id`, `action`, `actor_user_id`, `target_type`, `target_id`, `since`/`until` (time-zone-aware) |
| `GET`, `PUT /courses/{id}/lessons/{lesson_id}/assignment` | Owner-org editors (an assignment lesson's definition; others get 404). `PUT` takes the course revision as `If-Match` (428 when missing, 409 when stale), like every outline edit |
| `GET /enrollments/{id}/lessons/{lesson_id}/assignment`, `POST .../submission-upload`, `PUT .../submission` | The enrolled student only (everyone else gets 404). `PUT` takes the submission revision as `If-Match` (`0` for the first; 428 when missing, 409 when stale); a graded submission can't be replaced (`409 already_graded`). Uploads are `submission` files: PDF, PNG or JPEG, checked by signature, up to `SUBMISSION_UPLOAD_MAX_BYTES` |
| `GET /courses/{id}/progress?batch_id=`, `GET /courses/{id}/progress.csv?batch_id=`, `GET /batches/{id}/courses` | org_admin, instructor (`course.read`) of the active org, for **their own org's batches** that have a batch assignment for the course (others get 404; the course owner org can't read another org's students). Read-only; CSV cells are neutralized against formula injection. Stopgap until Phase 5 (ClickHouse) |
| `GET /courses/{id}/lessons/{lesson_id}/submissions`, `GET /assignment-submissions/{id}`, `PUT /assignment-submissions/{id}/grade` | Graders (`assignment.grade`: instructor, org_admin) of the **students' org**. The course owner org sees none of another org's submissions; other orgs get 404. Grading takes the submission revision as `If-Match`, is audited (`assignment.graded`), refuses grading your own work, and completes the lesson in the same transaction |

All paths are under `/api/v1`, and platform admins can call every endpoint.

**Platform-admin endpoints (Phase 2.5)** are separate `/platform/*` routes gated by
`require_platform_admin`. They read across organizations through the `PLATFORM_ADMIN` branches the
RLS policies already had; no org-level policy was widened for them, and there is no
impersonation.

**Status codes:**
- **401:** no token or an invalid one (`WWW-Authenticate: Bearer`).
- **403:** missing permission in your own org, or an org you don't belong to.
- **404:** a resource belonging to another org. We don't reveal that it exists.
- **400 `organization_required`:** the endpoint needs an active org and none was selected.

`apps/api/tests/test_endpoint_roles.py` calls **every** endpoint as seven callers:
- anonymous
- student, lab_author, instructor and org_admin, all in the same org
- an unrelated org's admin
- a platform admin

It asserts the expected outcome for each. A meta-test fails if an endpoint is missing from the
matrix.

## Row-Level Security

Every table has RLS enabled, with **separate policies per operation** (`FOR SELECT` / `INSERT` /
`UPDATE` / `DELETE`; never `FOR ALL`). The API connects as `skillify_app`, which owns no tables and
has no `BYPASSRLS`, so RLS always applies. Each request sets these per-transaction settings:

- `app.current_org` (the active org)
- `app.current_user`
- `app.platform_admin` (from the verified token)

**Helper functions** in the `app` schema are the only way policies read identity data (the
module-boundary rule, applied to SQL). They're `SECURITY DEFINER` with a fixed `search_path`, and
only the app role can execute them:

| Function | Purpose |
|---|---|
| `app.current_org_id()`, `app.current_user_id()` | The per-request settings |
| `app.current_user_is_platform_admin()` | The verified platform flag |
| `app.current_user_has_role(org, roles[])` | Role check in a specific org (archived orgs grant nothing) |
| `app.current_user_is_member(org)` | Any role in the org |
| `app.current_user_in_batch(batch)` | Batch membership (used for student visibility in Phase 2) |
| `app.org_is_content_publisher(org)` | Whether the org may share content with other orgs (Phase 2) |
| `app.user_is_member_of(user, org)`, `app.user_visible_to_current_user(user)` | Roster visibility |
| `app.provision_user(...)`, `app.ensure_users(...)` | Sign-in provisioning; bulk find-or-create for invitations and imports (the caller must be an org admin) |

**Who can see and change what.** "Staff" means org_admin, instructor or lab_author in the active
org.

| Table | Read | Write |
|---|---|---|
| organizations | Members of the org; platform admins | Platform admins |
| users | Yourself; staff see members of the active org | Yourself (name only; email and Keycloak id change only through provisioning); platform admins |
| memberships | Your own memberships in every org; staff see the active org's | org_admin of the active org |
| batches | Staff: all of the active org's; students: only batches they're in | org_admin |
| batch_members | Staff; students see only their own rows | org_admin, and only for members of that org (the batch must belong to it: composite FK) |
| invitations, import_jobs, import_job_errors | org_admin | org_admin |
| audit_log | org_admin of the active org; platform admins | Any member, only as themselves; **no UPDATE or DELETE** (append-only) |
| outbox_events                                                                | Active org; sensitive learning events additionally require the owning student or the student's org's instructor/org_admin (platform override). Covers historical assignment_graded v1 rows           | Insert for the active org; legitimate student INSERT/RETURNING remains allowed; only the relay role (`skillify_relay`) can mark events published                                                                                                                                     |
| skills | Everyone (global taxonomy) | Platform admins, and org_admin / instructor / lab_author of a **content-publisher** org |
| courses | Owner-org editors*; readers through an assignment** | Owner-org editors* |
| course_modules, lessons, lesson_skills (the draft) | Owner-org editors* only | Owner-org editors* |
| course_versions, course_version_lessons | Owner-org editors*; readers** | Owner-org editors* (publish). Immutable: no UPDATE or DELETE |
| course_assignments | Owner-org editors*; the receiving org's org_admins and instructors | Publisher-made rows: owner-org editors* (to other orgs only if the owner is a content publisher). Narrowing rows: the receiving org's org_admin, batch rows under an existing org grant only. Removal: only the org that created the row. No UPDATE |
| enrollments, lesson_progress (the student's org) | The student; org_admin and instructors of that org | The student (own progress); org_admin; system jobs; **instructors only where a graded submission exists** (`app.enrollment_graded`, `app.lesson_graded`, migration 0011). A trigger (`app.enrollments_grader_guard`) lets such a grader change only an enrollment's progress columns |
| video_assets | Owner-org editors*; org_admin and instructors of a reader org** for assets in any published version; enrolled students for assets in their pinned major's latest minor, with current batch access | Owner-org editors* |
| files | Same as video_assets, for the files published lessons use (a pdf lesson's PDF, a notes lesson's images). `submission` files: the student who uploaded them, and the org's instructors and org_admins | Owner-org editors*; a student writes only their own `submission` files |
| assignments (draft definitions, owner org) | Owner-org editors* only; students and graders read the published copy in the version | Owner-org editors* |
| assignment_submissions, assignment_grades (the student's org) | The student (own); the org's instructors and org_admins; platform admins | Submissions: the student (own, only while `submitted`, only into their own active enrollment); graders mark them graded. Grades: graders only. No DELETE except platform admins |
| catalog_entries | Everyone | Owner-org editors* |
| question_banks, questions, question_skills, question_keys (draft, owner org) | Owner-org instructors/org_admins and platform admins only; students never read draft keys or explanations                                                                                            | Same editors; skill links are replace-by-delete/insert                                                                                                                                                                                                                               |
| quizzes (draft, owner org)                                                   | Owner-org editors only                                                                                                                                                                               | Same editors                                                                                                                                                                                                                                                                         |
| quiz_versions (published, owner org)                                         | Owner editors, assigned-org reader staff, or an active enrolled student with the matching major and current batch access                                                                             | Owner editors INSERT; no app-role UPDATE/DELETE                                                                                                                                                                                                                                      |
| quiz_version_questions (published public prompts)                            | Reader staff; students only the questions selected for an accessible own attempt                                                                                                                     | Owner editors INSERT; no app-role UPDATE/DELETE                                                                                                                                                                                                                                      |
| quiz_version_keys (published private keys/explanations)                      | Owner-org editors only, including after submission; controlled solutions require the narrow reveal function                                                                                          | Owner editors INSERT; no app-role UPDATE/DELETE                                                                                                                                                                                                                                      |
| quiz_attempts, quiz_answers (student org)                                    | Owning active student with current batch access; student's org instructor/org_admin; platform admins. Publisher staff cannot read another org's work                                                 | Direct app-role INSERT/UPDATE/DELETE revoked; guarded runtime mutation functions ship in step 3                                                                                                                                                                                      |

\* **Editors**: `instructor` or `org_admin` of the course's owner org, acting in that org, or platform
admins.

\** **Readers** of a published course, where the course is assigned to the reader's active org:
- that org's `org_admin` and `instructor`, through any assignment (org grant or batch assignment)
- students, **only** through a batch assignment for a batch they belong to. This applies in the
  owner org too.

Helper functions for courses: `app.course_readable(course)` (the reader rule above) and
`app.is_org_grant(grant, course, org)` (used by the narrowing policy). Both are `SECURITY DEFINER`,
so policies on `courses` and `course_assignments` don't recurse into each other.

**Assessment SQL interfaces (migration 0012, Phase 3 step 1):**

- Enrollment ownership interface `app.learning_event_owned(enrollment, org)` checks
  the enrollment's actual org/user plus active membership, rather than trusting an
  outbox payload's user_id. The outbox app-role SELECT policy restricts all enrollment
  and video-progress aggregates and named learning events, independent of event version.
  Staff require instructor/org_admin in the student's org. The relay's separate policies
  and grants remain intact; other nonlearning events retain their previous org scope.
- `app.quiz_version_readable(version)` checks the course-version/lesson identity and
  permits reader staff or the student's active enrollment, pinned major and current
  batch grant. `app.quiz_attempt_readable(attempt)` also verifies enrollment/student/org,
  course/lesson/version identity. `app.quiz_question_readable(question)` restricts
  students to the stored selected question manifest, avoiding disclosure of a bank's pool.
- `app.quiz_reveal_allowed(attempt)` and `app.quiz_attempt_solutions(attempt)` require
  the owning currently authorized student and a submitted attempt. score_only returns
  no keys; correct_answers returns no explanations; explanations permits both. With
  after_attempts_exhausted, the same enrollment/lesson/major must have used its full
  allowance and have no active attempt. Defaults are score_only / immediately.
  Both functions read immutable per-attempt publication rules. Response models
  independently enforce the same mode/timing rules and reject nested secret fields.

Every helper has fixed `search_path = pg_catalog, public`, SECURITY DEFINER,
PUBLIC execution revoked and execution granted only to `skillify_app`. These are
documented cross-module policy interfaces; application modules still communicate only
through service interfaces. No HTTP endpoints are introduced by the foundation step;
the existing endpoint MATRIX remains complete.

`app.video_readable(asset)` additionally checks the current student's active enrollment, pinned
major, latest minor and current assignment. Playback services first authorize the enrollment and
lesson. Signed URLs remain usable until their short expiry; revocation prevents issuing new URLs.
`app.file_readable(file)` (migration 0007) applies the same checks to files, through
`course_version_lessons.file_ids`.

**Read access includes the content (decided 2026-09-30, migration 0008).** Staff (`org_admin`,
`instructor`) of an org that reads a course can play its videos and open its PDFs and notes
images in any published version, just as they read the outline and versions. Both functions
therefore also allow a caller who holds one of those roles in the active org when
`app.course_readable` holds for the course. Editing stays owner-org only. Students still need an
enrollment through an assigned batch; unassigned orgs see nothing.

**Organization directory caveat (accepted 2026-09-30).** Staff of **every** content-publisher
org can list the names of **all** active organizations. That is acceptable while SkillifyMe is the
only publisher. **Revisit it before any other organization gets `is_content_publisher`.**
Options then:
- limit each publisher to orgs it already has a relationship with
- make the directory opt-in per organization
- keep cross-org discovery for platform admins only

**Assignment rules (confirmed 2026-09-26):**
1. Assignments the publisher made, including publisher-made batch assignments, can be removed only
   by the publisher. A receiving org's `org_admin` can remove only the assignments their own org
   created.
2. Instructors of an assigned org can read the course but cannot distribute it to batches. Only the
   receiving org's `org_admin` distributes.
3. Opting into a new major version applies to a whole organization or to chosen batches, never to
   individual students.

The content rules are enforced by `app/modules/courses/tests/test_course_rls.py`,
`app/modules/skills/tests/` and `app/modules/enrollments/tests/`. The identity rules are enforced by
`app/modules/identity/tests/test_rls_*.py`:
- an org A user cannot read, change or insert org B data, tested through raw SQL **and** through
  direct repository calls
- a forged org context grants nothing
- per-role visibility
- helper-function hardening

`tests/test_schema_guards.py` checks that every table has RLS, that no policy is `FOR ALL`, and that
every foreign key is indexed.

## Sessions in the browser

- **Login:** `/auth/login` → Keycloak (authorization code + PKCE, state, nonce) → `/auth/callback`.
  The code is exchanged server-side by the Next.js BFF, using a confidential client.
- **Token storage:** tokens are stored only in `__Host-` cookies: httpOnly, Secure, SameSite=Lax,
  and additionally AES-GCM encrypted. There are **no tokens in JavaScript or `localStorage`**.
- **`/backend/*` proxy:**
  - attaches `Authorization: Bearer` (refreshing near expiry) and `X-Organization-Id`
  - strips browser cookies
  - forwards only `/api/v1/*` and `/health/*`
- **CSRF:** state-changing requests must carry our own `Origin` (and `Sec-Fetch-Site: same-origin`
  when present), on top of SameSite=Lax.
- **Logout** is POST-only. It clears the cookies and ends the Keycloak SSO session.
- **Page prefetches** bypass the login-redirect proxy (Next.js's
  `next-router-prefetch` or `purpose: prefetch` matcher exclusions). Page shells contain no
  private data and API authorization still applies. This prevents background prefetches
  from starting extra OIDC flows and overwriting the current login's state cookie; actual
  page navigation remains protected.

## Rate limits

All use Redis sliding windows and return `429` with `Retry-After`.

| What | Key | Default |
|---|---|---|
| Failed authentication / probing orgs you don't belong to (API) | Client IP | 30 / minute |
| Invitations, including resends | Acting user | 60 / hour |
| CSV imports | Acting user | 10 / hour |
| `/auth/login`, `/auth/callback` (web BFF) | Client IP (`X-Forwarded-For`) | 20 / minute |

The local dev default for the web BFF limit is higher: 300. There's no edge proxy locally to tell
clients apart.

Keycloak's brute-force detection comes from `KEYCLOAK_BRUTE_FORCE` in `.env`, rendered into the
realm (`infra/keycloak/realm.template.json`) and re-applied to an existing realm by `keycloak-sync`:

- **Local and CI:** off (`.env.example`), because parallel automated logins as the shared dev users
  trip it.
- **Servers:** `docker-compose.prod.yml` forces it on, whatever `.env` says: 10 failures lock an
  account temporarily, waiting 60 s more per further failure up to 15 minutes; the failure count
  resets after 12 hours. `keycloak-sync` changes these settings only; it never resets an
  account's lockout state.
- The password-grant client `skillifyme-test` exists only with `KEYCLOAK_DEV_CLIENTS=true`, which
  the server override forces off.

## Adding an endpoint or a table (checklist)

- [ ] **Service function:** call `require_permission`, or `require_org_permission` for org-scoped
  actions, before doing anything. Add a permission to `ROLE_PERMISSIONS` if needed, and update the
  tables above.
- [ ] **Router:** use `CurrentPrincipal` / `TenantSession`, so the RLS context is set.
- [ ] **Tests:** add a row to `MATRIX` in `tests/test_endpoint_roles.py`. The meta-test fails until
  you do.
  Routes that belong to one user (like a student's enrollment) set `denied="404"`.
- [ ] **New tenant-owned table:** add `organization_id` (indexed), enable RLS, and write one policy
  per operation using the `app.*` helpers. The schema guards fail otherwise.
- [ ] **Admin action:** record it with `audit.record()` in the same transaction.
- [ ] **Changing who can see a batch or course:** emit an outbox event, and document it in
  `docs/events.md`.
