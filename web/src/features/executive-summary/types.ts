/**
 * Types for the deterministic, template-driven executive summary. See
 * `summary.ts` for the pure calculation and CLAUDE.md/the milestone
 * document for why this is never AI-generated: no LLM, external service,
 * random ordering, current time, or hidden global state is used anywhere
 * in this feature.
 */

import type { ComparisonStatusTotals } from "../comparison/types";
import type { NormalizedGitLabTarget, NormalizedKubernetesTarget, NormalizedSummary, Severity } from "../report-import";
import type { FindingCategory } from "../report-workspace/category";

export interface AffectedCategory {
  readonly category: FindingCategory;
  readonly count: number;
  readonly highestSeverity: Severity;
}

export interface RecommendationItem {
  readonly checkId: string;
  readonly severity: Severity;
  readonly category: FindingCategory;
  readonly recommendation: string;
}

interface ExecutiveSummaryTargetKubernetes {
  readonly platform: "kubernetes";
  readonly target: NormalizedKubernetesTarget;
}

interface ExecutiveSummaryTargetGitLab {
  readonly platform: "gitlab";
  readonly target: NormalizedGitLabTarget;
}

export type ExecutiveSummaryTarget = ExecutiveSummaryTargetKubernetes | ExecutiveSummaryTargetGitLab;

export interface SingleReportExecutiveSummary {
  readonly mode: "single";
  readonly targetInfo: ExecutiveSummaryTarget;
  readonly generatedAt: string;
  readonly summary: NormalizedSummary;
  readonly affectedCategories: readonly AffectedCategory[];
  readonly recommendations: readonly RecommendationItem[];
}

export interface ComparisonExecutiveSummary {
  readonly mode: "comparison";
  readonly targetInfo: ExecutiveSummaryTarget;
  readonly olderGeneratedAt: string;
  readonly newerGeneratedAt: string;
  readonly statusTotals: ComparisonStatusTotals;
  /** The newer report's own severity summary -- never merged with resolved findings. */
  readonly newerSummary: NormalizedSummary;
  /** Derived from active (new + persistent) findings only -- resolved findings are excluded. */
  readonly affectedCategories: readonly AffectedCategory[];
  /** Derived from active (new + persistent) findings only -- resolved findings are excluded. */
  readonly recommendations: readonly RecommendationItem[];
}

export type ExecutiveSummary = SingleReportExecutiveSummary | ComparisonExecutiveSummary;
