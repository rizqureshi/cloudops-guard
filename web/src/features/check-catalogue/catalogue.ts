/**
 * Loads and validates the project-owned check catalogue at module-evaluation
 * time (build time for any `.astro` page, test-collection time for Vitest),
 * so malformed catalogue data fails loudly rather than reaching either the
 * production build or the running site.
 *
 * `CHECK_CATALOGUE` is exposed already sorted in deterministic check-ID
 * order, via the same `compareOrdinal` (plain code-unit comparison, never
 * `String.prototype.localeCompare`) the report workspace uses for every
 * other stable ordering in this codebase -- see
 * `../report-workspace/sorting.ts`.
 */

import rawCatalogue from "../../data/check-catalogue.json";
import { compareOrdinal } from "../report-workspace/sorting";
import { checkCatalogueSchema } from "./schema";
import type { CheckCatalogueEntry } from "./types";

function loadCatalogue(): readonly CheckCatalogueEntry[] {
  const result = checkCatalogueSchema.safeParse(rawCatalogue);
  if (!result.success) {
    throw new Error(`Invalid check catalogue data (web/src/data/check-catalogue.json): ${result.error.message}`);
  }
  return [...result.data].sort((a, b) => compareOrdinal(a.checkId, b.checkId));
}

export const CHECK_CATALOGUE: readonly CheckCatalogueEntry[] = loadCatalogue();

const CATALOGUE_BY_ID: ReadonlyMap<string, CheckCatalogueEntry> = new Map(
  CHECK_CATALOGUE.map((entry) => [entry.checkId, entry]),
);

/** `undefined` for any check ID not present in the catalogue -- never throws. */
export function findCatalogueEntry(checkId: string): CheckCatalogueEntry | undefined {
  return CATALOGUE_BY_ID.get(checkId);
}
