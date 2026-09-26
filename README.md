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
| Keycloak          | http://localhost:$KEYCLOAK_PORT (realm `skillifyme`) |
| MinIO console     | http://localhost:9001                 |
| Redpanda console  | http://localhost:8082                 |
| Mailpit           | http://localhost:8025                 |

Credentials for all of these are in your local `.env`, which is created from `.env.example`.

### Signing in

Open http://localhost:3000 and click **Sign in**. The web app sends you to Keycloak's login page
and back. Org admins get an **Admin** link: batches, members and invitations, and CSV import.
Tokens never reach browser JavaScript; they're kept in encrypted httpOnly cookies by the Next.js
server.

### Dev realm test users (local only)

`make dev` runs the `seed` job (`make seed` to run it again). It creates three orgs and the users
below. All of them use the password `Local-Dev-Only-1`.

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
make down            # stop (keeps data)
make clean           # stop and wipe local data volumes
```

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
- **Ports are configuration.** Every port is set in `.env` (see `.env.example`). If the default
  Keycloak port is taken, change `KEYCLOAK_PORT` (e.g. to 8180). Everything else follows from it:
  - the token issuer, `http://localhost:$KEYCLOAK_PORT/realms/skillifyme`, pinned via `KC_HOSTNAME`
    so it's the same whichever host fetched the token
  - the API's JWKS URL
  - the dev realm's redirect URIs
  `apps/api/tests/test_config_ports.py` fails if a port gets hardcoded.
