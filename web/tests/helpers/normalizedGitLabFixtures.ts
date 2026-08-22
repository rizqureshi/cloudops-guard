import type { NormalizedGitLabFinding, NormalizedGitLabReport } from "../../src/features/report-import";

/** Minimal, valid, hand-composed normalized GitLab finding for isolated unit/component tests. */
export function buildNormalizedGitLabFinding(
  overrides: Partial<NormalizedGitLabFinding> = {},
): NormalizedGitLabFinding {
  return {
    platform: "gitlab",
    checkId: "GL-BR-001",
    title: "Default branch is not protected",
    severity: "high",
    projectPath: "test-group/test-project",
    resourceKind: "ProtectedBranch",
    resourceName: "main",
    jobName: null,
    evidence: "evidence text",
    impact: "impact text",
    recommendation: "recommendation text",
    autoRemediable: false,
    auditedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function buildNormalizedGitLabReport(
  overrides: Partial<Omit<NormalizedGitLabReport, "findings" | "summary">> = {},
  findings: NormalizedGitLabFinding[] = [buildNormalizedGitLabFinding()],
): NormalizedGitLabReport {
  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const finding of findings) {
    counts[finding.severity] += 1;
  }
  return {
    platform: "gitlab",
    generatedAt: "2026-01-01T00:00:00Z",
    target: {
      gitlabUrl: "https://gitlab.example.com",
      projectId: 1000,
      projectPath: "test-group/test-project",
      defaultBranch: "main",
    },
    findings,
    summary: {
      ...counts,
      total: counts.critical + counts.high + counts.medium + counts.low,
    },
    ...overrides,
  };
}
