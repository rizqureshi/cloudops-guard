/**
 * Bridges a single report's findings, or a comparison's results, into one
 * uniform "workspace item" shape that the rest of `report-workspace` (and
 * `ReportWorkspace.tsx`) can filter/sort/display without needing two
 * separate code paths. `status` is `null` for single-report mode; the UI
 * only renders comparison-status controls/badges when it is not `null` for
 * every item (see `ReportWorkspace.tsx`).
 */

import type { ComparisonStatus } from "../comparison/types";
import type { NormalizedFinding } from "../report-import";
import { matchesFilters, type WorkspaceFilterState } from "./filtering";
import { sortFindings, type SortOption } from "./sorting";

export interface WorkspaceItem<T extends NormalizedFinding = NormalizedFinding> {
  readonly finding: T;
  readonly status: ComparisonStatus | null;
}

/** Extends the finding-level `SortOption` with the comparison-only "comparisonStatus" option. */
export type WorkspaceSortOption = SortOption | "comparisonStatus";

export const COMPARISON_SORT_OPTION: WorkspaceSortOption = "comparisonStatus";

const STATUS_RANK: Readonly<Record<ComparisonStatus, number>> = {
  new: 0,
  persistent: 1,
  resolved: 2,
};

export function buildSingleReportItems<T extends NormalizedFinding>(findings: readonly T[]): WorkspaceItem<T>[] {
  return findings.map((finding) => ({ finding, status: null }));
}

export function filterWorkspaceItems<T extends NormalizedFinding>(
  items: readonly WorkspaceItem<T>[],
  filters: WorkspaceFilterState,
): WorkspaceItem<T>[] {
  return items.filter(
    (item) =>
      (filters.comparisonStatus === "all" || item.status === filters.comparisonStatus) &&
      matchesFilters(item.finding, filters),
  );
}

/**
 * Sorts items by delegating to the existing `sortFindings` comparator
 * chain on the underlying findings (recovering each finding's status
 * afterward via a reference-keyed `Map`, the same technique
 * `ReportWorkspace.tsx` uses for React keys). For `"comparisonStatus"`,
 * findings are first fully ordered via the deterministic `severity` chain,
 * then a **stable** secondary sort groups them by status rank
 * (new → persistent → resolved) -- `Array.prototype.sort` has been a
 * stable sort in every supported JavaScript engine since ES2019, so the
 * full deterministic order from the first pass survives, undisturbed,
 * within each status group.
 */
export function sortWorkspaceItems<T extends NormalizedFinding>(
  items: readonly WorkspaceItem<T>[],
  sortOption: WorkspaceSortOption,
): WorkspaceItem<T>[] {
  const statusByFinding = new Map<T, ComparisonStatus | null>();
  for (const item of items) {
    statusByFinding.set(item.finding, item.status);
  }

  const findingSortOption: SortOption = sortOption === "comparisonStatus" ? "severity" : sortOption;
  const sortedFindings = sortFindings(
    items.map((item) => item.finding),
    findingSortOption,
  );
  const paired: WorkspaceItem<T>[] = sortedFindings.map((finding) => ({
    finding,
    status: statusByFinding.get(finding) ?? null,
  }));

  if (sortOption !== "comparisonStatus") {
    return paired;
  }
  return [...paired].sort(
    (a, b) => STATUS_RANK[a.status ?? "persistent"] - STATUS_RANK[b.status ?? "persistent"],
  );
}
