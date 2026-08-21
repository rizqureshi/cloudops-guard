import { describe, expect, it } from "vitest";

import {
  assertReportFileSize,
  MAX_FINDINGS_PER_REPORT,
  MAX_REPORT_FILE_BYTES,
  parseKubernetesReport,
  ReportValidationError,
} from "../../../src/features/report-import";
import { buildKubernetesFinding, buildKubernetesReport } from "../../helpers/builders";

function buildManyFindings(count: number): Record<string, unknown>[] {
  // Deliberately minimal and synthetic -- never stored as a repository
  // fixture, generated in-memory for exactly this test.
  return Array.from({ length: count }, () => buildKubernetesFinding({ severity: "low" }));
}

describe("MAX_FINDINGS_PER_REPORT", () => {
  it("is exactly 10,000", () => {
    expect(MAX_FINDINGS_PER_REPORT).toBe(10_000);
  });

  it("accepts a report with exactly 10,000 findings", () => {
    const findings = buildManyFindings(MAX_FINDINGS_PER_REPORT);
    const report = parseKubernetesReport(
      buildKubernetesReport({ summary: { critical: 0, high: 0, medium: 0, low: MAX_FINDINGS_PER_REPORT } }, findings),
    );
    expect(report.findings).toHaveLength(MAX_FINDINGS_PER_REPORT);
    expect(report.summary.total).toBe(MAX_FINDINGS_PER_REPORT);
  });

  it("rejects a report with 10,001 findings", () => {
    const count = MAX_FINDINGS_PER_REPORT + 1;
    const findings = buildManyFindings(count);
    const report = buildKubernetesReport(
      { summary: { critical: 0, high: 0, medium: 0, low: count } },
      findings,
    );
    try {
      parseKubernetesReport(report);
      throw new Error("expected parser to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ReportValidationError);
      expect((error as ReportValidationError).code).toBe("too_many_findings");
    }
  });
});

describe("MAX_REPORT_FILE_BYTES / assertReportFileSize", () => {
  it("is exactly 10 MiB", () => {
    expect(MAX_REPORT_FILE_BYTES).toBe(10 * 1024 * 1024);
  });

  it("accepts zero bytes", () => {
    expect(() => assertReportFileSize(0)).not.toThrow();
  });

  it("accepts exactly 10 MiB", () => {
    expect(() => assertReportFileSize(MAX_REPORT_FILE_BYTES)).not.toThrow();
  });

  it("rejects 10 MiB plus one byte", () => {
    try {
      assertReportFileSize(MAX_REPORT_FILE_BYTES + 1);
      throw new Error("expected assertReportFileSize to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ReportValidationError);
      expect((error as ReportValidationError).code).toBe("file_too_large");
    }
  });

  it.each([
    ["negative", -1],
    ["fractional", 1024.5],
    ["NaN", Number.NaN],
    ["positive Infinity", Number.POSITIVE_INFINITY],
    ["negative Infinity", Number.NEGATIVE_INFINITY],
  ])("rejects a %s byte size", (_label, size) => {
    try {
      assertReportFileSize(size);
      throw new Error("expected assertReportFileSize to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ReportValidationError);
      expect((error as ReportValidationError).code).toBe("file_too_large");
    }
  });
});
