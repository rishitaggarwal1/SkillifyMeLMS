import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, ".next");
const analysis = join(dist, "diagnostics", "analyze", "data");

// Next 16.3's bundled analyzer reader uses a big-endian JSON-header length,
// followed by JSON metadata and binary graph edges. We need the metadata only.
// Fail on missing/changed metadata; an empty audit must never imply success.
function header(path) {
  const bytes = readFileSync(path);
  const length = bytes.readUInt32BE(0);
  assert(length > 0 && length <= bytes.length - 4, "Invalid analyzer header");
  return JSON.parse(bytes.subarray(4, 4 + length).toString("utf8"));
}

const stats = JSON.parse(
  readFileSync(join(dist, "diagnostics", "route-bundle-stats.json"), "utf8"),
);
const budgets = JSON.parse(
  readFileSync(join(root, "scripts", "student-bundle-budgets.json"), "utf8"),
);
const redirects = new Set([
  "/learn/courses/[courseId]",
  "/learn/courses/[courseId]/lessons/[lessonId]",
]);
const routes = JSON.parse(readFileSync(join(analysis, "routes.json"), "utf8")).filter(
  (r) => r === "/learn" || r.startsWith("/learn/"),
);
assert(routes.length > 0, "No student routes found");
const rows = routes.sort().map((route) => {
  const graph = header(join(analysis, route.slice(1), "analyze.data"));
  const paths = new Map();
  const fullPath = (index) => {
    if (!paths.has(index)) {
      const source = graph.sources[index];
      assert(source && typeof source.path === "string", "Missing source metadata");
      paths.set(
        index,
        (source.parent_source_index === null ? "" : fullPath(source.parent_source_index)) +
          source.path,
      );
    }
    return paths.get(index);
  };
  const clientSources = new Set();
  // This includes emitted deferred chunks, not only first-load scripts.
  // Server/SSR modules cannot be confused with browser output.
  for (const part of graph.chunk_parts) {
    const filename = graph.output_files[part.output_file_index]?.filename;
    assert(typeof filename === "string", "Missing output metadata");
    if (filename.startsWith("[client-fs]/_next/") && filename.endsWith(".js") && part.size > 0) {
      clientSources.add(fullPath(part.source_index));
    }
  }
  const first = stats.find((s) => s.route === route);
  assert(first || redirects.has(route), "Missing production size statistics for " + route);
  assert(
    clientSources.size > 0 || (!first && redirects.has(route)),
    "No browser modules audited for " + route,
  );
  const forbidden = [...clientSources].filter((p) =>
    /@tiptap[/+]|@dnd-kit[/+]|prosemirror-/i.test(p),
  );
  assert.deepEqual(forbidden, [], "Authoring dependencies in student output: " + route);
  const files = first
    ? [...new Set(first.firstLoadChunkPaths)].map((p) => join(root, p.replaceAll("\\", "/")))
    : [];
  const buffers = files.map((p) => readFileSync(p));
  const raw = buffers.reduce((n, bytes) => n + bytes.length, 0);
  if (first) assert.equal(raw, first.firstLoadUncompressedJsBytes, "Stale build statistics");
  const gzip = first
    ? buffers.reduce((n, bytes) => n + gzipSync(bytes, { level: 9 }).length, 0)
    : null;
  if (gzip !== null) {
    assert(Number.isInteger(budgets.routes[route]), "Missing student bundle budget: " + route);
    assert(
      gzip <= budgets.routes[route],
      "Student bundle regression: " +
        route +
        " (" +
        gzip +
        " > " +
        budgets.routes[route] +
        " gzip bytes)",
    );
  }
  return {
    route,
    first_load_raw_bytes: first ? raw : null,
    first_load_gzip_bytes: gzip,
    budget_gzip_bytes: gzip === null ? null : budgets.routes[route],
    first_load_chunks: files.length,
    audited_client_sources: clientSources.size,
    authoring_dependencies: forbidden,
  };
});
for (const route of Object.keys(budgets.routes)) {
  assert(
    rows.some((row) => row.route === route && row.first_load_gzip_bytes !== null),
    "Budgeted route missing from production build: " + route,
  );
}
const report = {
  git_head: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim(),
  application_worktree_modified:
    execFileSync(
      "git",
      ["status", "--porcelain", "--", "src", "next.config.ts", "package.json", "pnpm-lock.yaml"],
      { cwd: root, encoding: "utf8" },
    ).trim().length > 0,
  next_version: JSON.parse(readFileSync(join(root, "node_modules/next/package.json"), "utf8"))
    .version,
  build_id: readFileSync(join(dist, "BUILD_ID"), "utf8").trim(),
  measurement:
    "Production first-load JS including shared/runtime chunks; sum of per-file gzip level 9. Redirects have no separate first-load bundle. Dependency audit covers all emitted browser chunks in each route graph, including deferred chunks.",
  routes: rows,
};
const json = JSON.stringify(report, null, 2) + "\n";
const arg = process.argv.indexOf("--report");
if (arg !== -1) {
  assert(process.argv[arg + 1], "--report requires a path");
  writeFileSync(resolve(process.argv[arg + 1]), json);
}
process.stdout.write(json);
