import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

export function readModuleGraph(path) {
  const bytes = readFileSync(path);
  const length = bytes.readUInt32BE(0);
  assert(length > 0 && length <= bytes.length - 4, "Invalid module graph header");
  return {
    header: JSON.parse(bytes.subarray(4, 4 + length).toString("utf8")),
    data: bytes.subarray(4 + length),
  };
}

/** Next 16.3 analyzer's synchronous graph traversal. Skip SSR and async edges:
 * sharing a deferred file across routes does not make it first-load output. */
export function synchronousBrowserFiles(graph, route, modules) {
  const { header, data } = modules;
  const edges = (reference, index) => {
    const { offset, length } = reference;
    if (!length) return [];
    const count = data.readUInt32BE(offset);
    if (index >= count) return [];
    const previous = index === 0 ? 0 : data.readUInt32BE(offset + index * 4);
    const end = data.readUInt32BE(offset + 4 + index * 4);
    return Array.from({ length: end - previous }, (_, j) =>
      data.readUInt32BE(offset + 4 + count * 4 + (previous + j) * 4),
    );
  };
  const visited = new Set();
  const asyncEntries = new Set();
  let routeEntry = false;
  for (const [index, module] of header.modules.entries()) {
    if (module.ident.includes(`app-page.js?page=${route}/page `)) {
      routeEntry = true;
      visited.add(index);
    }
    if (module.ident.includes("next/dist/client/app-next-turbopack.js [app-client]"))
      visited.add(index);
  }
  assert(routeEntry, "Missing student route entry: " + route);
  for (const index of visited) {
    for (const dep of edges(header.async_module_dependencies, index)) {
      asyncEntries.add(header.modules[dep].path);
    }
    for (const dep of edges(header.module_dependencies, index)) {
      if (!header.modules[dep].ident.includes("[app-ssr]")) visited.add(dep);
    }
  }
  const paths = new Set(
    [...visited]
      .filter((i) => header.modules[i].ident.includes("[app-client]"))
      .map((i) => header.modules[i].path),
  );
  assert(paths.size > 0, "No synchronous client modules: " + route);
  const fullPath = (index) => {
    const source = graph.sources[index];
    return (
      (source.parent_source_index === null ? "" : fullPath(source.parent_source_index)) +
      source.path
    );
  };
  const files = new Set();
  for (const part of graph.chunk_parts) {
    const filename = graph.output_files[part.output_file_index].filename;
    if (!filename.startsWith("[client-fs]/_next/") || !filename.endsWith(".js") || part.size <= 0)
      continue;
    const source = fullPath(part.source_index);
    if (source.endsWith("/polyfill-nomodule.js")) continue;
    if (paths.has(source) || source.startsWith("[turbopack]/")) files.add(filename);
  }
  // A shared library source can have parts in both initial and deferred files.
  // Its path alone must not promote a deferred entry's entire chunk to first load.
  for (const filename of files) {
    const parts = graph.chunk_parts.filter(
      (p) => graph.output_files[p.output_file_index].filename === filename,
    );
    const sources = parts.map((p) => fullPath(p.source_index));
    const deferredEntry = sources.some((p) => asyncEntries.has(p) && !paths.has(p));
    const initialAppEntry = sources.some((p) => p.startsWith("[project]/src/") && paths.has(p));
    if (deferredEntry && !initialAppEntry) files.delete(filename);
  }
  return files;
}
