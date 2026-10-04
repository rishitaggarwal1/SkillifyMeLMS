# Repository instructions

Read CLAUDE.md first. Every decision in it is binding.

## Workflow

- Before coding any phase, present a plan and wait for user approval.
- After coding, run local lint, type-checks and all tests, and fix failures before
  reporting the step as done (`make lint` and `make test`). API tests use real
  Postgres, Redis, Keycloak and MinIO from Docker Compose.
- A step is done only when local lint, type-checks and all tests pass **and** the
  GitHub Actions run for its pushed commit is green. After each push, check the
  exact commit with `python scripts/ci_status.py <full-sha>` (no `gh` required).
  The checker polls Actions, reports every job and exits zero only for a green
  run with successful jobs. Include the CI run URL in every step summary.
- Record deviations from a plan in that phase's `docs/plans/` file as you go.
- Do not claim a check passed unless you ran it. Report unavailable checks plainly.

## Layout and module boundaries

- `apps/web`: Next.js App Router, strict TypeScript. Read `apps/web/AGENTS.md`
  and the relevant bundled Next.js documentation before changing web code.
- `apps/api`: async FastAPI modular monolith. Each backend module lives under
  `apps/api/app/modules/<module>/` with `router.py`, `schemas.py`, `models.py`,
  `service.py`, `repository.py` and `tests/`. Tableless module exceptions are
  documented in the phase plans.
- `services/`: standalone runner services; `infra/`: infrastructure;
  `docs/`: architecture, access control, events and phase plans.
- Modules talk only via service interfaces. Never query another module's tables
  or import another module's repositories or models for querying. Database RLS
  helpers are the documented SQL interfaces between modules.

## API and data contracts

- REST routes live under `/api/v1`. Every list endpoint uses cursor pagination;
  never use offset pagination.
- Every error uses the envelope
  `{"error": {"code": str, "message": str, "details": ...}}`.
- Outline mutations require the course revision in `If-Match`: return
  `428 precondition_required` when missing and `409` when stale. Submission and
  grading mutations use their documented submission revision contracts.
- Return 404, not 403, for a resource the caller cannot see; do not reveal
  another organization's or another student's resource existence. Missing
  permissions in the caller's own scope follow `docs/access-control.md`.
- Every new endpoint needs a row in `MATRIX` in
  `apps/api/tests/test_endpoint_roles.py` and a line in `docs/access-control.md`.
- Run `make gen-api` after API changes. Never edit
  `apps/web/src/lib/api/schema.ts` by hand.
- Keep the API stateless. Use UUIDv7 IDs, UTC `timestamptz`, indexed foreign keys
  and filter columns, and batched queries. Tenant-owned tables have
  `organization_id` and per-operation RLS policies; request identity settings
  are transaction-local. Authorization belongs in the service layer.
- Write domain events and audits in the state-change transaction; document
  event changes in `docs/events.md`. Follow the ownership, sharing, versioning
  and completion rules in CLAUDE.md and the access-control reference.

## Secrets and safety

- No secrets in code. Configure through environment variables documented in
  `.env.example`; keep browser tokens in encrypted httpOnly cookies.
- `.claude/` and `.secrets/` stay out of git. Never stage generated credentials,
  private local configuration or secret files.
- Tests ship with features. Use real backing services for API tests, Vitest for
  web units and Playwright for key user flows. Keep the UI mobile-first.
