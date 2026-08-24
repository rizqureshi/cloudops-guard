/**
 * Pure, deterministic multiset matching between two same-platform finding
 * arrays, producing new/persistent/resolved results.
 *
 * Grouping is done with a `Map<Fingerprint, T[]>`, built in one pass over
 * each input array -- O(n) in the combined finding count, not O(n^2), so
 * this remains suitable as reports approach the existing 10,000-finding
 * limit. Within a fingerprint group, older and newer occurrences are
 * deterministically ordered via the existing `sortFindings` disambiguation
 * chain (fingerprint fields are, by construction, identical within a
 * group -- checkId, resource identity -- so this only exercises the
 * chain's tie-breaking tail: title/evidence/impact/recommendation/
 * auditedAt/autoRemediable) and paired index-by-index:
 * `min(olderCount, newerCount)` pairs become `persistent`, any leftover
 * older occurrences become `resolved`, any leftover newer occurrences
 * become `new`. Every occurrence in both inputs produces exactly one
 * result -- duplicates are never collapsed.
 *
 * The set of fingerprints itself is sorted with `compareOrdinal` before
 * iterating, so the returned `results` array's order depends only on
 * finding *content*, never on the original arrays' element order --
 * reversing or shuffling either input produces the same classification
 * and the same `results` order.
 */

import type {
  NormalizedFinding,
  NormalizedGitLabReport,
  NormalizedKubernetesReport,
  NormalizedWebReport,
} from "../report-import";
import { compareOrdinal, sortFindings } from "../report-workspace/sorting";
import { ComparisonError } from "./errors";
import { computeFingerprint, type Fingerprint } from "./fingerprint";
import type {
  ComparisonFindingResult,
  ComparisonResult,
  ComparisonStatusTotals,
  GitLabComparisonResult,
  KubernetesComparisonResult,
} from "./types";
import { assertComparable } from "./validation";

function groupByFingerprint<T extends NormalizedFinding>(findings: readonly T[]): Map<Fingerprint, T[]> {
  const groups = new Map<Fingerprint, T[]>();
  for (const finding of findings) {
    const fingerprint = computeFingerprint(finding);
    const group = groups.get(fingerprint);
    if (group) {
      group.push(finding);
    } else {
      groups.set(fingerprint, [finding]);
    }
  }
  return groups;
}

function matchFindings<T extends NormalizedFinding>(
  olderFindings: readonly T[],
  newerFindings: readonly T[],
): ComparisonFindingResult<T>[] {
  const olderGroups = groupByFingerprint(olderFindings);
  const newerGroups = groupByFingerprint(newerFindings);
  const fingerprints = Array.from(new Set([...olderGroups.keys(), ...newerGroups.keys()])).sort(compareOrdinal);

  const results: ComparisonFindingResult<T>[] = [];
  for (const fingerprint of fingerprints) {
    // Arbitrary sort option: within one fingerprint group, checkId and
    // resource identity are already identical, so any option exercises
    // the same deterministic disambiguation tail.
    const olderGroup = sortFindings(olderGroups.get(fingerprint) ?? [], "checkId");
    const newerGroup = sortFindings(newerGroups.get(fingerprint) ?? [], "checkId");
    const pairCount = Math.min(olderGroup.length, newerGroup.length);

    // Every index below is bounds-checked by its loop condition
    // (`pairCount`/`.length`), so the non-null assertions reflect a proven
    // invariant, not an unchecked assumption -- `noUncheckedIndexedAccess`
    // cannot see that itself.
    for (let index = 0; index < pairCount; index += 1) {
      results.push({
        status: "persistent",
        displayFinding: newerGroup[index]!,
        olderFinding: olderGroup[index]!,
        newerFinding: newerGroup[index]!,
      });
    }
    for (let index = pairCount; index < olderGroup.length; index += 1) {
      results.push({
        status: "resolved",
        displayFinding: olderGroup[index]!,
        olderFinding: olderGroup[index]!,
        newerFinding: null,
      });
    }
    for (let index = pairCount; index < newerGroup.length; index += 1) {
      results.push({
        status: "new",
        displayFinding: newerGroup[index]!,
        olderFinding: null,
        newerFinding: newerGroup[index]!,
      });
    }
  }
  return results;
}

function computeStatusTotals<T extends NormalizedFinding>(
  results: readonly ComparisonFindingResult<T>[],
): ComparisonStatusTotals {
  let newCount = 0;
  let persistentCount = 0;
  let resolvedCount = 0;
  for (const result of results) {
    if (result.status === "new") {
      newCount += 1;
    } else if (result.status === "persistent") {
      persistentCount += 1;
    } else {
      resolvedCount += 1;
    }
  }
  return { new: newCount, persistent: persistentCount, resolved: resolvedCount };
}

/**
 * Compares two Kubernetes reports. `olderReport`/`newerReport` is an
 * explicit contract, never inferred or silently reordered by timestamp --
 * a caller passing them backwards gets a `ComparisonError`
 * (`non_positive_time_range`), not a silently-swapped comparison. Neither
 * input report or its findings is mutated.
 */
export function compareKubernetesReports(
  olderReport: NormalizedKubernetesReport,
  newerReport: NormalizedKubernetesReport,
): KubernetesComparisonResult {
  assertComparable(olderReport, newerReport);
  const results = matchFindings(olderReport.findings, newerReport.findings);
  return {
    platform: "kubernetes",
    olderReport,
    newerReport,
    results,
    statusTotals: computeStatusTotals(results),
  };
}

/** Compares two GitLab reports. See `compareKubernetesReports` for the contract. */
export function compareGitLabReports(
  olderReport: NormalizedGitLabReport,
  newerReport: NormalizedGitLabReport,
): GitLabComparisonResult {
  assertComparable(olderReport, newerReport);
  const results = matchFindings(olderReport.findings, newerReport.findings);
  return {
    platform: "gitlab",
    olderReport,
    newerReport,
    results,
    statusTotals: computeStatusTotals(results),
  };
}

/**
 * Picks the platform-appropriate comparator from the reports' own
 * `platform` field, rather than requiring the caller to already know which
 * platform it has. Shared by `DemoController` (Phase 3F) and the local
 * report explorer (Phase 3G) so the platform-dispatch logic exists exactly
 * once. Pure and deterministic: receives no file, filename, DOM, storage,
 * or network object -- only the two already-normalized reports -- and
 * either returns a `ComparisonResult` or throws the same sanitized
 * `ComparisonError` (`mixed_platform`) that `assertComparable` would.
 *
 * A function value cannot survive Astro's island-props JSON serialization
 * for `client:load` hydration (verified directly during Phase 3F: an
 * earlier draft that accepted a `compare` function as an island prop
 * produced `"compare":[0,null]` in the built page's serialized props,
 * which would throw at runtime post-hydration) -- so callers must import
 * this function directly rather than receiving it as a prop from an
 * `.astro` page.
 */
export function compareReports(older: NormalizedWebReport, newer: NormalizedWebReport): ComparisonResult {
  if (older.platform === "kubernetes") {
    if (newer.platform !== "kubernetes") {
      throw new ComparisonError("mixed_platform");
    }
    return compareKubernetesReports(older, newer);
  }
  if (newer.platform !== "gitlab") {
    throw new ComparisonError("mixed_platform");
  }
  return compareGitLabReports(older, newer);
}
