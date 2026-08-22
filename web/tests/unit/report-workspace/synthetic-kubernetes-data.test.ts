import { describe, expect, it } from "vitest";

import { parseKubernetesReport } from "../../../src/features/report-import";
import syntheticKubernetesReportRaw from "../../../src/data/synthetic-kubernetes-report.json";

const IMPLEMENTED_KUBERNETES_CHECK_IDS = [
  "K8S-RES-001",
  "K8S-RES-002",
  "K8S-RES-003",
  "K8S-RES-004",
  "K8S-IMG-001",
  "K8S-REL-001",
] as const;

describe("synthetic Kubernetes demo dataset", () => {
  it("passes through parseKubernetesReport without modification bypassing validation", () => {
    expect(() => parseKubernetesReport(syntheticKubernetesReportRaw)).not.toThrow();
  });

  it("has no platform field on the raw dataset", () => {
    expect(Object.hasOwn(syntheticKubernetesReportRaw, "platform")).toBe(false);
  });

  it("contains every currently implemented Kubernetes check ID at least once", () => {
    const report = parseKubernetesReport(syntheticKubernetesReportRaw);
    const presentCheckIds = new Set(report.findings.map((f) => f.checkId));
    for (const checkId of IMPLEMENTED_KUBERNETES_CHECK_IDS) {
      expect(presentCheckIds.has(checkId)).toBe(true);
    }
  });

  it("contains no check ID outside the implemented set", () => {
    const report = parseKubernetesReport(syntheticKubernetesReportRaw);
    for (const finding of report.findings) {
      expect(IMPLEMENTED_KUBERNETES_CHECK_IDS).toContain(finding.checkId);
    }
  });

  it("does not fabricate a Critical-severity finding", () => {
    const report = parseKubernetesReport(syntheticKubernetesReportRaw);
    expect(report.summary.critical).toBe(0);
    expect(report.findings.some((f) => f.severity === "critical")).toBe(false);
  });

  it("has a summary that exactly equals the recomputed findings (parseKubernetesReport itself enforces this)", () => {
    const report = parseKubernetesReport(syntheticKubernetesReportRaw);
    const recomputed = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const finding of report.findings) {
      recomputed[finding.severity] += 1;
    }
    expect(report.summary).toEqual({
      ...recomputed,
      total: recomputed.critical + recomputed.high + recomputed.medium + recomputed.low,
    });
  });

  it("uses only fictional demonstration identities, never a real domain", () => {
    const report = parseKubernetesReport(syntheticKubernetesReportRaw);

    expect(report.target.clusterContext).toContain("demo");
    for (const namespace of new Set(report.findings.map((f) => f.namespace))) {
      expect(namespace).toMatch(/-demo$/);
    }
    for (const finding of report.findings) {
      // Every evidence string that names an image must use the reserved
      // example.com domain (RFC 2606), never a real registry/company host.
      const imageMention = /image '([^']+)'/.exec(finding.evidence) ?? /image: ([^)]+)\)/.exec(finding.evidence);
      if (imageMention) {
        expect(imageMention[1]).toMatch(/^example\.com\//);
      }
    }
  });

  it("has enough findings and resource variation for search/filter/sort to be meaningful", () => {
    const report = parseKubernetesReport(syntheticKubernetesReportRaw);
    expect(report.findings.length).toBeGreaterThanOrEqual(IMPLEMENTED_KUBERNETES_CHECK_IDS.length);

    const distinctNamespaces = new Set(report.findings.map((f) => f.namespace));
    const distinctResourceKinds = new Set(report.findings.map((f) => f.resourceKind));
    const distinctSeverities = new Set(report.findings.map((f) => f.severity));

    expect(distinctNamespaces.size).toBeGreaterThan(1);
    expect(distinctResourceKinds.size).toBeGreaterThan(1);
    expect(distinctSeverities.size).toBeGreaterThan(1);
  });
});
