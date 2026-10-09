# Step 9 student bundle acceptance (2026-10-09)

Measured production builds with the Step 8 release as baseline. **KiB = 1,024
bytes**. JavaScript totals include shared framework/runtime first-load scripts.
Deferred downloads are excluded from first load but remain in the dependency audit.

Source baseline: Step 8 `4da76dfa07e3d4bbca8791a9621ca0b9b11ab223`.
Reduction commit: `7ac8fd49c42468a4a355edb5953ca22d747685f5`, [green acceptance CI](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37977673085). After artifacts
were captured from the changed worktree before that commit, so their recorded
HEAD is the baseline with `application_worktree_modified: true`. Production
application code did not change after the measured builds. The new renderer
test's import-settlement setup was repaired before the final passing local gates.
The release close-out changes documentation only.

## Safe reductions delivered

- Shared error-envelope validation uses named `object`, `optional`, `string`
  and `unknown` imports from `zod/mini`. The standard envelope, optional/null
  details, untrusted-response rejection and field stripping retain the same
  behavior, with independent tests. Deferred student quiz validation uses named
  classic Zod imports, retaining its chained UUID and bounds rules.
  [Zod Mini's functional API](https://zod.dev/packages/mini) supports these shared
  schemas without removing client validation.
- Named classic imports alone saved **49.6 KiB** per route. The retained Mini
  change and first-use toast loading save **73.4 KiB total**, including that
  experiment; these are not additive savings.
- All callers use a small global toast adapter. Sonner imports on the first
  notification, queues messages until its themed host mounts and delivers each
  once. Import failure retains messages in an accessible retry/dismiss fallback.
  Tests cover real first-toast rendering, ordered concurrent messages, mount
  races, remounts, failure/retry and the existing failed-edit notification.
- No dependency version changed. All emitted student browser graphs, including
  deferred quiz/assignment/history chunks, remain free of Tiptap, dnd-kit and
  ProseMirror. A CI guard additionally rejects Sonner in student first load.

## Before / after and fixed CI ceilings

Windows: Node 22.14.0, Next 16.3.6, build `yRQp-xTUgYieyrSMA_ZOI`.
Linux Docker builder: Node 22.23.3, zlib 1.3.1-e00f703, build
`ihvAT9lx4I9xXrCRdRVDB`. Sum actual files compressed independently at gzip
level 9. Linux differs by at most 38 bytes per route. Ceilings are the **exact
larger measured number**, without whole-KiB rounding or automatic increase.

| Student route                                          | Before bytes (KiB) | After Windows bytes (KiB) | After Linux bytes (KiB) | Windows saving KiB | CI ceiling bytes |
| ------------------------------------------------------ | -----------------: | ------------------------: | ----------------------: | -----------------: | ---------------: |
| `/learn`                                               |    341,751 (333.7) |           266,637 (260.4) |         266,673 (260.4) |              73.35 |          266,673 |
| `/learn/courses/[courseId]`                            |           Redirect |                  Redirect |                Redirect |               None |             None |
| `/learn/courses/[courseId]/lessons/[lessonId]`         |           Redirect |                  Redirect |                Redirect |               None |             None |
| `/learn/enrollments/[enrollmentId]`                    |    345,419 (337.3) |           270,318 (264.0) |         270,349 (264.0) |              73.34 |          270,349 |
| `/learn/enrollments/[enrollmentId]/lessons/[lessonId]` |    345,419 (337.3) |           270,318 (264.0) |         270,349 (264.0) |              73.34 |          270,349 |
| `/learn/enrollments/[enrollmentId]/video/[lessonId]`   |    334,442 (326.6) |           259,328 (253.2) |         259,366 (253.3) |              73.35 |          259,366 |

Baseline shared transfer: **334,442 bytes (326.6 KiB), 13 files**.
New Windows shared transfer: **259,328 bytes (253.25 KiB), 12 files**.
Course URLs redirect to enrollment players and have no independent browser
bundle. The production audit passed the tightened ceilings. Setting `/learn`'s
ceiling to one byte produced the expected regression failure; the exact budgets
were restored byte-for-byte afterward.

Evidence: [baseline and all modules](student-bundles-step9-before.json),
[named-import experiment](student-bundles-step9-named-imports.json),
[final route audit](student-bundles-step9-after.json),
[Linux measurement](student-bundles-step9-linux.json),
[full final package/module attribution](student-bundles-step9-after-packages.json).

## Complete shared attribution by package

Every package and shared module is included, with explicit application/generated
and bundler-runtime groups. Next's vendored React/React DOM belongs to `next`,
rather than being counted twice. Sonner has zero shared modules after deferral.

Analyzer module weights use production `compressed_size` (raw DEFLATE level 6)
plus an 18-byte gzip wrapper per part. See the [Next compression implementation](https://github.com/vercel/next.js/blob/canary/turbopack/crates/turbopack-analyze/src/compressed_size.rs).
Independent module compression does not add to whole-file gzip transfer. The
last two columns proportionally allocate **measured shared transfer** by those
weights: an **estimate** of package share, not measured package download sizes.
Their exact byte values sum to 334,442 before and 259,328 after; displayed rounding can differ slightly.

| Package / group             | Shared modules before / after | Before module gzip equivalents KiB | After module gzip equivalents KiB | Estimated before shared transfer KiB | Estimated after shared transfer KiB |
| --------------------------- | ----------------------------: | ---------------------------------: | --------------------------------: | -----------------------------------: | ----------------------------------: |
| `next`                      |                     157 / 157 |                             167.47 |                            167.52 |                               122.40 |                              127.84 |
| `@base-ui/react`            |                     142 / 142 |                              66.86 |                             66.85 |                                48.87 |                               51.02 |
| `zod`                       |                       89 / 12 |                             132.41 |                             26.57 |                                96.78 |                               20.28 |
| `@tanstack/query-core`      |                       16 / 16 |                              13.18 |                             13.18 |                                 9.63 |                               10.06 |
| `(application / generated)` |                       21 / 23 |                              11.67 |                             12.44 |                                 8.53 |                                9.50 |
| `cn`                        |                         3 / 3 |                              10.49 |                             10.50 |                                 7.67 |                                8.01 |
| `@base-ui/utils`            |                       35 / 35 |                               8.64 |                              8.64 |                                 6.31 |                                6.59 |
| `lucide-react`              |                       33 / 29 |                               6.20 |                              5.45 |                                 4.53 |                                4.16 |
| `(bundler runtime)`         |                         4 / 4 |                               4.29 |                              4.29 |                                 3.13 |                                3.27 |
| `@floating-ui/dom`          |                         1 / 1 |                               2.98 |                              2.99 |                                 2.18 |                                2.28 |
| `@floating-ui/core`         |                         1 / 1 |                               2.60 |                              2.61 |                                 1.90 |                                1.99 |
| `openapi-fetch`             |                         1 / 1 |                               2.46 |                              2.46 |                                 1.80 |                                1.88 |
| `@tanstack/react-query`     |                         8 / 8 |                               1.88 |                              1.88 |                                 1.37 |                                1.44 |
| `@floating-ui/utils`        |                         2 / 2 |                               1.83 |                              1.82 |                                 1.34 |                                1.39 |
| `next-themes`               |                         1 / 1 |                               1.44 |                              1.44 |                                 1.05 |                                1.10 |
| `@floating-ui/react-dom`    |                         1 / 1 |                               1.21 |                              1.20 |                                 0.89 |                                0.92 |
| `use-sync-external-store`   |                         4 / 4 |                               0.94 |                              0.94 |                                 0.69 |                                0.72 |
| `@swc/helpers`              |                         2 / 2 |                               0.44 |                              0.44 |                                 0.32 |                                0.33 |
| `class-variance-authority`  |                         1 / 1 |                               0.40 |                              0.40 |                                 0.29 |                                0.31 |
| `clsx`                      |                         1 / 1 |                               0.23 |                              0.23 |                                 0.17 |                                0.18 |
| `sonner`                    |                         1 / 0 |                               9.22 |                              0.00 |                                 6.74 |                                0.00 |
| **Total**                   |                 **524 / 444** |                         **446.85** |                        **331.86** |                           **326.60** |                          **253.25** |

Baseline top 15: **146.40 KiB**
of independent module equivalents; the other **509 modules** total
**300.45 KiB** on the same basis.
Subtracting the top 15 from actual shared transfer produces about 180 KiB but
mixes compression bases. All 21 baseline groups above and the full module
artifact account for the remainder. After reduction, all **444 modules /
20 groups** are attributed; the other **429 modules** beyond the
new top 15 total **205.33 KiB** of module equivalents.

Shared membership follows synchronous module edges from route entries and the
client bootstrap. Sonner remains deferred even though every route shares its
async chunk. Repeated library sources cannot promote an async entry's chunk
into first load. Common file counts reconcile with production shared files;
player-only SSR preload files do not enter this shared inventory. Missing or
changed analyzer metadata fails the report.

## Remaining reduction plan (Phase 3 follow-up)

Target: **under 200 KiB gzip on every student route**. It is not achieved. The
largest Linux route is **264.01 KiB**, requiring more than
**64.01 KiB** additional reduction. These estimates
are hypotheses after this step, subject to overlap and compression effects.

| Proposal                                                                                                         | Estimated additional saving | Acceptance constraint                                                                                          |
| ---------------------------------------------------------------------------------------------------------------- | --------------------------: | -------------------------------------------------------------------------------------------------------------- |
| Defer noninitial Base UI menus/dialogs and retain server-rendered triggers/layout; isolate common shell imports. |                   25-40 KiB | Preserve drawer, role/org switching, confirmation, keyboard/focus restoration, 44px controls and axe coverage. |
| Reduce class-conflict machinery after a shared-component equivalence audit.                                      |                     6-8 KiB | Preserve responsive/variant overrides and semantic tokens.                                                     |
| Narrow remaining shared Mini/core validation paths after tracing callers.                                        |                    5-10 KiB | Preserve envelope, bounds, UUID, refinement and failure behavior.                                              |
| Server-render additional static shell/navigation/read-only summaries using interactive islands.                  |                   10-20 KiB | Preserve context, transitions, invalidation and loading/error states.                                          |

Combined hypothesis: **46-78 KiB**, projecting the largest route to roughly
**186-218 KiB**. Only the upper range meets the target; no guarantee is claimed.
Measure each accepted change and investigate more work if needed. Required
React/Next router/RSC runtime and learning cache/timer behavior remain. Fixed
CI ceilings remain enforced until another verified reduction tightens them.

## Reproduction

From `apps/web`, with lockfile dependencies installed:

```sh
pnpm build
pnpm exec next experimental-analyze --output
node scripts/student-bundles.mjs
node scripts/student-bundle-breakdown.mjs --report ../../docs/design/student-bundles-step9-after-packages.json
```

For Linux, build the existing Dockerfile's `builder` target and sum its
`route-bundle-stats.json` first-load files with `gzipSync(..., {level: 9})`.
The committed Linux artifact records Node/zlib/build IDs and exact results.
