import { expect, test } from "@playwright/test";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { PUBLIC_ROUTES } from "./support/routes";

/**
 * A route-coverage proof, not a functional test: it never opens a browser
 * page. It reads the real, already-produced `dist/` directory on disk (the
 * same production build `astro preview` serves for every other Phase 3J
 * spec) and compares its actual set of `index.html`-producing routes
 * against `PUBLIC_ROUTES` -- the one list every other Phase 3J test
 * (accessibility, product-quality) also imports.
 *
 * This makes the coverage claim self-verifying rather than asserted only in
 * a document: if a route is added to the site without being added to
 * `PUBLIC_ROUTES`, this test fails (an untested route exists). If a route
 * is removed from the site without `PUBLIC_ROUTES` being updated, this
 * test also fails (an expected route disappeared) -- it does not merely
 * warn.
 *
 * No sitemap, route, or production runtime feature was added to make this
 * possible -- it walks the static build output that already exists.
 */

const DIST_DIR = fileURLToPath(new URL("../../dist", import.meta.url));

function collectBuiltRoutes(dir: string, base = ""): string[] {
  const routes: string[] = [];
  for (const entryName of readdirSync(dir)) {
    const entryPath = join(dir, entryName);
    const stat = statSync(entryPath);
    if (stat.isDirectory()) {
      routes.push(...collectBuiltRoutes(entryPath, `${base}/${entryName}`));
    } else if (entryName === "index.html") {
      routes.push(base === "" ? "/" : base);
    }
  }
  return routes;
}

test.describe("route inventory: production build coverage proof", () => {
  test("the actual dist/**/index.html route set matches the shared PUBLIC_ROUTES inventory exactly", () => {
    const builtRoutes = collectBuiltRoutes(DIST_DIR).sort();
    const expectedRoutes = [...PUBLIC_ROUTES].sort();

    const untested = builtRoutes.filter((route) => !expectedRoutes.includes(route));
    const missing = expectedRoutes.filter((route) => !builtRoutes.includes(route));

    expect(untested, `Built route(s) not present in PUBLIC_ROUTES (untested): ${JSON.stringify(untested)}`).toEqual(
      [],
    );
    expect(missing, `Expected route(s) missing from the production build: ${JSON.stringify(missing)}`).toEqual([]);
    expect(builtRoutes.length).toBe(29);
  });
});
