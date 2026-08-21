import { describe, expect, it } from "vitest";

import { parseKubernetesReport, ReportValidationError } from "../../../src/features/report-import";
import { buildKubernetesFinding, buildKubernetesReport } from "../../helpers/builders";

function expectSummaryMismatch(fn: () => unknown): void {
  try {
    fn();
    throw new Error("expected parser to throw");
  } catch (error) {
    expect(error).toBeInstanceOf(ReportValidationError);
    expect((error as ReportValidationError).code).toBe("summary_mismatch");
  }
}

describe("summary recomputation", () => {
  it("accepts an empty findings array with an all-zero summary", () => {
    const report = parseKubernetesReport(
      buildKubernetesReport({ summary: { critical: 0, high: 0, medium: 0, low: 0 } }, []),
    );
    expect(report.findings).toHaveLength(0);
    expect(report.summary).toEqual({ critical: 0, high: 0, medium: 0, low: 0, total: 0 });
  });

  it("recomputes a correct summary from mixed-severity findings and derives total", () => {
    const findings = [
      buildKubernetesFinding({ severity: "high" }),
      buildKubernetesFinding({ severity: "high" }),
      buildKubernetesFinding({ severity: "medium" }),
      buildKubernetesFinding({ severity: "low" }),
    ];
    const report = parseKubernetesReport(
      buildKubernetesReport({ summary: { critical: 0, high: 2, medium: 1, low: 1 } }, findings),
    );
    expect(report.summary).toEqual({ critical: 0, high: 2, medium: 1, low: 1, total: 4 });
  });

  it.each(["critical", "high", "medium", "low"] as const)(
    "rejects the report when the supplied %s count disagrees with the recomputed count",
    (severity) => {
      const finding = buildKubernetesFinding({ severity: "high" });
      const summary = { critical: 0, high: 1, medium: 0, low: 0 };
      summary[severity] += 1; // introduce a one-off mismatch on exactly this field
      expectSummaryMismatch(() =>
        parseKubernetesReport(buildKubernetesReport({ summary }, [finding])),
      );
    },
  );

  it("does not silently correct a mismatched summary", () => {
    const finding = buildKubernetesFinding({ severity: "high" });
    const report = buildKubernetesReport(
      { summary: { critical: 0, high: 99, medium: 0, low: 0 } },
      [finding],
    );
    expectSummaryMismatch(() => parseKubernetesReport(report));
  });

  it.each([
    ["a numeric string", "1"],
    ["a fraction", 1.5],
    ["negative", -1],
    ["a boolean", true],
    ["NaN", Number.NaN],
    ["Infinity", Number.POSITIVE_INFINITY],
  ])("rejects a summary count that is %s", (_label, badValue) => {
    const report = buildKubernetesReport({
      summary: { critical: badValue, high: 0, medium: 0, low: 0 },
    });
    try {
      parseKubernetesReport(report);
      throw new Error("expected parser to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ReportValidationError);
      expect((error as ReportValidationError).code).toBe("invalid_report");
    }
  });
});
