import { describe, expect, it } from "vitest";

import { compareKubernetesReports } from "../../../src/features/comparison/compare";
import {
  buildComparisonExecutiveSummary,
  buildSingleReportExecutiveSummary,
} from "../../../src/features/executive-summary/summary";
import { buildNormalizedGitLabFinding, buildNormalizedGitLabReport } from "../../helpers/normalizedGitLabFixtures";
import {
  buildNormalizedKubernetesFinding,
  buildNormalizedKubernetesReport,
} from "../../helpers/normalizedKubernetesFixtures";

describe("buildSingleReportExecutiveSummary: identity and totals", () => {
  it("renders Kubernetes target identity correctly", () => {
    const report = buildNormalizedKubernetesReport({
      target: { clusterContext: "demo-cluster", namespaceFilter: "payments" },
    });
    const summary = buildSingleReportExecutiveSummary(report);
    expect(summary.targetInfo).toEqual({
      platform: "kubernetes",
      target: { clusterContext: "demo-cluster", namespaceFilter: "payments" },
    });
  });

  it("renders GitLab target identity correctly", () => {
    const report = buildNormalizedGitLabReport({
      target: {
        gitlabUrl: "https://gitlab.example.com",
        projectId: 42,
        projectPath: "team/project",
        defaultBranch: "main",
      },
    });
    const summary = buildSingleReportExecutiveSummary(report);
    expect(summary.targetInfo).toEqual({
      platform: "gitlab",
      target: {
        gitlabUrl: "https://gitlab.example.com",
        projectId: 42,
        projectPath: "team/project",
        defaultBranch: "main",
      },
    });
  });

  it("single-report totals match the normalized report's own summary exactly", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", severity: "medium" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-IMG-001", severity: "high", resourceName: "b" }),
    ];
    const report = buildNormalizedKubernetesReport({}, findings);
    const summary = buildSingleReportExecutiveSummary(report);
    expect(summary.summary).toEqual(report.summary);
  });
});

describe("buildComparisonExecutiveSummary: totals separation", () => {
  const persistentFinding = buildNormalizedKubernetesFinding({
    checkId: "K8S-RES-001",
    severity: "medium",
    namespace: "payments-demo",
    resourceName: "checkout-api",
    recommendation: "recommendation: persistent",
  });
  const resolvedFinding = buildNormalizedKubernetesFinding({
    checkId: "K8S-RES-004",
    severity: "high",
    namespace: "payments-demo",
    resourceName: "checkout-api",
    recommendation: "recommendation: resolved",
  });
  const newFinding = buildNormalizedKubernetesFinding({
    checkId: "K8S-REL-001",
    severity: "high",
    namespace: "commerce-demo",
    resourceName: "cache-pod",
    recommendation: "recommendation: new",
  });

  const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" }, [
    persistentFinding,
    resolvedFinding,
  ]);
  const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" }, [
    persistentFinding,
    newFinding,
  ]);
  const comparison = compareKubernetesReports(older, newer);

  it("computes correct status totals", () => {
    const summary = buildComparisonExecutiveSummary(comparison);
    expect(summary.statusTotals).toEqual({ new: 1, persistent: 1, resolved: 1 });
  });

  it("newerSummary equals the newer report's own severity summary, never merged with resolved findings", () => {
    const summary = buildComparisonExecutiveSummary(comparison);
    expect(summary.newerSummary).toEqual(newer.summary);
    // newer.summary counts only persistentFinding (medium) + newFinding
    // (high) -- resolvedFinding (high) must not be folded in, so high
    // stays 1, not 2.
    expect(summary.newerSummary).toEqual({ critical: 0, high: 1, medium: 1, low: 0, total: 2 });
  });

  it("excludes resolved findings from affected categories and recommendations", () => {
    const summary = buildComparisonExecutiveSummary(comparison);
    const recommendationTexts = summary.recommendations.map((r) => r.recommendation);
    expect(recommendationTexts).not.toContain(resolvedFinding.recommendation);
    expect(recommendationTexts).toContain(persistentFinding.recommendation);
    expect(recommendationTexts).toContain(newFinding.recommendation);

    const totalCategoryCount = summary.affectedCategories.reduce((sum, c) => sum + c.count, 0);
    expect(totalCategoryCount).toBe(2); // persistent + new only, not resolved
  });
});

describe("buildAffectedCategories (via buildSingleReportExecutiveSummary): deterministic ordering", () => {
  it("orders by highest severity present, then descending count, then ordinal category name", () => {
    const findings = [
      // Resource management: 2 findings, highest severity medium
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", severity: "medium", resourceName: "a" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-002", severity: "medium", resourceName: "b" }),
      // Image security: 1 finding, severity high
      buildNormalizedKubernetesFinding({ checkId: "K8S-IMG-001", severity: "high", resourceName: "c" }),
      // Reliability: 1 finding, severity high
      buildNormalizedKubernetesFinding({ checkId: "K8S-REL-001", severity: "high", resourceName: "d" }),
    ];
    const report = buildNormalizedKubernetesReport({}, findings);
    const summary = buildSingleReportExecutiveSummary(report);

    // High-severity categories first (Image security, Reliability -- tied
    // on severity and count, so ordinal name breaks the tie: "Image
    // security" < "Reliability"), then medium-severity Resource management.
    expect(summary.affectedCategories.map((c) => c.category)).toEqual([
      "Image security",
      "Reliability",
      "Resource management",
    ]);
  });

  it("produces identical results regardless of input finding order", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", severity: "medium", resourceName: "a" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-IMG-001", severity: "high", resourceName: "b" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-REL-001", severity: "high", resourceName: "c" }),
    ];
    const forward = buildSingleReportExecutiveSummary(buildNormalizedKubernetesReport({}, findings));
    const shuffled = buildSingleReportExecutiveSummary(
      buildNormalizedKubernetesReport({}, [findings[2]!, findings[0]!, findings[1]!]),
    );
    expect(shuffled.affectedCategories).toEqual(forward.affectedCategories);
    expect(shuffled.recommendations).toEqual(forward.recommendations);
  });
});

describe("buildRecommendations (via buildSingleReportExecutiveSummary): diversity, dedup, and limit", () => {
  it("selects the highest-priority recommendation from each distinct category before repeating a category", () => {
    const findings = [
      buildNormalizedKubernetesFinding({
        checkId: "K8S-RES-001",
        severity: "low",
        resourceName: "a",
        recommendation: "res-low",
      }),
      buildNormalizedKubernetesFinding({
        checkId: "K8S-RES-002",
        severity: "high",
        resourceName: "b",
        recommendation: "res-high",
      }),
      buildNormalizedKubernetesFinding({
        checkId: "K8S-IMG-001",
        severity: "medium",
        resourceName: "c",
        recommendation: "img-medium",
      }),
    ];
    const summary = buildSingleReportExecutiveSummary(buildNormalizedKubernetesReport({}, findings));

    // Sorted by severity first: res-high (high), img-medium (medium),
    // res-low (low). Category-diversity pass takes res-high (Resource
    // management) and img-medium (Image security) first; res-low would be
    // a second Resource-management entry, so it's only included in the
    // fill pass if there is room.
    expect(summary.recommendations.map((r) => r.recommendation)).toEqual(["res-high", "img-medium", "res-low"]);
  });

  it("deduplicates identical recommendation text even when multiple resources trigger the same check", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", resourceName: "a", recommendation: "shared" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", resourceName: "b", recommendation: "shared" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", resourceName: "c", recommendation: "shared" }),
    ];
    const summary = buildSingleReportExecutiveSummary(buildNormalizedKubernetesReport({}, findings));
    expect(summary.recommendations).toHaveLength(1);
    expect(summary.recommendations[0]!.recommendation).toBe("shared");
  });

  it("never exceeds five recommendations", () => {
    const findings = Array.from({ length: 8 }, (_, i) =>
      buildNormalizedKubernetesFinding({
        checkId: `K8S-RES-00${(i % 4) + 1}`,
        resourceName: `resource-${i}`,
        recommendation: `recommendation-${i}`,
      }),
    );
    const summary = buildSingleReportExecutiveSummary(buildNormalizedKubernetesReport({}, findings));
    expect(summary.recommendations.length).toBeLessThanOrEqual(5);
  });

  it("produces identical recommendations regardless of input finding order", () => {
    const findings = [
      buildNormalizedGitLabFinding({ checkId: "GL-BR-001", severity: "high", recommendation: "r1" }),
      buildNormalizedGitLabFinding({ checkId: "GL-MR-001", severity: "medium", recommendation: "r2" }),
      buildNormalizedGitLabFinding({ checkId: "GL-SEC-001", severity: "high", recommendation: "r3" }),
      buildNormalizedGitLabFinding({ checkId: "GL-COST-001", severity: "low", recommendation: "r4" }),
    ];
    const forward = buildSingleReportExecutiveSummary(buildNormalizedGitLabReport({}, findings));
    const shuffled = buildSingleReportExecutiveSummary(
      buildNormalizedGitLabReport({}, [findings[3]!, findings[1]!, findings[2]!, findings[0]!]),
    );
    expect(shuffled.recommendations).toEqual(forward.recommendations);
  });
});

describe("zero-findings behavior", () => {
  it("returns empty affected-categories and recommendations for a zero-finding report, with no positive health claim in the data itself", () => {
    const report = buildNormalizedKubernetesReport({}, []);
    const summary = buildSingleReportExecutiveSummary(report);
    expect(summary.affectedCategories).toEqual([]);
    expect(summary.recommendations).toEqual([]);
    expect(summary.summary).toEqual({ critical: 0, high: 0, medium: 0, low: 0, total: 0 });
  });

  it("does not include any health/risk/maturity/completeness score field on the returned object", () => {
    const summary = buildSingleReportExecutiveSummary(buildNormalizedKubernetesReport({}, []));
    const keys = Object.keys(summary);
    for (const forbidden of ["healthScore", "riskScore", "maturityScore", "score", "grade", "rating"]) {
      expect(keys).not.toContain(forbidden);
    }
  });
});
