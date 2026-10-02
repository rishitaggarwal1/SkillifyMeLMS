# SkillifyMe Portal

Multi-tenant, browser-based practical learning platform (LMS + coding labs + assessments + analytics) for Indian colleges and placement training. Target scale: 100,000 concurrent users.

These are permanent decisions. Follow them in every task. Changing one requires explicit approval from the user.

## Architecture

- **Monorepo layout**
  - `apps/web` — Next.js (App Router, TypeScript)
  - `apps/api` — Python FastAPI (async)
  - `services/` — future runner services (code execution, etc.)
  - `infra/` — Terraform (later)
  - `docs/` — architecture and design docs; phase plans live in `docs/plans/`
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

## Content ownership & sharing

Decided 2026-09-26.

- Every course has exactly one **owner organization** (`courses.organization_id`).
- `organizations.is_content_publisher` marks orgs that author content for other orgs. SkillifyMe's own org has it.
- **Two-level assignment** through `course_assignments (course_id, organization_id, batch_id NULL)`:
  - `batch_id IS NULL` is an **org grant**: an entitlement for the org, which its `org_admin` distributes. An org grant alone makes the course visible to no students.
  - A `batch_id` row is a **batch assignment**: the course becomes visible to that batch's students.
  - A publisher assigns a course to an org (org grant) or directly to specific batches of that org.
  - The receiving org's `org_admin` chooses which of their batches receive a granted course. Org admins can only narrow within what was assigned to them, never widen it.
  - **Who removes assignments:** assignments the publisher made (including publisher-made batch assignments) can be removed only by the publisher. A receiving org's `org_admin` can remove only the assignments their own org created.
  - **Instructors of an assigned org** can read the course but cannot distribute it to batches; only the receiving org's `org_admin` distributes.
- **Students see a course only when it is assigned to their batch.** This includes students of the owner org.
- Owner-org members with the `instructor` or `org_admin` role can see, preview (drafts and published versions) and edit a course. No one else can edit. Assigned orgs get read and enroll access only.
- Any org can author private courses that are visible only to itself.
- **Versioning:**
  - Each published version is marked **minor** (corrections only: no lessons added, removed or reordered) or **major**.
  - Minor versions apply automatically to all existing enrollments.
  - Major versions apply only to new enrollments. An `org_admin` can opt their org's existing enrollments into the new major version; progress carries over for lessons whose stable lesson ID still exists.
  - Opt-in applies to a **whole organization or chosen batches**, never to individual students.
  - Lessons keep stable IDs across versions.
  - A video replaced in a minor release: lessons already completed stay completed; partially watched progress for that lesson restarts.
- **Lesson types** are a database enum that already includes the placeholder types `quiz`, `lab` and `assignment` alongside `video`, `notes` and `pdf`, so later phases need no enum migration.
- **Skills taxonomy** is global (no `organization_id`) in this phase. Everyone can read it; only `platform_admin` and staff (`org_admin`, `instructor`, `lab_author`) of a content-publisher org can create or edit skills.
- These rules are enforced by **PostgreSQL RLS policies**, written per operation, not only by service-layer checks. They must be covered by tests proving that:
  - an assigned org cannot edit
  - an unassigned org cannot see
  - a batch-level assignment limits visibility to that batch's students
  - org admins cannot widen an assignment
  - students cannot see courses not assigned to their batch

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
  3. A step is done only when (a) lint, type-check and all tests pass locally **and** (b) the GitHub Actions run for the pushed commit is green. Watch the run after each push (`gh run watch`, or poll the Actions API) rather than assuming; every step summary includes the CI run URL.

## Commands

- `make dev` — start the full local stack (docker-compose infra + api + web)
- `make test` — run all API and web tests
- `make lint` — lint and type-check both apps
- `make migrate` — apply Alembic migrations
- `make gen-api` — regenerate `apps/web/src/lib/api/schema.ts` from the API's OpenAPI spec
