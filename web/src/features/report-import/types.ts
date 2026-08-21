/**
 * The browser-only normalized report representation produced by
 * `parseKubernetesReport`/`parseGitLabReport`/`parseReport`.
 *
 * `NormalizedWebReport` is not a new CloudOps Guard CLI report format: it
 * has no relationship to `report.json`'s on-disk schema beyond being
 * derived from it, is never written back into a file, and is never sent to
 * a server (see docs/milestones/v0.3.0-interactive-web-demo.md, section E).
 * Every field name here uses camelCase, distinct from the snake_case
 * on-the-wire JSON contract in `schemas.ts`.
 */

export type Severity = "critical" | "high" | "medium" | "low";

export type KubernetesResourceKind = "Namespace" | "Pod" | "Deployment";

export type GitLabResourceKind = "Project" | "ProtectedBranch" | "CIJob" | "CIService";

/** Recomputed (never trusted-as-given) severity counts, plus a derived total. */
export interface NormalizedSummary {
  readonly critical: number;
  readonly high: number;
  readonly medium: number;
  readonly low: number;
  readonly total: number;
}

export interface NormalizedKubernetesFinding {
  readonly platform: "kubernetes";
  readonly checkId: string;
  readonly title: string;
  readonly severity: Severity;
  readonly clusterContext: string;
  readonly namespace: string;
  readonly resourceKind: KubernetesResourceKind;
  readonly resourceName: string;
  readonly containerName: string | null;
  readonly evidence: string;
  readonly impact: string;
  readonly recommendation: string;
  readonly autoRemediable: boolean;
  readonly auditedAt: string;
}

export interface NormalizedGitLabFinding {
  readonly platform: "gitlab";
  readonly checkId: string;
  readonly title: string;
  readonly severity: Severity;
  readonly projectPath: string;
  readonly resourceKind: GitLabResourceKind;
  readonly resourceName: string;
  readonly jobName: string | null;
  readonly evidence: string;
  readonly impact: string;
  readonly recommendation: string;
  readonly autoRemediable: boolean;
  readonly auditedAt: string;
}

/** Discriminated on `platform`, same as the report-level union below. */
export type NormalizedFinding = NormalizedKubernetesFinding | NormalizedGitLabFinding;

export interface NormalizedKubernetesTarget {
  readonly clusterContext: string;
  readonly namespaceFilter: string | null;
}

export interface NormalizedGitLabTarget {
  readonly gitlabUrl: string;
  readonly projectId: number;
  readonly projectPath: string;
  readonly defaultBranch: string;
}

export interface NormalizedKubernetesReport {
  readonly platform: "kubernetes";
  readonly generatedAt: string;
  readonly target: NormalizedKubernetesTarget;
  readonly findings: readonly NormalizedKubernetesFinding[];
  readonly summary: NormalizedSummary;
}

export interface NormalizedGitLabReport {
  readonly platform: "gitlab";
  readonly generatedAt: string;
  readonly target: NormalizedGitLabTarget;
  readonly findings: readonly NormalizedGitLabFinding[];
  readonly summary: NormalizedSummary;
}

/** Discriminated on `platform` -- the type every parser in this module returns. */
export type NormalizedWebReport = NormalizedKubernetesReport | NormalizedGitLabReport;
