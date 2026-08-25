/**
 * The single, shared inventory of every public route the production build
 * emits. Every Phase 3J test that needs "all 29 routes" (the route-coverage
 * proof, the axe scan, the product-quality checks) imports this list rather
 * than maintaining its own copy, so the three can never silently drift out
 * of sync with each other.
 *
 * The 17 check-detail routes are derived from the real check-catalogue
 * data file, validated through the project's real Zod schema (the same
 * `checkCatalogueSchema` the production `catalogue.ts` module and
 * `tests/test_web_check_catalogue_contract.py`'s Python contract test both
 * rely on) -- rather than hand-listed, so a future catalogue change is
 * reflected here automatically instead of silently going untested.
 *
 * The raw JSON is imported directly with an explicit `type: "json"` import
 * attribute (rather than through `../../../src/features/check-catalogue/
 * catalogue.ts`, which imports its JSON without one) because Playwright
 * Test's Node ESM runtime -- unlike Vite/Vitest, which special-case JSON
 * imports automatically -- requires the attribute explicitly.
 */

import rawCatalogue from "../../../src/data/check-catalogue.json" with { type: "json" };
import { checkCatalogueSchema } from "../../../src/features/check-catalogue/schema";

const CHECK_CATALOGUE = checkCatalogueSchema.parse(rawCatalogue);

const STATIC_ROUTES: readonly string[] = [
  "/",
  "/demo/kubernetes",
  "/demo/gitlab",
  "/explorer",
  "/checks",
  "/roadmap",
  "/learn",
  "/learn/read-only-audits",
  "/learn/local-report-privacy",
  "/privacy",
  "/request-demo",
  "/feedback",
];

const CHECK_DETAIL_ROUTES: readonly string[] = CHECK_CATALOGUE.map((entry) => `/checks/${entry.checkId}`);

/** All 29 public routes the production build emits, in a fixed, deterministic order. */
export const PUBLIC_ROUTES: readonly string[] = [...STATIC_ROUTES, ...CHECK_DETAIL_ROUTES];

/** Routes whose primary content is derived from an *imported* report -- never scanned/exercised with report content in axe/product-quality tests beyond their empty state. */
export const REPORT_DERIVED_ROUTES: readonly string[] = ["/demo/kubernetes", "/demo/gitlab", "/explorer"];

/** Routes that hydrate the `ContactForm` island. */
export const CONTACT_ROUTES: readonly string[] = ["/request-demo", "/feedback"];

/** Expected Astro-island hydration count per route. Every route not listed here is expected to have zero islands. */
export const EXPECTED_ISLAND_COUNTS: ReadonlyMap<string, number> = new Map([
  ["/demo/kubernetes", 1],
  ["/demo/gitlab", 1],
  ["/explorer", 1],
  ["/checks", 1],
  ["/request-demo", 1],
  ["/feedback", 1],
]);
