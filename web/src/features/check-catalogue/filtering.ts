/**
 * Pure search and filter logic over catalogue entries, mirroring
 * `../report-workspace/filtering.ts`'s design: plain functions, no React,
 * no hidden fields in the searchable text beyond what §5 requires (check
 * ID and title only -- not evidence/impact/recommendation, unlike the
 * report workspace's broader finding search).
 */

import { deriveCategory, type FindingCategory } from "../report-workspace/category";
import { compareOrdinal } from "../report-workspace/sorting";
import type { Severity } from "../report-import";
import type { CheckCatalogueEntry, CheckPlatform } from "./types";

export interface CatalogueFilterState {
  readonly search: string;
  readonly platform: CheckPlatform | "all";
  readonly category: FindingCategory | "all";
  readonly severity: Severity | "all";
}

export const DEFAULT_CATALOGUE_FILTER_STATE: CatalogueFilterState = {
  search: "",
  platform: "all",
  category: "all",
  severity: "all",
};

export function matchesCatalogueSearch(entry: CheckCatalogueEntry, query: string): boolean {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) {
    return true;
  }
  return entry.checkId.toLowerCase().includes(trimmed) || entry.title.toLowerCase().includes(trimmed);
}

export function matchesCatalogueFilters(entry: CheckCatalogueEntry, filters: CatalogueFilterState): boolean {
  if (filters.platform !== "all" && entry.platform !== filters.platform) {
    return false;
  }
  if (filters.severity !== "all" && entry.severity !== filters.severity) {
    return false;
  }
  if (filters.category !== "all" && deriveCategory(entry.checkId) !== filters.category) {
    return false;
  }
  return matchesCatalogueSearch(entry, filters.search);
}

export function filterCatalogueEntries(
  entries: readonly CheckCatalogueEntry[],
  filters: CatalogueFilterState,
): CheckCatalogueEntry[] {
  return entries.filter((entry) => matchesCatalogueFilters(entry, filters));
}

/** Distinct categories present in `entries`, in a stable, deterministic order. */
export function distinctCatalogueCategories(entries: readonly CheckCatalogueEntry[]): FindingCategory[] {
  return Array.from(new Set(entries.map((entry) => deriveCategory(entry.checkId)))).sort(compareOrdinal);
}
