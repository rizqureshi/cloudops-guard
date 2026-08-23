/**
 * Pure, deterministic executive-summary calculation. No LLM, external
 * service, random ordering, current time (`Date.now()`/`new Date()`), or
 * hidden global state is used anywhere in this module -- every output is a
 * fixed function of the `NormalizedWebReport`/`ComparisonResult` passed
 * in, so calling this twice with the same input always produces the same
 * output.
 */

import type { ComparisonResult } from "../comparison/types";
import type { NormalizedFinding, NormalizedWebReport } from "../report-import";
import { deriveCategory, type FindingCategory } from "../report-workspace/category";
import { compareOrdinal, sortFindings } from "../report-workspace/sorting";
import type {
  AffectedCategory,
  ComparisonExecutiveSummary,
  ExecutiveSummaryTarget,
  RecommendationItem,
  SingleReportExecutiveSummary,
} from "./types";

const MAX_RECOMMENDATIONS = 5;

const SEVERITY_RANK: Readonly<Record<NormalizedFinding["severity"], number>> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

function resolveTargetInfo(report: NormalizedWebReport): ExecutiveSummaryTarget {
  if (report.platform === "kubernetes") {
    return { platform: "kubernetes", target: report.target };
  }
  return { platform: "gitlab", target: report.target };
}

function resolveComparisonTargetInfo(comparison: ComparisonResult): ExecutiveSummaryTarget {
  if (comparison.platform === "kubernetes") {
    return { platform: "kubernetes", target: comparison.newerReport.target };
  }
  return { platform: "gitlab", target: comparison.newerReport.target };
}

/**
 * Groups `findings` by category, tracking each category's occurrence
 * count and highest-severity member. Ordered deterministically: highest
 * severity present first, then descending occurrence count, then ordinal
 * category name -- never insertion order or any per-run-random tiebreak.
 */
function buildAffectedCategories(findings: readonly NormalizedFinding[]): AffectedCategory[] {
  const byCategory = new Map<FindingCategory, { count: number; highestSeverity: NormalizedFinding["severity"] }>();
  for (const finding of findings) {
    const category = deriveCategory(finding.checkId);
    const existing = byCategory.get(category);
    if (!existing) {
      byCategory.set(category, { count: 1, highestSeverity: finding.severity });
    } else {
      existing.count += 1;
      if (SEVERITY_RANK[finding.severity] < SEVERITY_RANK[existing.highestSeverity]) {
        existing.highestSeverity = finding.severity;
      }
    }
  }

  const categories: AffectedCategory[] = Array.from(byCategory.entries()).map(([category, info]) => ({
    category,
    count: info.count,
    highestSeverity: info.highestSeverity,
  }));

  categories.sort((a, b) => {
    const severityDiff = SEVERITY_RANK[a.highestSeverity] - SEVERITY_RANK[b.highestSeverity];
    if (severityDiff !== 0) {
      return severityDiff;
    }
    const countDiff = b.count - a.count;
    if (countDiff !== 0) {
      return countDiff;
    }
    return compareOrdinal(a.category, b.category);
  });
  return categories;
}

/**
 * Selects up to `MAX_RECOMMENDATIONS` recommendations, deterministically:
 *
 * 1. Sort eligible findings via the existing `sortFindings("severity")`
 *    disambiguation chain (severity, then check ID, then resource
 *    identity, then the full display-field tie-breaker tail) -- the same
 *    comparator the workspace uses, not a reimplementation.
 * 2. Deduplicate by exact recommendation text, keeping only each text's
 *    first (highest-priority) occurrence in that sorted order.
 * 3. First pass: walk the deduplicated, sorted candidates and take the
 *    first (highest-priority) one from each distinct category not yet
 *    represented -- this is what guarantees category diversity rather
 *    than five recommendations from the single most-affected category.
 * 4. Second pass: if fewer than five were selected, fill the remaining
 *    slots from the same deduplicated, sorted candidate list, in order,
 *    skipping ones already selected.
 */
function buildRecommendations(findings: readonly NormalizedFinding[]): RecommendationItem[] {
  const sorted = sortFindings(findings, "severity");

  const seenRecommendationText = new Set<string>();
  const deduped: RecommendationItem[] = [];
  for (const finding of sorted) {
    if (seenRecommendationText.has(finding.recommendation)) {
      continue;
    }
    seenRecommendationText.add(finding.recommendation);
    deduped.push({
      checkId: finding.checkId,
      severity: finding.severity,
      category: deriveCategory(finding.checkId),
      recommendation: finding.recommendation,
    });
  }

  const includedIndices = new Set<number>();
  const usedCategories = new Set<FindingCategory>();
  const result: RecommendationItem[] = [];

  // Every index below is bounds-checked by its loop condition
  // (`index < deduped.length`), so the non-null assertions reflect a
  // proven invariant, not an unchecked assumption.
  for (let index = 0; index < deduped.length && result.length < MAX_RECOMMENDATIONS; index += 1) {
    const candidate = deduped[index]!;
    if (!usedCategories.has(candidate.category)) {
      usedCategories.add(candidate.category);
      includedIndices.add(index);
      result.push(candidate);
    }
  }
  for (let index = 0; index < deduped.length && result.length < MAX_RECOMMENDATIONS; index += 1) {
    if (!includedIndices.has(index)) {
      result.push(deduped[index]!);
    }
  }
  return result;
}

/** Executive summary for a single, non-comparison report. */
export function buildSingleReportExecutiveSummary(report: NormalizedWebReport): SingleReportExecutiveSummary {
  return {
    mode: "single",
    targetInfo: resolveTargetInfo(report),
    generatedAt: report.generatedAt,
    summary: report.summary,
    affectedCategories: buildAffectedCategories(report.findings),
    recommendations: buildRecommendations(report.findings),
  };
}

/**
 * Executive summary for a comparison. Affected categories and
 * recommendations are derived only from "active" findings -- `new` and
 * `persistent` results -- never `resolved` ones, so a fixed problem never
 * still shows up as something to act on. `newerSummary` is the newer
 * report's own severity summary, verbatim: it is never recomputed by
 * merging in resolved findings.
 */
export function buildComparisonExecutiveSummary(comparison: ComparisonResult): ComparisonExecutiveSummary {
  const activeFindings = comparison.results
    .filter((result) => result.status === "new" || result.status === "persistent")
    .map((result) => result.displayFinding);

  return {
    mode: "comparison",
    targetInfo: resolveComparisonTargetInfo(comparison),
    olderGeneratedAt: comparison.olderReport.generatedAt,
    newerGeneratedAt: comparison.newerReport.generatedAt,
    statusTotals: comparison.statusTotals,
    newerSummary: comparison.newerReport.summary,
    affectedCategories: buildAffectedCategories(activeFindings),
    recommendations: buildRecommendations(activeFindings),
  };
}
