/**
 * Minimal, valid, hand-composed raw report/finding objects for isolated
 * unit tests -- deliberately synthetic and small, mirroring the Python
 * project's own `tests/fixtures/builders.py` convention of building real,
 * complete objects rather than ad hoc partial stand-ins. These are for
 * unit-level dispatch/validation/limit tests; the full-fixture behavioral
 * tests in golden-fixtures.test.ts use the two real repository-root golden
 * fixtures instead.
 */

export function buildKubernetesFinding(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    check_id: "K8S-RES-001",
    title: "Container has no CPU request",
    severity: "medium",
    cluster_context: "test-cluster",
    namespace: "default",
    resource_kind: "Deployment",
    resource_name: "api",
    container_name: "api",
    evidence: "evidence text",
    impact: "impact text",
    recommendation: "recommendation text",
    auto_remediable: false,
    audited_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function buildKubernetesReport(
  overrides: Record<string, unknown> = {},
  findings: Record<string, unknown>[] = [buildKubernetesFinding()],
): Record<string, unknown> {
  const summary = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const finding of findings) {
    const severity = finding.severity as keyof typeof summary;
    if (severity in summary) {
      summary[severity] += 1;
    }
  }
  return {
    cluster_context: "test-cluster",
    namespace_filter: null,
    generated_at: "2026-01-01T00:00:00Z",
    findings,
    summary,
    ...overrides,
  };
}

export function buildGitLabFinding(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    check_id: "GL-BR-001",
    title: "Default branch is not protected",
    severity: "high",
    project_path: "group/project",
    resource_kind: "ProtectedBranch",
    resource_name: "main",
    job_name: null,
    evidence: "evidence text",
    impact: "impact text",
    recommendation: "recommendation text",
    auto_remediable: false,
    audited_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function buildGitLabReport(
  overrides: Record<string, unknown> = {},
  findings: Record<string, unknown>[] = [buildGitLabFinding()],
): Record<string, unknown> {
  const summary = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const finding of findings) {
    const severity = finding.severity as keyof typeof summary;
    if (severity in summary) {
      summary[severity] += 1;
    }
  }
  return {
    platform: "gitlab",
    gitlab_url: "https://gitlab.example.com",
    project_id: 1,
    project_path: "group/project",
    default_branch: "main",
    generated_at: "2026-01-01T00:00:00Z",
    findings,
    summary,
    ...overrides,
  };
}
