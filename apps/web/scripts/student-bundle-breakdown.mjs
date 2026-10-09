// Production analyzer attribution; no application source or bundling settings change.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

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
const browserFiles = (g) =>
  new Set(
    g.output_files
      .filter((f) => f.filename.startsWith("[client-fs]/_next/") && f.filename.endsWith(".js"))
      .map((f) => f.filename),
  );
const shared = new Set(
  [...browserFiles(graphs[0])].filter((name) => graphs.every((g) => browserFiles(g).has(name))),
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
const gzip = (paths) =>
  paths.reduce((n, p) => n + gzipSync(readFileSync(join(root, p)), { level: 9 }).length, 0);
const report = {
  next_version: JSON.parse(readFileSync(join(root, "node_modules/next/package.json"), "utf8"))
    .version,
  build_id: readFileSync(join(dist, "BUILD_ID"), "utf8").trim(),
  method:
    "Shared = intersection of the four student browser graphs. Ranking uses production analyzer compressed_size: level 6 DEFLATE plus an 18-byte gzip wrapper per module part. Per-module gzip equivalents do not add to whole-file gzip. Analyzer recompiles in production mode with analyze-build IDs, so its filenames differ from production first-load stats. Whole first-load/shared totals independently sum actual transferred files at gzip level 9. Exclude the analyzer-only nomodule polyfill and all SSR/server/non-JS outputs.",
  compression_source:
    "https://github.com/vercel/next.js/blob/canary/turbopack/crates/turbopack-analyze/src/compressed_size.rs",
  learn_first_load_gzip_bytes: gzip(learn.firstLoadChunkPaths),
  shared_first_load_gzip_bytes: gzip(actualShared),
  shared_first_load_chunks: actualShared.length,
  top_15: rows.slice(0, 15),
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
