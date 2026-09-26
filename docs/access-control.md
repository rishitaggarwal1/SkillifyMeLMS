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
| `lab.author` | | | ✓ | | Author coding labs (used from Phase 3) |
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

All paths are under `/api/v1`, and platform admins can call every endpoint.

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
| outbox_events | The active org's events | Insert for the active org; only the relay role (`skillify_relay`) can mark events published |

These are enforced by `app/modules/identity/tests/test_rls_*.py`:
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

Keycloak's brute-force detection is **off in the dev realm**, because parallel automated logins
trip it. Production realms must enable it.

## Adding an endpoint or a table (checklist)

- [ ] **Service function:** call `require_permission`, or `require_org_permission` for org-scoped
  actions, before doing anything. Add a permission to `ROLE_PERMISSIONS` if needed, and update the
  tables above.
- [ ] **Router:** use `CurrentPrincipal` / `TenantSession`, so the RLS context is set.
- [ ] **Tests:** add a row to `MATRIX` in `tests/test_endpoint_roles.py`. The meta-test fails until
  you do.
- [ ] **New tenant-owned table:** add `organization_id` (indexed), enable RLS, and write one policy
  per operation using the `app.*` helpers. The schema guards fail otherwise.
- [ ] **Admin action:** record it with `audit.record()` in the same transaction.
- [ ] **Changing who can see a batch or course:** emit an outbox event, and document it in
  `docs/events.md`.
