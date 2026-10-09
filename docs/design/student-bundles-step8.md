# Student shared first-load analysis — Phase 3 Step 8

The Step 7 production application is unchanged in Step 8. The measured `/learn`
first-load JavaScript is **333.7 KiB gzip**. Its shared portion is **326.6 KiB**,
spread across **13 files** common to all four renderable student routes. The
enrollment/lesson players load **337.3 KiB** and the video route **326.6 KiB**.
The two course redirects have no separate first-load bundle. The target of
**under 200 KiB on every student route is not met**; the largest route needs
more than 137.3 KiB of actual transfer reduction.

Evidence: [machine-readable breakdown](student-bundles-step8-breakdown.json),
[Step 7 first-load methodology](student-bundles-step7.md). Next.js is pinned
to 16.3.6. All six student graphs still exclude Tiptap, dnd-kit and ProseMirror,
including deferred quiz/assignment output. No reduction is implemented here.

## Top 15 modules in the shared client output

These sizes come from the production-mode analyzer's `compressed_size`, plus
an 18-byte gzip header/trailer per module part. The analyzer uses **level 6
DEFLATE without gzip wrappers**, as documented in [Next's compression
implementation](https://github.com/vercel/next.js/blob/canary/turbopack/crates/turbopack-analyze/src/compressed_size.rs).
They are **gzip-equivalent module attributions**, not separately downloaded
files; their sum is not a whole-chunk transfer total. Whole first-load/shared
figures above independently sum actual production files using Node gzip level 9.
KiB means 1,024 bytes.

| Rank | Module (package-relative path) | Gzip-equivalent KiB |
| ---: | --- | ---: |
| 1 | next: compiled/react-dom/cjs/react-dom-client.production.js | 61.7 |
| 2 | zod: v4/core/schemas.js | 11.3 |
| 3 | sonner: dist/index.mjs | 9.2 |
| 4 | zod: v4/core/compile.js | 7.6 |
| 5 | next: compiled/react-server-dom-turbopack/cjs/react-server-dom-turbopack-client.browser.production.js | 7.3 |
| 6 | next: client/components/segment-cache/cache.js | 7.0 |
| 7 | zod: v4/classic/schemas.js | 6.5 |
| 8 | cn: dist/engine.js | 5.6 |
| 9 | cn: dist/tables.js | 4.8 |
| 10 | zod: v4/core/json-schema-processors.js | 4.6 |
| 11 | zod: v4/core/util.js | 4.6 |
| 12 | next: client/components/router-reducer/ppr-navigations.js | 4.1 |
| 13 | zod: v4/classic/from-json-schema.js | 4.0 |
| 14 | zod: v4/core/to-json-schema.js | 4.0 |
| 15 | next: client/components/segment-cache/scheduler.js | 3.9 |

The shared-output intersection is calculated independently in analyzer graphs
and actual production route statistics. `experimental-analyze` recompiles in
production mode with `analyze-build` identifiers, so its hashed filenames differ
from the deployable build. The analyzer's extra `polyfill-nomodule.js` is excluded:
it is absent from first-load statistics and supported modern browsers do not
download it. SSR/server outputs, assets, CSS and route-only modules are excluded.
The baseline `/learn` has no deferred feature imports; the student player graphs
have extra deferred modules, which do not enter this shared ranking.

## Proposed reduction sequence (estimates, not achieved savings)

The analyzer attributes 89 shared modules to Zod, 142 to Base UI, five to
Floating UI, one to Sonner and three to `cn`. Their individually compressed
module sums overstate separately compressed file transfer; use fresh production
builds to accept savings. Do not reduce validation, accessibility, navigation,
timer durability, theme behavior or error contracts to achieve a number.

| Order | Proposal | Estimated first-load gzip saving | Gate / scope |
| ---: | --- | ---: | --- |
| 1 | Try direct named Zod value imports in shared validators; keep inferred types as type-only imports. This may remove unused JSON Schema and schema-constructor exports retained by the `z` namespace. | 25–45 KiB | Candidate for Step 9 only if mechanical, behavior-preserving and measured. Keep only if build, contracts, full suites and analyzer improve; report before/after. |
| 2 | Use narrow Zod/mini runtime validation in student/shared transport and split author/admin validators out of common client entry points. | 80–100 KiB **total Zod saving including order 1**, not additional | Phase 3 follow-up if it requires schema rewrites. Preserve every bounds/refinement/error test; first trace the common import paths. |
| 3 | Defer noninitial menu/dialog implementations from the shell/shared state barrel; use small native controls where appropriate. Keep initial triggers/layout server-rendered. | 30–50 KiB | Follow-up: preserve keyboard/focus restoration, mobile drawer, org/role switching, confirmation and axe coverage. Base UI imports are already package subpaths; adding an icon-import optimizer alone will not solve this. |
| 4 | Reduce or replace the class conflict engine only after proving every responsive/variant override still resolves correctly. | 8–10 KiB | Follow-up; shared design system behavior needs a focused equivalence audit. |
| 5 | Load toast rendering on demand while preserving the global host, queue and live announcements. | 5–7 KiB | Follow-up; buffering, first-toast behavior and accessibility need tests. |

The combined hypothesis is **123–167 KiB** of transfer savings, projecting the
largest route to roughly **170–214 KiB**. This is an estimate with compression
and overlap uncertainty; it does **not** prove the 200 KiB target. Aim for the
middle/high end, remeasure after each accepted change, and investigate further
server rendering if all four routes remain above target. React DOM and the
required Next router/RSC runtime remain; do not propose removing them as a
cheap optimization. There is no identified heavy authoring leak or bulk icon
import to remove in Step 8.

## Regression budget

The existing mandatory CI student audit now fails above the current production
baseline rounded **up to whole KiB**: `/learn` **334 KiB**, the two players
**338 KiB**, and video **327 KiB**. This allows less than 1 KiB for chunk-ID/gzip
variation and is explicit in [the fixed budgets](../../apps/web/scripts/student-bundle-budgets.json).
It fails on missing budgeted/new student routes, stale build data, missing audits
or authoring dependencies. It never raises budgets automatically. The desired
200 KiB target is recorded separately; enforcing it today would make the
unchanged application fail. Tighten ceilings after verified reductions.

Reproduce from `apps/web`:

```sh
pnpm build
pnpm exec next experimental-analyze --output
node scripts/student-bundles.mjs
node scripts/student-bundle-breakdown.mjs --report ../../docs/design/student-bundles-step8-breakdown.json
```
