/**
 * Pure search and filter logic over normalized findings. No hidden/raw JSON
 * is ever included in the searchable text -- only the same fields the
 * workspace UI actually displays.
 */

import type { NormalizedFinding, Severity } from "../report-import";
import { deriveCategory, type FindingCategory } from "./category";
import { compareOrdinal } from "./sorting";

export const SEVERITY_FILTER_OPTIONS: readonly Severity[] = ["critical", "high", "medium", "low"];

export interface WorkspaceFilterState {
  readonly search: string;
  readonly severity: Severity | "all";
  readonly category: FindingCategory | "all";
  readonly resourceKind: string | "all";
}

export const DEFAULT_FILTER_STATE: WorkspaceFilterState = {
  search: "",
  severity: "all",
  category: "all",
  resourceKind: "all",
};

/** The same fields the finding row/detail UI displays -- nothing hidden. */
function searchableFields(finding: NormalizedFinding): string[] {
  const fields = [
    finding.checkId,
    finding.title,
    finding.resourceKind,
    finding.resourceName,
    finding.evidence,
    finding.impact,
    finding.recommendation,
  ];
  if (finding.platform === "kubernetes") {
    fields.push(finding.namespace);
    if (finding.containerName) {
      fields.push(finding.containerName);
    }
  } else {
    fields.push(finding.projectPath);
    if (finding.jobName) {
      fields.push(finding.jobName);
    }
  }
  return fields;
}

export function matchesSearch(finding: NormalizedFinding, query: string): boolean {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) {
    return true;
  }
  return searchableFields(finding).some((field) => field.toLowerCase().includes(trimmed));
}

export function matchesFilters(finding: NormalizedFinding, filters: WorkspaceFilterState): boolean {
  if (filters.severity !== "all" && finding.severity !== filters.severity) {
    return false;
  }
  if (filters.category !== "all" && deriveCategory(finding.checkId) !== filters.category) {
    return false;
  }
  if (filters.resourceKind !== "all" && finding.resourceKind !== filters.resourceKind) {
    return false;
  }
  return matchesSearch(finding, filters.search);
}

/**
 * Generic over `T` (constrained to `NormalizedFinding`) purely so the
 * return type matches whatever concrete finding type was passed in --
 * e.g. calling this with `NormalizedKubernetesFinding[]` returns
 * `NormalizedKubernetesFinding[]`, not the wider union. `Array.prototype.
 * filter` already only selects references, never constructs new or
 * different objects, so this reflects existing behavior rather than
 * changing it.
 */
export function filterFindings<T extends NormalizedFinding>(
  findings: readonly T[],
  filters: WorkspaceFilterState,
): T[] {
  return findings.filter((finding) => matchesFilters(finding, filters));
}

/**
 * Distinct resource kinds present in `findings`, in a stable, deterministic
 * order. Uses `compareOrdinal` (plain code-unit `<`/`>`/`===`), not
 * `String.prototype.localeCompare` -- locale-aware comparison is
 * environment-dependent and not guaranteed to produce the same order
 * everywhere (see sorting.ts).
 */
export function distinctResourceKinds(findings: readonly NormalizedFinding[]): string[] {
  return Array.from(new Set(findings.map((f) => f.resourceKind))).sort(compareOrdinal);
}

/** Distinct categories present in `findings`, in a stable, deterministic order. */
export function distinctCategories(findings: readonly NormalizedFinding[]): FindingCategory[] {
  return Array.from(new Set(findings.map((f) => deriveCategory(f.checkId)))).sort(compareOrdinal);
}
