# SkillifyMe Portal

Multi-tenant, browser-based practical learning platform (LMS + coding labs + assessments + analytics) for Indian colleges and placement training. Target scale: 100,000 concurrent users.

These are permanent decisions. Follow them in every task. Changing one requires explicit approval from the user.

## Architecture

- **Monorepo layout**
  - `apps/web` — Next.js (App Router, TypeScript)
  - `apps/api` — Python FastAPI (async)
  - `services/` — future runner services (code execution, etc.)
  - `infra/` — Terraform (later)
  - `docs/` — architecture and design docs
- **Modular monolith backend**: each module lives in `apps/api/app/modules/<module>/` with `router.py`, `schemas.py`, `models.py`, `service.py`, `repository.py`, `tests/`.
  - Modules interact only through each other's **service interfaces**. Never query another module's tables, and never import another module's repository or models for querying.
- **Stateless API**: all state lives in PostgreSQL, Redis, or S3. No in-process session/state that must survive a request.
- **Multi-tenancy**: every tenant-owned table has `organization_id`. PostgreSQL Row-Level Security is enforced using per-request session settings `app.current_org` and `app.current_user` (set via `set_config(..., true)` inside the request transaction).
- **IDs** are UUIDv7. **Timestamps** are `timestamptz` stored in UTC.
- **REST API** under `/api/v1`.
  - Cursor pagination on every list endpoint (no offset pagination).
  - One error envelope for every error: `{"error": {"code": str, "message": str, "details": ...}}`.
- **Frontend API types** are generated from FastAPI's OpenAPI spec with `openapi-typescript` into `apps/web/src/lib/api/schema.ts` (`make gen-api`). Never hand-edit that file.
- **Domain events** are written to an outbox table in the same transaction as the state change, then relayed to Kafka.

## Stack

- **Web**: Next.js, TypeScript strict mode, Tailwind CSS, shadcn/ui, TanStack Query, React Hook Form + Zod.
- **API**: FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, pydantic-settings, Celery + Redis, structlog (JSON logs), OpenTelemetry.
- **Data**: PostgreSQL 16, Redis 7, S3 (MinIO locally), Kafka (Redpanda locally), ClickHouse (added in Phase 5).
- **Auth**: Keycloak (OIDC). **Realtime**: Centrifugo (added in Phase 4).

## Engineering rules

- **Tests ship with every feature**
  - API: pytest against real Postgres/Redis from docker-compose (no mocking the database).
  - Web: Vitest for units, Playwright for key user flows.
- **No secrets in code.** All config via environment variables, documented in `.env.example`.
- **Security by default**
  - Validate all input (Pydantic on the API, Zod on the web).
  - Authorization checks live in the service layer.
  - No string-built SQL — use SQLAlchemy constructs / bound parameters.
  - No tokens in `localStorage` (use httpOnly cookies).
- **Performance by default**
  - No N+1 queries (use explicit eager loading / batched queries).
  - Index every foreign key and every filter column.
  - Paginate every list.
  - Cache read-heavy data in Redis.
- **Mobile-first UI**: students use low-end Android phones on 4G. Keep JS bundles small, avoid heavy client-side dependencies, design for small screens first.
- **Workflow**
  1. Before coding any phase, present a plan and wait for approval.
  2. After coding, run lint, type-check, and all tests, and fix failures before saying the work is done.

## Commands

- `make dev` — start the full local stack (docker-compose infra + api + web)
- `make test` — run all API and web tests
- `make lint` — lint and type-check both apps
- `make migrate` — apply Alembic migrations
- `make gen-api` — regenerate `apps/web/src/lib/api/schema.ts` from the API's OpenAPI spec
