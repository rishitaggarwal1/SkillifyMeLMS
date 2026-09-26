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
| Keycloak          | http://localhost:8080 (realm `skillifyme`) |
| MinIO console     | http://localhost:9001                 |
| Redpanda console  | http://localhost:8082                 |
| Mailpit           | http://localhost:8025                 |

Credentials for all of these are in your local `.env`, which is created from `.env.example`.

### Dev realm test users (local only)

All four use the password `Local-Dev-Only-1`:

| User                          | Realm role    |
| ----------------------------- | ------------- |
| superadmin@skillifyme.local   | super_admin   |
| orgadmin@skillifyme.local     | org_admin     |
| instructor@skillifyme.local   | instructor    |
| student@skillifyme.local      | student       |

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
- **Port clash on 8080.** If something else already uses port 8080, set `KEYCLOAK_PORT` (and the
  port in `OIDC_ISSUER`) in your `.env`.
