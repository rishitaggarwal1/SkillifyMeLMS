# Student bundle measurement — Phase 3 Step 7

Measured locally on 2026-10-08 with Node's gzip level 9 and Next.js 16.3.6.
The baseline is the verified production build of Step 6,
`c23fd8e71dd0300c6eb2133b0b7e30a1a2d761fe`. The after build uses the Step 7
working tree before its commit; its report identifies the base HEAD and marks
the application working tree as modified.

| Student route                                          | Before raw bytes | After raw bytes | Before gzip KiB | After gzip KiB |
| ------------------------------------------------------ | ---------------: | --------------: | --------------: | -------------: |
| `/learn`                                               |        1,196,527 |       1,197,452 |           333.6 |          333.7 |
| `/learn/enrollments/[enrollmentId]`                    |        1,226,197 |       1,207,162 |           343.9 |          337.3 |
| `/learn/enrollments/[enrollmentId]/lessons/[lessonId]` |        1,226,197 |       1,207,162 |           343.9 |          337.3 |
| `/learn/enrollments/[enrollmentId]/video/[lessonId]`   |        1,174,487 |       1,174,487 |           326.6 |          326.6 |

These are **first-load JavaScript totals**, including each route's shared and
runtime chunks. Gzip totals sum compression of each unique JS file separately,
as separate network transfers; KiB means 1,024 bytes. Tiny compression changes
can arise from generated chunk identifiers. CSS, fonts, server code and HTTP
headers are excluded. The two `/learn/courses/...` routes are server-only
redirects and have no separate first-load client bundle.

Quiz and assignment views load on demand from the client lesson switch.
Their deferred transfers are excluded from these first-load totals. The
dependency check independently covers **all emitted browser modules in each
route's production analyzer graph, including deferred chunks**. The after
player graph explicitly includes quiz-lesson, quiz-session, assignment-lesson
and assignment-history. All six student route graphs contain **zero Tiptap,
dnd-kit or ProseMirror browser modules**, before and after.

Machine-readable evidence:

- [Before](student-bundles-step7-before.json): exact byte counts, build ID and baseline SHA.
- [After](student-bundles-step7-after.json): exact byte counts, build ID and application source state.

Reproduce from `apps/web` after building:

```sh
pnpm build
pnpm exec next experimental-analyze --output
node scripts/student-bundles.mjs --report ../../docs/design/student-bundles-step7-after.json
```

The script reads Next's production route statistics and its bundled analyzer
metadata format, distinguishes browser outputs from SSR/server outputs and
fails on missing/empty audits, inconsistent build statistics or an authoring
dependency. The CI E2E job runs this same audit after its production build.
