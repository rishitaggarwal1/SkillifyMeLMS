# SkillifyMe Portal

Multi-tenant practical learning platform (LMS, coding labs, assessments, analytics) for colleges and
placement training.

- Architecture: [`docs/architecture.md`](docs/architecture.md)
- Permanent engineering decisions: [`CLAUDE.md`](CLAUDE.md)

## Repository layout

```
apps/api     FastAPI modular monolith (Python 3.13, uv)
apps/web     Next.js App Router frontend (TypeScript, pnpm)
services/    Future standalone services (code runner)
infra/       Local docker config now; Terraform later
docs/        Architecture and design docs
```

## Prerequisites

- Docker Desktop
- GNU make (Windows: `winget install ezwinports.make`)
- [uv](https://docs.astral.sh/uv/) (Windows: `winget install astral-sh.uv`). It installs Python 3.13 itself.
- Node 22+ and pnpm (Windows: `winget install pnpm.pnpm`)
- Windows only: Git for Windows. The Makefile runs its recipes with Git Bash.

## Quick start

```bash
make install   # host toolchains: API venv, web deps, Playwright chromium, pre-commit hooks
make dev       # builds and starts the whole stack; returns once everything is healthy
```

| Service           | URL                                   |
| ----------------- | ------------------------------------- |
| Web               | http://localhost:3000                 |
| API docs          | http://localhost:8000/docs            |
| API readiness     | http://localhost:8000/health/ready    |
| Keycloak          | `KEYCLOAK_PUBLIC_URL` (http://localhost:8080, realm `skillifyme`) |
| MinIO console     | http://localhost:9001                 |
| Redpanda console  | http://localhost:8082                 |
| Mailpit           | http://localhost:8025                 |

Credentials for all of these are in your local `.env`, which is created from `.env.example`.

### Signing in

Open http://localhost:3000 and click **Sign in**. The web app sends you to Keycloak's login page
and back. Org admins get an **Admin** link: batches, members and invitations, and CSV import.
Tokens never reach browser JavaScript; they're kept in encrypted httpOnly cookies by the Next.js
server.

### Dev users (local only)

`make dev` runs the `seed` job (`make seed` to run it again). With `SEED_DEV_USERS=true` (the
`.env.example` default) it creates three orgs and the users below, in Keycloak through its admin
API and in the database. All of them use `DEV_USER_PASSWORD` from `.env` (`Local-Dev-Only-1` in
`.env.example`). A server never sets `SEED_DEV_USERS`, so these accounts exist only locally and in
CI; the realm template itself contains no users.

| User                              | Organization and role                        |
| --------------------------------- | -------------------------------------------- |
| platform.admin@skillifyme.local   | platform admin (Keycloak realm role)         |
| content.admin@skillifyme.local    | SkillifyMe (content publisher): org_admin    |
| author@skillifyme.local           | SkillifyMe: instructor                       |
| lab.author@skillifyme.local       | SkillifyMe: lab_author                       |
| multi@skillifyme.local            | SkillifyMe and Demo College: instructor      |
| admin@demo-college.local          | Demo College: org_admin                      |
| instructor@demo-college.local     | Demo College: instructor                     |
| cse.student@demo-college.local    | Demo College: student, batch CSE 2026        |
| ece.student@demo-college.local    | Demo College: student, batch ECE 2026        |
| admin@other-college.local         | Other College: org_admin                     |
| instructor@other-college.local    | Other College: instructor                    |
| student@other-college.local       | Other College: student, batch MECH 2026      |

## Everyday commands

```bash
make help            # everything below, and more
make lint            # ruff + mypy --strict, eslint + prettier + tsc
make test            # pytest (real Postgres/Redis), vitest, playwright (needs `make dev` running)
make migrate         # alembic upgrade head
make migration m="add courses"
make gen-api         # regenerate apps/web/src/lib/api/schema.ts from the OpenAPI spec
make logs s=api      # follow one service's logs
make dev-web-host    # stack in Docker, web app on the host (see "Windows development")
make down            # stop (keeps data)
make clean           # stop and wipe local data volumes
```

## Demo

Start the stack, then create the demo data:

```bash
make dev
make seed-demo
```

Open the web app and sign in with one of the four roles below. The home page sends each
single-role user to their area.

| Role | Login | Landing and demo |
|---|---|---|
| Platform admin | `demo.platform-admin@skillifyme.co.in` | `/platform`: platform counts, organizations and users |
| College admin | `demo.admin@skillifyme.co.in` | `/admin`: batches; `/admin/courses` shows granted courses and distribution |
| Instructor | `demo.instructor@skillifyme.co.in` | `/teach`: assigned courses, submissions to grade and batch progress |
| Student | `demo.student@skillifyme.co.in` | `/learn`: Python Foundations, 85% complete; quiz failed then passed |

`demo.author@skillifyme.co.in` is the instructor in the SkillifyMe publisher org who owns and
edits the course. The other students are `demo.student2@skillifyme.co.in` through
`demo.student8@skillifyme.co.in`, all in Demo College's CSE 2026 batch.

The seed publishes **Python Foundations** (two modules, seven required lessons: two videos,
two notes lessons, a PDF, a FizzBuzz assignment and the Python essentials quiz), grants it to
Demo College and assigns it to CSE 2026. The quiz has all three question types, Python skill
tags, a ten-minute timer, two attempts and explanations revealed after attempts are exhausted.
Priya fails then passes, Aarav passes, Ananya fails, Rohan expires without answering and the
other students have not started. Students range from 0% to 100%, with three submissions and
one grade. Fresh assignments have a correctness/readability rubric (6 + 4 marks), a due date
frozen when authored, and a 10% penalty per started late day. Aarav's initial raw grade is
9/10, reduced to 8.10/10 for one late day. Reruns do not move the due date, alter accepted
penalties, replace existing attempts/grades or rotate passwords.

An existing Phase 2.5 six-lesson demo is left intact by a normal run, which reports the
required upgrade. To add the assessments, run:

```bash
make seed-demo args=--upgrade-course
make seed-demo
make seed-demo
```

The guarded upgrade publishes **2.0**, preserves the first six lesson IDs and existing work,
and opts the **whole CSE 2026 batch** into the new major through the college-admin service.
Other batches stay on their existing major. It refuses to overwrite an edited legacy
definition: reconcile that definition manually first. Existing submissions/grades retain
their historical rules; `--reset` creates the fresh rubric/late examples if a local operator
wants to restore the demo. No lab execution is fabricated; labs remain Phase 4.

Random passwords are written only to **`.secrets/demo-credentials.txt`**, which stays out of git;
they are never printed or logged. Compose mounts `./.secrets` at `/secrets` and writes
`/secrets/demo-credentials.txt`. When invoking the CLI directly, `DEMO_CREDENTIALS_FILE` can
override the path. File mode is 0600 inside Linux; Windows bind mounts retain their host NTFS
permissions.

```bash
make seed-demo args=--reset             # restore demo progress and submissions
make seed-demo args=--rotate-passwords  # issue new passwords for every demo login
```

All three flags (`--reset`, `--rotate-passwords`, `--upgrade-course`) refuse outside
`ENVIRONMENT=local` unless you explicitly add
`--i-know-this-is-not-local`. **Production always refuses the demo seed**, including with that
override. The read-only `apps/web/e2e/demo-smoke.spec.ts` checks the four demo logins and their
landings and seeded quiz history/results at 360px, without submitting or grading anything;
it reads the credentials file and
disables tracing.

## Windows development

The stack runs in Docker, and the web container runs `next dev` on the source you mount into it.
On Windows, file watching across that mount is unreliable: with the checkout on a Windows drive
(or under `/mnt/c` in WSL), edits often don't reach the container, even with polling enabled.
When that happens the web app keeps serving old code until you restart it.

**Recommended:** develop inside WSL2.

1. Enable Docker Desktop's **WSL 2 based engine** (Settings → General), and turn on integration
   for your distro (Settings → Resources → WSL integration).
2. Clone the repository **inside the WSL2 filesystem**, e.g. `~/src/LMS`, not under `/mnt/c/...`.
   Files there are native Linux files, so file events and I/O are fast.
3. Install the prerequisites inside WSL (make, uv, Node 22 + pnpm) and run every `make` command
   from the WSL shell. VS Code's *WSL* extension opens the folder directly.

**Fallback, without moving the checkout:** `make dev-web-host`.

- It starts everything in Docker except the web container, then runs the web app on your
  machine (`pnpm dev`), where file watching works.
- It uses the same settings from `.env` (`KEYCLOAK_PUBLIC_URL`, `KEYCLOAK_INTERNAL_URL`,
  `WEB_ORIGIN`), with the API on `API_PORT` and Redis on `REDIS_PORT`, and serves on `WEB_PORT`,
  so sign-in redirects are unchanged.
- The API's catalog revalidation calls (`http://web:3000/api/revalidate`) can't reach a host
  process and are retried, then dropped. This makes no difference in development: the catalog
  pages read the API fresh on every request there (`CATALOG_DATA_CACHE=off`, set by
  `make dev-web-host` and the compose web service).
- Stop it with Ctrl+C. `make dev` goes back to the containerised web app.

## Bunny Stream smoke test (manual)

Development and CI use the local video provider (MinIO). To check our Bunny integration against a
real library, put every `BUNNY_*` value in `.env` and run:

```bash
uv run --project apps/api python scripts/smoke_test_bunny.py
```

It refuses to start if any `BUNNY_*` setting is missing, prints the checks it will perform, creates
one test video (deleted at the end), and exits non-zero on failure. The webhook check needs
`make dev` running. It is never run by CI or `make test`.

## Notes

- Upstream MinIO no longer publishes community Docker images, so local S3 uses Chainguard's
  source-built `cgr.dev/chainguard/minio` image.
- **Database roles.** The API and Celery workers connect as `skillify_app`, and the outbox relay
  as `skillify_relay`. Neither owns any table, and neither can bypass Row-Level Security, so RLS
  always applies. Migrations run as the owner role. `python -m app.cli.db_roles` creates or
  updates both roles and is safe to run repeatedly; the `migrate` job runs it before
  `alembic upgrade head`.
- **Background services.** `worker` runs Celery tasks, and `beat` runs the Celery scheduler (keep
  only one instance). `outbox-relay` publishes outbox events to Kafka (Redpanda locally); see
  `docs/events.md`.
- **Hosts and ports are configuration.** Every port and every externally visible URL is set in
  `.env` (see `.env.example`): `WEB_ORIGIN`, `KEYCLOAK_PUBLIC_URL`, `S3_PUBLIC_ENDPOINT_URL`. If the
  default Keycloak port is taken, change `KEYCLOAK_PORT` (e.g. to 8180); `KEYCLOAK_PUBLIC_URL` and
  `KEYCLOAK_INTERNAL_URL` follow it (`${KEYCLOAK_PORT}`). From these come:
  - the token issuer, `<KEYCLOAK_PUBLIC_URL>/realms/skillifyme`, pinned via `KC_HOSTNAME` so it's
    the same whichever host fetched the token
  - the API's JWKS URL, the realm's redirect URIs and web origins, and MinIO's CORS origin
  `apps/api/tests/test_config_hosts.py` fails if a host or port gets hardcoded.
- **Keycloak realm.** `infra/keycloak/realm.template.json` is rendered from `.env` at startup by
  the `keycloak-realm` job and imported on Keycloak's first start. Keycloak never re-imports an
  existing realm, so `keycloak-sync` then re-applies the settings (redirect URIs, SMTP,
  brute-force settings, missing clients and roles) without deleting anything or touching client
  secrets. Both live in `infra/keycloak/realm.py`.
- **A dev/demo server** runs the same compose file plus `docker-compose.prod.yml` behind Caddy,
  configured only through `.env` (`.env.dev-server.example` lists the keys that change). See
  `infra/dev-vm/README.md`; nothing has been deployed yet.
