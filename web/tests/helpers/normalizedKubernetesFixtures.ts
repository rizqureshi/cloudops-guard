import type {
  NormalizedKubernetesFinding,
  NormalizedKubernetesReport,
} from "../../src/features/report-import";

/** Minimal, valid, hand-composed normalized Kubernetes finding for isolated unit/component tests. */
export function buildNormalizedKubernetesFinding(
  overrides: Partial<NormalizedKubernetesFinding> = {},
): NormalizedKubernetesFinding {
  return {
    platform: "kubernetes",
    checkId: "K8S-RES-001",
    title: "Container has no CPU request",
    severity: "medium",
    clusterContext: "test-cluster",
    namespace: "default",
    resourceKind: "Deployment",
    resourceName: "api",
    containerName: "api",
    evidence: "evidence text",
    impact: "impact text",
    recommendation: "recommendation text",
    autoRemediable: false,
    auditedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function buildNormalizedKubernetesReport(
  overrides: Partial<Omit<NormalizedKubernetesReport, "findings" | "summary">> = {},
  findings: NormalizedKubernetesFinding[] = [buildNormalizedKubernetesFinding()],
): NormalizedKubernetesReport {
  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const finding of findings) {
    counts[finding.severity] += 1;
  }
  return {
    platform: "kubernetes",
    generatedAt: "2026-01-01T00:00:00Z",
    target: {
      clusterContext: "test-cluster",
      namespaceFilter: null,
    },
    findings,
    summary: {
      ...counts,
      total: counts.critical + counts.high + counts.medium + counts.low,
    },
    ...overrides,
  };
}
