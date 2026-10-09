// Complete package attribution of the shared production browser output.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { readModuleGraph, synchronousBrowserFiles } from "./student-first-load.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, ".next");
const base = join(dist, "diagnostics", "analyze", "data");
const stats = JSON.parse(
  readFileSync(join(dist, "diagnostics", "route-bundle-stats.json"), "utf8"),
).filter((s) => s.route === "/learn" || s.route.startsWith("/learn/"));
assert.equal(stats.length, 4, "Review attribution for new student routes");
const graphs = stats.map((s) => {
  const bytes = readFileSync(join(base, s.route.slice(1), "analyze.data"));
  const length = bytes.readUInt32BE(0);
  assert(length > 0 && length <= bytes.length - 4, "Invalid analyzer header");
  return JSON.parse(bytes.subarray(4, 4 + length).toString("utf8"));
});
const modulesGraph = readModuleGraph(join(base, "modules.data"));
const firstLoadFiles = graphs.map((g, i) => {
  const files = synchronousBrowserFiles(g, stats[i].route, modulesGraph);
  return files;
});
const shared = new Set(
  [...firstLoadFiles[0]].filter((name) => firstLoadFiles.every((files) => files.has(name))),
);
const graph = graphs[stats.findIndex((s) => s.route === "/learn")];
const fullPath = (i) => {
  const s = graph.sources[i];
  assert(s && typeof s.path === "string", "Missing source");
  return (s.parent_source_index === null ? "" : fullPath(s.parent_source_index)) + s.path;
};
const modules = new Map();
for (const part of graph.chunk_parts) {
  if (!shared.has(graph.output_files[part.output_file_index].filename) || part.size <= 0) continue;
  const path = fullPath(part.source_index);
  // Analyzer includes a nomodule polyfill. It is not in production first-load
  // stats and is not downloaded by supported modern browsers; do not count it.
  if (path.endsWith("/polyfill-nomodule.js")) continue;
  assert(Number.isInteger(part.compressed_size), "Missing module compression metadata");
  const row = modules.get(path) ?? {
    source: path,
    raw_bytes: 0,
    analyzer_deflate_bytes: 0,
    parts: 0,
  };
  row.raw_bytes += part.size;
  row.analyzer_deflate_bytes += part.compressed_size;
  row.parts++;
  modules.set(path, row);
}
assert(modules.size >= 15, "Empty/incomplete analyzer attribution");
const rows = [...modules.values()]
  .map((m) => ({ ...m, gzip_equivalent_bytes: m.analyzer_deflate_bytes + 18 * m.parts }))
  .sort((a, b) => b.gzip_equivalent_bytes - a.gzip_equivalent_bytes);
const learn = stats.find((s) => s.route === "/learn");
const actualShared = learn.firstLoadChunkPaths.filter((p) =>
  stats.every((s) => s.firstLoadChunkPaths.includes(p)),
);
assert.equal(shared.size, actualShared.length, "Shared analyzer/build file counts differ");
const gzip = (paths) =>
  paths.reduce((n, p) => n + gzipSync(readFileSync(join(root, p)), { level: 9 }).length, 0);
const packageOf = (path) => {
  const marker = "/node_modules/";
  const start = path.lastIndexOf(marker);
  if (start === -1)
    return path.includes("[project]/") ? "(application / generated)" : "(bundler runtime)";
  const parts = path.slice(start + marker.length).split("/");
  return parts[0].startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
};
const packages = new Map();
for (const row of rows) {
  const name = packageOf(row.source);
  const group = packages.get(name) ?? {
    package: name,
    modules: 0,
    raw_bytes: 0,
    gzip_equivalent_bytes: 0,
  };
  group.modules++;
  group.raw_bytes += row.raw_bytes;
  group.gzip_equivalent_bytes += row.gzip_equivalent_bytes;
  packages.set(name, group);
}
const totalAttribution = rows.reduce((n, m) => n + m.gzip_equivalent_bytes, 0);
const sharedGzip = gzip(actualShared);
const packageRows = [...packages.values()].sort(
  (a, b) => b.gzip_equivalent_bytes - a.gzip_equivalent_bytes,
);
// Pooling changes compression. This proportional allocation accounts for every
// transferred byte, but is explicitly an estimate of each package's share.
let allocated = 0;
let cumulativeWeight = 0;
for (const group of packageRows) {
  cumulativeWeight += group.gzip_equivalent_bytes;
  const cumulativeBytes = Math.round((sharedGzip * cumulativeWeight) / totalAttribution);
  group.estimated_shared_transfer_bytes = cumulativeBytes - allocated;
  allocated = cumulativeBytes;
}
assert.equal(
  packageRows.reduce((n, p) => n + p.modules, 0),
  rows.length,
);
assert.equal(allocated, sharedGzip);
const report = {
  next_version: JSON.parse(readFileSync(join(root, "node_modules/next/package.json"), "utf8"))
    .version,
  build_id: readFileSync(join(dist, "BUILD_ID"), "utf8").trim(),
  method:
    "Shared = intersection of synchronous file candidates in the four student browser graphs. Follow module_dependencies from each route's app-page entry and the client bootstrap, excluding app-ssr and async edges. A repeated library source does not promote a chunk whose async entry is unreachable and has no synchronous application entry. Shared-file count is reconciled with actual production shared first-load stats; route-only SSR preload extras do not enter this shared inventory. Count every module in each selected shared file. Ranking uses production analyzer compressed_size: level 6 DEFLATE plus an 18-byte gzip wrapper per module part. Per-module gzip equivalents do not add to whole-file gzip. Analyzer recompiles in production mode with analyze-build IDs, so its filenames differ from production first-load stats. Whole first-load/shared totals independently sum actual transferred files at gzip level 9. Exclude deferred-only files, analyzer-only nomodule polyfill and all SSR/server/non-JS outputs.",
  compression_source:
    "https://github.com/vercel/next.js/blob/canary/turbopack/crates/turbopack-analyze/src/compressed_size.rs",
  learn_first_load_gzip_bytes: gzip(learn.firstLoadChunkPaths),
  shared_first_load_gzip_bytes: sharedGzip,
  shared_first_load_chunks: actualShared.length,
  top_15: rows.slice(0, 15),
  all_modules: rows,
  module_attribution_total_gzip_equivalent_bytes: totalAttribution,
  remaining_after_top_15_gzip_equivalent_bytes: rows
    .slice(15)
    .reduce((n, m) => n + m.gzip_equivalent_bytes, 0),
  package_allocation_method:
    "All shared modules classified by their innermost node_modules package; application/generated and bundler modules have explicit groups. Package gzip-equivalent sums are independent module compression, not measured package download sizes. estimated_shared_transfer_bytes proportionally allocates actual shared gzip-9 transfer using those weights, with rounding reconciled to the measured total; this is an estimate, not exact per-package compression.",
  packages: packageRows,
  groups: Object.fromEntries(
    ["zod", "@base-ui/react", "@floating-ui", "sonner", "cn"].map((name) => {
      const group = rows.filter(
        (m) =>
          m.source.includes("/node_modules/" + name + "/") ||
          (name === "@floating-ui" && m.source.includes("/node_modules/@floating-ui/")),
      );
      return [
        name,
        {
          modules: group.length,
          gzip_equivalent_bytes: group.reduce((n, m) => n + m.gzip_equivalent_bytes, 0),
        },
      ];
    }),
  ),
};
const json = JSON.stringify(report, null, 2) + "\n";
const arg = process.argv.indexOf("--report");
if (arg !== -1) {
  assert(process.argv[arg + 1], "--report requires a path");
  writeFileSync(resolve(process.argv[arg + 1]), json);
}
process.stdout.write(json);
