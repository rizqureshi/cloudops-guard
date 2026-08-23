import { describe, expect, it } from "vitest";

import { compareKubernetesReports } from "../../../src/features/comparison/compare";
import { parseKubernetesReport } from "../../../src/features/report-import";
import earlierReportRaw from "../../../src/data/synthetic-kubernetes-report.json";
import laterReportRaw from "../../../src/data/synthetic-kubernetes-report-later.json";

describe("synthetic Kubernetes comparison dataset", () => {
  it("both raw reports pass parseKubernetesReport", () => {
    expect(() => parseKubernetesReport(earlierReportRaw)).not.toThrow();
    expect(() => parseKubernetesReport(laterReportRaw)).not.toThrow();
  });

  it("shares the same fictional target across both states", () => {
    const earlier = parseKubernetesReport(earlierReportRaw);
    const later = parseKubernetesReport(laterReportRaw);
    expect(later.target).toEqual(earlier.target);
    expect(earlier.target).toEqual({ clusterContext: "cloudops-guard-demo-cluster", namespaceFilter: null });
  });

  it("the later report's generated timestamp is strictly later than the earlier report's", () => {
    const earlier = parseKubernetesReport(earlierReportRaw);
    const later = parseKubernetesReport(laterReportRaw);
    expect(Date.parse(later.generatedAt)).toBeGreaterThan(Date.parse(earlier.generatedAt));
  });

  it("does not fabricate a Critical-severity finding in either state", () => {
    for (const raw of [earlierReportRaw, laterReportRaw]) {
      const report = parseKubernetesReport(raw);
      expect(report.summary.critical).toBe(0);
    }
  });

  it("covers all six implemented Kubernetes checks across the two states", () => {
    const earlier = parseKubernetesReport(earlierReportRaw);
    const later = parseKubernetesReport(laterReportRaw);
    const presentCheckIds = new Set([
      ...earlier.findings.map((f) => f.checkId),
      ...later.findings.map((f) => f.checkId),
    ]);
    for (const checkId of ["K8S-RES-001", "K8S-RES-002", "K8S-RES-003", "K8S-RES-004", "K8S-IMG-001", "K8S-REL-001"]) {
      expect(presentCheckIds.has(checkId)).toBe(true);
    }
  });

  it("produces at least one New, one Persistent, and one Resolved result when compared", () => {
    const earlier = parseKubernetesReport(earlierReportRaw);
    const later = parseKubernetesReport(laterReportRaw);
    const comparison = compareKubernetesReports(earlier, later);

    expect(comparison.statusTotals.new).toBeGreaterThanOrEqual(1);
    expect(comparison.statusTotals.persistent).toBeGreaterThanOrEqual(1);
    expect(comparison.statusTotals.resolved).toBeGreaterThanOrEqual(1);
    // Exact totals, recorded so a future accidental data edit is caught.
    expect(comparison.statusTotals).toEqual({ new: 2, persistent: 3, resolved: 6 });
  });
});
