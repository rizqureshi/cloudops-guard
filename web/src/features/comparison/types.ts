/**
 * Browser-only comparison types. These describe a purely client-side
 * derivation over two already-normalized `NormalizedWebReport`s -- they
 * have no relationship to any released Python report contract, are never
 * written back into a file, and are never sent to a server. Kept in their
 * own feature folder, separate from `../report-import` (the normalized
 * report representation itself) and `../report-workspace` (single-report
 * display), so neither of those needs to know comparison exists.
 */

import type {
  NormalizedGitLabFinding,
  NormalizedGitLabReport,
  NormalizedKubernetesFinding,
  NormalizedKubernetesReport,
} from "../report-import";

export type ComparisonStatus = "new" | "persistent" | "resolved";

/** Fixed Phase 3F UI ordering -- see sorting/filtering in report-workspace. */
export const COMPARISON_STATUS_ORDER: readonly ComparisonStatus[] = ["new", "persistent", "resolved"];

/**
 * One matched or unmatched occurrence from the comparison's multiset
 * matching (see `compare.ts`). `displayFinding` is which occurrence the UI
 * shows: the newer occurrence for `new`/`persistent`, the older occurrence
 * for `resolved`. `olderFinding`/`newerFinding` retain both occurrences
 * (when they exist) for a `persistent` result, but Phase 3F does not
 * perform field-by-field evidence or severity change tracking between
 * them -- they are exposed only so a future phase could, without this
 * phase claiming that capability.
 */
export interface ComparisonFindingResult<TFinding> {
  readonly status: ComparisonStatus;
  readonly displayFinding: TFinding;
  readonly olderFinding: TFinding | null;
  readonly newerFinding: TFinding | null;
}

export interface ComparisonStatusTotals {
  readonly new: number;
  readonly persistent: number;
  readonly resolved: number;
}

export interface KubernetesComparisonResult {
  readonly platform: "kubernetes";
  readonly olderReport: NormalizedKubernetesReport;
  readonly newerReport: NormalizedKubernetesReport;
  readonly results: readonly ComparisonFindingResult<NormalizedKubernetesFinding>[];
  readonly statusTotals: ComparisonStatusTotals;
}

export interface GitLabComparisonResult {
  readonly platform: "gitlab";
  readonly olderReport: NormalizedGitLabReport;
  readonly newerReport: NormalizedGitLabReport;
  readonly results: readonly ComparisonFindingResult<NormalizedGitLabFinding>[];
  readonly statusTotals: ComparisonStatusTotals;
}

/** Discriminated on `platform`, same convention as `NormalizedWebReport`. */
export type ComparisonResult = KubernetesComparisonResult | GitLabComparisonResult;
