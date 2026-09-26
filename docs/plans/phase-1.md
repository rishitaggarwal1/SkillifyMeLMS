# Phase 1 — Identity, organizations and access control (plan)

**Status:** approved 2026-09-26; in progress. Built in 5 steps. After each step: lint, type-check,
all tests, fix, commit and push to `main`, summarize, and wait for "continue".

This phase also delivers everything in the "Prerequisites from Phase 1" section of
[`phase-2.md`](phase-2.md). Where the two differ, this plan wins:

- Login is a Next.js backend-for-frontend (BFF) that keeps the tokens in cookies. There are no
  API-side sessions.
- The platform role is named `platform_admin`, not `super_admin`.

## Design

### How login and requests work

- **Login** runs in Next.js routes using `openid-client`: authorization code flow with PKCE, state
  and nonce.
  - `/auth/login` starts it.
  - `/auth/callback` exchanges the code for tokens.
  - `/auth/logout` clears the cookies and ends the Keycloak session.
- **Tokens** (access, refresh, ID) are stored only in httpOnly, Secure, SameSite=Lax cookies.
  - Each cookie is also encrypted with AES-GCM using `SESSION_SECRET`.
  - Nothing is stored in `localStorage`.
- **The `/backend` proxy:**
  - forwards the access token as `Authorization: Bearer`
  - refreshes it when it's within 30 seconds of expiring
  - forwards `X-Organization-Id` from the `sm_org` cookie, and the client IP
  - rejects state-changing requests (POST, PATCH, DELETE) whose `Origin` isn't our own site (CSRF
    protection)
- **Keycloak URLs:**
  - Keycloak listens on `KEYCLOAK_PORT` (default 8080) inside and outside Docker. The browser
    sees the issuer at `localhost:<port>`, pinned by `KC_HOSTNAME`; containers reach Keycloak at
    `keycloak:<port>`.
  - The BFF and the API therefore get both URLs, configured explicitly rather than by discovery.
- **JWT validation in the API:**
  - The signature is checked against Keycloak's public keys (JWKS), cached with a TTL.
  - An unknown key ID triggers one refetch, with a cooldown, so key rotation works.
  - `iss`, `aud` (`skillifyme-api`), `exp`, `nbf` and `azp` are checked.
  - `alg=none` and symmetric (HS*) algorithms are rejected.
- **Principal (current user and org):**
  1. `app.provision_user(...)` (`SECURITY DEFINER`) creates the user on first login, and updates
     their profile when it changed.
  2. The API loads the user's memberships.
  3. It picks the active org from `X-Organization-Id`, which must be one of the user's memberships
     unless they're a platform admin.
  4. It calls `set_tenant_context(org, user, platform_admin)` so RLS applies.

  The resolved principal is cached in Redis for 60 seconds, and cleared when the user's memberships
  change.
- **`platform_admin`** is a Keycloak realm role carried in the token. It reaches Postgres as a third
  per-transaction setting, `app.platform_admin`.
- **Google** is an identity provider in the dev realm. Its credentials come from env placeholders.
  A first-broker-login flow automatically links a Google login to an existing account with the same
  email.

### Data model (`identity` and `audit` modules)

| Table | Key points |
|---|---|
| `organizations` | `name`, unique `slug`, `is_content_publisher`, `status` (active or archived) |
| `users` | unique `keycloak_sub`, unique `lower(email)`, `full_name`, `status` (invited, active, disabled), `last_login_at`. Global, since one user can be in many orgs. |
| `memberships` | unique `(user_id, organization_id, role)`. Several roles per user per org are allowed. |
| `batches` | `organization_id`, `name` (unique per org, case-insensitive), `status`, `UNIQUE (id, organization_id)` |
| `batch_members` | unique `(batch_id, user_id)`; `organization_id`, with a composite foreign key to `batches(id, organization_id)` |
| `invitations` | org, email, roles, `batch_ids`, status, `expires_at` |
| `import_jobs`, `import_job_errors` | status and progress counts for each CSV import; per-row errors |
| `audit_log` | actor, action, target, `before`/`after`, IP, request ID, org, timestamp. Append-only: UPDATE and DELETE are revoked from the app role. |

- **Roles:** `platform_admin` (from Keycloak, platform-wide), plus per-org `org_admin`,
  `instructor`, `lab_author` and `student`.
- **Indexes:** every foreign key and filter column; `pg_trgm` for member search.

### RLS

Every table gets separate policies for SELECT, INSERT, UPDATE and DELETE.

- **Helper functions** (`SECURITY DEFINER`, `STABLE`, fixed `search_path`, executable only by the
  app role). These are identity's database-level interface for other modules' policies:
  - `app.current_user_is_platform_admin()`
  - `app.current_user_has_role(org, roles[])`
  - `app.current_user_is_member(org)`
  - `app.current_user_in_batch(batch)`
  - `app.org_is_content_publisher(org)`
  - `app.user_visible_to_current_user(user)`
  - `app.user_is_member_of(user, org)`
  - `app.provision_user(sub, email, full_name)`
- **Staff** means `org_admin`, `instructor` or `lab_author` in the current org.
- **Who sees and changes what:**
  - **Organizations:** members see their own orgs; platform admins manage all.
  - **Users:** you see yourself; staff see members of the current org; platform admins see all.
    Column grants mean the app role can only update `full_name`, `status` and `updated_at`.
  - **Memberships:** you see your own in every org (this powers the org switcher); staff see the
    current org's. Only the org_admin or platform admin can change them.
  - **Batches:** staff see all of the current org's batches; students see only their own. Only the
    org_admin can change them.
  - **Batch members:** staff see all; students see only their own rows. Only the org_admin can add
    or remove, and only users who are members of that org.
  - **Invitations, imports, import errors:** org_admin only.
  - **Audit log:** a current-org member may insert rows for that org, and only as themselves. The
    org_admin reads their org's rows; platform admins read all.
  - **Outbox:** the app role may insert and read events for its current org only. The relay uses a
    separate `skillify_relay` role.
- Policies call the helpers as `(SELECT app.fn(...))`, so each is evaluated once per query rather
  than once per row.

### Authorization in the service layer

- `require_role` and `require_permission`, with permissions defined in one table in code:
  - `org.manage` → platform_admin
  - `batch.manage`, `member.invite`, `member.import`, `audit.read` → org_admin
  - `member.read` → org_admin, instructor
  - `lab.author` → lab_author (later phases)
- Anything the user isn't allowed to see returns 404, not 403.
- The full matrix will be documented in `docs/access-control.md`.

### APIs (`/api/v1`, cursor pagination everywhere)

- **Current user:** `GET /me`.
- **Organizations** (platform admin): create, list, get, update, archive. Members can read
  `GET /organizations/current`.
- **Batches:**
  - create, list, update, archive
  - `GET/POST /batches/{id}/members` (bulk, up to 500) and `DELETE /batches/{id}/members/{user_id}`
  - adding or removing a batch member writes a `batch_member_added` / `batch_member_removed` outbox
    event
- **Members:** `GET /members?q=&role=&batch_id=`, `PATCH /members/{user_id}` (roles),
  `DELETE /members/{user_id}` (also removes their batch memberships, with events).
- **Invitations:** find or create the user in Keycloak (admin service account), then have Keycloak
  send its "set your password" email. List, revoke and resend endpoints.
- **CSV imports:**
  - `POST /imports` (multipart) stores the file in S3 and queues a Celery job.
  - The job validates rows, detects duplicates, and creates users in bulk through Keycloak's
    `partialImport`, then creates memberships and batch memberships.
  - `GET /imports/{id}` returns progress. `GET /imports/{id}/errors.csv` returns the error report,
    escaped against spreadsheet formula injection.
- **Audit log:** `GET /audit-log`. Every admin action writes an audit row in the same transaction.
- **Rate limiting:** a Redis sliding window (atomic Lua script), returning `429` with `Retry-After`.
  - API: invitations, imports, org switching.
  - BFF: login, callback, refresh.

### Platform pieces

- **Outbox → Kafka relay** (`outbox-relay` service, aiokafka): claims up to 500 events at a time
  with `FOR UPDATE SKIP LOCKED`, publishes them keyed by aggregate ID, then stamps `published_at`.
- **`docs/events.md`:** the event envelope and schemas.
- **Celery `beat`** service.
- **Keycloak realm:**
  - `skillifyme-web` becomes a confidential client (BFF, with PKCE)
  - `skillifyme-admin` service account for user management
  - the `platform_admin` realm role
  - test users for every role and org, including one multi-org user
- **Seed data:** `make seed`, which also runs automatically after migrations. Safe to run twice.
  - SkillifyMe (publisher)
  - Demo College, with CSE 2026 and ECE 2026
  - Other College
  - the test users' memberships

## Build steps

1. **Data model and RLS:** migration, models, repositories, helper functions, policies, audit table,
   relay and beat. Tests: isolation (raw SQL and repositories) and the helpers.
2. **Auth:** JWT validation, the principal, org selection, `require_role` and `require_permission`,
   realm changes, seed data, and Keycloak integration tests.
3. **APIs:** the endpoints above, rate limiting, events and `docs/events.md`. Tests: the role matrix,
   CSV import, rate limiting, audit.
4. **Frontend:** BFF login and cookies, the proxy, the org switcher, admin pages, CSV upload. Vitest.
5. **End-to-end:** Playwright (the org admin logs in, creates a batch, imports 50 students), CI on
   compose, `docs/access-control.md`, and updating the Phase 2 plan.

## Done when

- Tests prove a user in org A cannot read or modify org B's data, including through direct
  repository calls.
- Every endpoint has role-check tests, and every Phase 2 prerequisite has tests.
- CSV import tests cover valid, duplicate and malformed rows.
- A Playwright test: the org admin logs in, creates a batch, and imports 50 students.
- Everything is pushed to `main`.

## Implementation notes (deviations from the plan above)

- **Keycloak user creation (step 3):** invitations and imports create accounts with `POST /users`
  (8 in parallel) instead of `partialImport`. The bulk endpoint needs realm-admin rights; the
  per-user endpoint needs only `manage-users`, so the service account stays least-privileged.
- **`app.ensure_users()` (migration 0003):** org admins can't see users outside their org under
  RLS, so a `SECURITY DEFINER` function finds or creates users by Keycloak id for invites and
  imports. It checks that the caller is an org admin (or platform admin) itself.
- **Invitations are accepted automatically** on the user's first login, inside `provision_user`.
  Revoking or expiring a pending invitation withdraws the access it granted.
- **Dev Keycloak keeps no data.** Dev-realm user ids are pinned in the realm file, so they survive
  re-imports, and the seed reconciles ids by email.
- **Email validation** allows the `.local` and `.test` domains outside production only (dev realm
  and test data).
