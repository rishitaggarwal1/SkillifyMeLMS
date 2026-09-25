# @skillifyme/web

Next.js (App Router) frontend for SkillifyMe Portal. See the repo-root `README.md` and `CLAUDE.md`.

| Command          | What it does                                               |
| ---------------- | ---------------------------------------------------------- |
| `pnpm dev`       | Dev server on :3000 (usually run via `make dev` in docker) |
| `pnpm lint`      | ESLint                                                     |
| `pnpm typecheck` | `tsc --noEmit` (strict)                                    |
| `pnpm test`      | Vitest unit/component tests                                |
| `pnpm test:e2e`  | Playwright against a running stack                         |
| `pnpm gen:api`   | Regenerate `src/lib/api/schema.ts` (prefer `make gen-api`) |

Layout:

- `src/app` — routes. `src/app/backend/[...path]` proxies browser calls to the API (same-origin).
- `src/features/<feature>` — feature code: query options, components, tests.
- `src/components/ui` — shadcn/ui primitives.
- `src/lib/api` — typed API client; `schema.ts` is generated, never edit by hand.
