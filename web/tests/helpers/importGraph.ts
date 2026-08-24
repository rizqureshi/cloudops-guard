/**
 * A small, real-source import-graph builder used only by the Phase 3I
 * architectural-isolation test (`tests/unit/contact-form/isolation.test.ts`).
 * It reads actual files from disk and extracts actual `import`/`export …
 * from` specifiers via a regex scan -- it does not hand-maintain a list of
 * "allowed" or "forbidden" modules; the graph is derived from whatever the
 * source files actually import today, so it stays correct as the codebase
 * changes.
 *
 * Deliberately not a full TypeScript/ESTree parser: this project has no
 * such dependency, and a regex over `import`/`export … from "…"` and
 * `import("…")` is sufficient for this codebase's plain, single-line-or-
 * simple-multiline import style. Only *relative* specifiers (starting with
 * `.`) are resolved and followed -- a bare package specifier (`react`,
 * `zod`) cannot reach an internal module and is not part of this graph.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, resolve } from "node:path";

const SOURCE_EXTENSIONS = [".ts", ".tsx", ".astro"];
const RESOLVABLE_EXTENSIONS = [".ts", ".tsx", ".astro", ".json", ".css"];
const IMPORT_SPECIFIER_PATTERN =
  /(?:import|export)\s+(?:type\s+)?[\s\S]*?\s+from\s+["']([^"']+)["']|import\s+["']([^"']+)["']|import\(\s*["']([^"']+)["']\s*\)/g;

/** Recursively lists every `.ts`/`.tsx`/`.astro` file under `rootDir`, excluding test files and build output. */
export function listSourceFiles(rootDir: string): string[] {
  const results: string[] = [];
  function walk(dir: string): void {
    for (const entry of readdirSync(dir)) {
      if (entry === "node_modules" || entry === "dist" || entry === ".astro") {
        continue;
      }
      const fullPath = join(dir, entry);
      const stats = statSync(fullPath);
      if (stats.isDirectory()) {
        walk(fullPath);
        continue;
      }
      if (SOURCE_EXTENSIONS.includes(extname(fullPath)) && !entry.includes(".test.")) {
        results.push(fullPath);
      }
    }
  }
  walk(rootDir);
  return results;
}

/** Every relative (`.`-prefixed) import/export-from/dynamic-import specifier found in `filePath`'s own text. */
export function extractRelativeImportSpecifiers(filePath: string): string[] {
  const content = readFileSync(filePath, "utf-8");
  const specifiers: string[] = [];
  for (const match of content.matchAll(IMPORT_SPECIFIER_PATTERN)) {
    const specifier = match[1] ?? match[2] ?? match[3];
    if (specifier && specifier.startsWith(".")) {
      specifiers.push(specifier);
    }
  }
  return specifiers;
}

function fileExistsAsFile(candidate: string): boolean {
  try {
    return statSync(candidate).isFile();
  } catch {
    return false;
  }
}

/** Resolves a relative import specifier from `fromFile` to an absolute file path on disk, or `null` if it cannot be found. */
export function resolveRelativeImport(fromFile: string, specifier: string): string | null {
  const base = resolve(dirname(fromFile), specifier);

  if (extname(base) && fileExistsAsFile(base)) {
    return base;
  }
  for (const ext of RESOLVABLE_EXTENSIONS) {
    if (fileExistsAsFile(base + ext)) {
      return base + ext;
    }
  }
  for (const ext of RESOLVABLE_EXTENSIONS) {
    const indexCandidate = join(base, `index${ext}`);
    if (fileExistsAsFile(indexCandidate)) {
      return indexCandidate;
    }
  }
  return null;
}

export type ImportGraph = ReadonlyMap<string, ReadonlySet<string>>;

/** Builds a directed file->its-resolved-relative-imports graph over every source file under `rootDirs`. */
export function buildImportGraph(rootDirs: readonly string[]): ImportGraph {
  const graph = new Map<string, Set<string>>();
  const files = rootDirs.flatMap((dir) => listSourceFiles(dir));

  for (const file of files) {
    const edges = new Set<string>();
    for (const specifier of extractRelativeImportSpecifiers(file)) {
      const resolved = resolveRelativeImport(file, specifier);
      if (resolved) {
        edges.add(resolved);
      }
    }
    graph.set(file, edges);
  }
  return graph;
}

/** Every file transitively reachable from any of `roots` via the graph's edges (roots themselves included). */
export function reachableFrom(roots: readonly string[], graph: ImportGraph): Set<string> {
  const visited = new Set<string>();
  const queue = [...roots];
  while (queue.length > 0) {
    const current = queue.pop();
    if (!current || visited.has(current)) {
      continue;
    }
    visited.add(current);
    for (const next of graph.get(current) ?? []) {
      if (!visited.has(next)) {
        queue.push(next);
      }
    }
  }
  return visited;
}
