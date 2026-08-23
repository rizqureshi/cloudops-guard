/**
 * Timestamp and target-compatibility validation for a comparison, run
 * before any matching happens. Every rejection is a sanitized
 * `ComparisonError` (see `errors.ts`) -- never a message reproducing a
 * report-supplied value.
 */

import type { NormalizedGitLabReport, NormalizedKubernetesReport, NormalizedWebReport } from "../report-import";
import { ComparisonError } from "./errors";

/**
 * Compares timestamps as instants (`Date.parse`), never lexicographically,
 * so two differently formatted strings representing the same instant are
 * correctly treated as equal (and rejected), not as "different" merely
 * because their characters differ. Rejects equal instants and reversed
 * order alike -- only a strictly later `newerIso` passes.
 */
function assertStrictlyIncreasingInstant(olderIso: string, newerIso: string): void {
  const olderInstant = Date.parse(olderIso);
  const newerInstant = Date.parse(newerIso);
  if (!(olderInstant < newerInstant)) {
    throw new ComparisonError("non_positive_time_range");
  }
}

/** Exact equality only, including `null` vs. a named namespace. */
function assertKubernetesTargetsCompatible(
  older: NormalizedKubernetesReport,
  newer: NormalizedKubernetesReport,
): void {
  if (
    older.target.clusterContext !== newer.target.clusterContext ||
    older.target.namespaceFilter !== newer.target.namespaceFilter
  ) {
    throw new ComparisonError("incompatible_target");
  }
}

/**
 * Exact equality on `gitlabUrl`, `projectId`, `projectPath` only.
 * `defaultBranch` is deliberately excluded -- it is not part of the
 * approved GitLab target-compatibility rule. The URL is compared only as
 * an opaque string: never normalized, parsed, lowercased, resolved, or
 * used to build a navigation target.
 */
function assertGitLabTargetsCompatible(older: NormalizedGitLabReport, newer: NormalizedGitLabReport): void {
  if (
    older.target.gitlabUrl !== newer.target.gitlabUrl ||
    older.target.projectId !== newer.target.projectId ||
    older.target.projectPath !== newer.target.projectPath
  ) {
    throw new ComparisonError("incompatible_target");
  }
}

/**
 * Validates that `olderReport`/`newerReport` may be compared: same
 * platform, a strictly later `newerReport.generatedAt`, and a compatible
 * target. Throws a sanitized `ComparisonError` on the first failing check;
 * never reorders the reports itself -- the `older`/`newer` contract is the
 * caller's responsibility (see `compare.ts`).
 */
export function assertComparable(olderReport: NormalizedWebReport, newerReport: NormalizedWebReport): void {
  if (olderReport.platform !== newerReport.platform) {
    throw new ComparisonError("mixed_platform");
  }
  assertStrictlyIncreasingInstant(olderReport.generatedAt, newerReport.generatedAt);
  if (olderReport.platform === "kubernetes" && newerReport.platform === "kubernetes") {
    assertKubernetesTargetsCompatible(olderReport, newerReport);
  } else if (olderReport.platform === "gitlab" && newerReport.platform === "gitlab") {
    assertGitLabTargetsCompatible(olderReport, newerReport);
  }
}
