import { describe, expect, it, vi } from "vitest";

import { importLocalReportFile } from "../../../src/features/local-report-explorer/importLocalReportFile";
import { LocalImportError } from "../../../src/features/local-report-explorer/errors";
import { MAX_REPORT_FILE_BYTES, ReportValidationError } from "../../../src/features/report-import";
import goldenGitlabReport from "../../../../tests/fixtures/golden_gitlab_report.json";
import goldenKubernetesReport from "../../../../tests/fixtures/golden_kubernetes_report.json";
import syntheticKubernetesReport from "../../../src/data/synthetic-kubernetes-report.json";
import syntheticGitlabReport from "../../../src/data/synthetic-gitlab-report-unprotected-branch.json";

function buildFile(name: string, content: unknown, mimeType = "application/json"): File {
  const text = typeof content === "string" ? content : JSON.stringify(content);
  return new File([text], name, { type: mimeType });
}

/** A minimal `File`-shaped test double, for asserting `.text()` is never called. */
function buildTrackedFile(name: string, size: number): { file: File; textSpy: ReturnType<typeof vi.fn> } {
  const textSpy = vi.fn(async () => "{}");
  const file = { name, size, text: textSpy } as unknown as File;
  return { file, textSpy };
}

describe("importLocalReportFile: valid imports", () => {
  it("imports a valid Kubernetes report", async () => {
    const file = buildFile("report.json", syntheticKubernetesReport);
    const report = await importLocalReportFile(file);
    expect(report.platform).toBe("kubernetes");
  });

  it("imports a valid GitLab report", async () => {
    const file = buildFile("report.json", syntheticGitlabReport);
    const report = await importLocalReportFile(file);
    expect(report.platform).toBe("gitlab");
  });

  it("imports the golden Kubernetes fixture", async () => {
    const file = buildFile("report.json", goldenKubernetesReport);
    const report = await importLocalReportFile(file);
    expect(report.platform).toBe("kubernetes");
    expect(report.findings.length).toBeGreaterThan(0);
  });

  it("imports the golden GitLab fixture", async () => {
    const file = buildFile("report.json", goldenGitlabReport);
    const report = await importLocalReportFile(file);
    expect(report.platform).toBe("gitlab");
    expect(report.findings.length).toBeGreaterThan(0);
  });

  it("accepts a case-insensitive .JSON extension", async () => {
    const file = buildFile("Report.JSON", syntheticKubernetesReport);
    const report = await importLocalReportFile(file);
    expect(report.platform).toBe("kubernetes");
  });
});

describe("importLocalReportFile: rejections", () => {
  it("rejects a wrong file extension with LocalImportError, never reading the file", async () => {
    const { file, textSpy } = buildTrackedFile("report.txt", 10);
    await expect(importLocalReportFile(file)).rejects.toThrow(LocalImportError);
    expect(textSpy).not.toHaveBeenCalled();
  });

  it("rejects a file with no extension", async () => {
    const file = buildFile("report", syntheticKubernetesReport);
    await expect(importLocalReportFile(file)).rejects.toMatchObject({ code: "wrong_extension" });
  });

  it("rejects HTML content presented with a .json extension (fails JSON parsing)", async () => {
    const file = buildFile("report.json", "<html><body>not a report</body></html>", "text/html");
    await expect(importLocalReportFile(file)).rejects.toMatchObject({ code: "malformed_json" });
  });

  it("rejects an oversized file before any read occurs", async () => {
    const { file, textSpy } = buildTrackedFile("report.json", MAX_REPORT_FILE_BYTES + 1);
    await expect(importLocalReportFile(file)).rejects.toThrow(ReportValidationError);
    await expect(importLocalReportFile(file)).rejects.toMatchObject({ code: "file_too_large" });
    expect(textSpy).not.toHaveBeenCalled();
  });

  it("accepts a file exactly at the size limit (only rejects when strictly over)", async () => {
    const file = buildFile("report.json", syntheticKubernetesReport);
    // `assertReportFileSize` accepts sizes through exactly the limit; force
    // `.size` to the boundary value while keeping the real, readable body.
    Object.defineProperty(file, "size", { value: MAX_REPORT_FILE_BYTES });
    await expect(importLocalReportFile(file)).resolves.toBeDefined();
  });

  it("rejects malformed JSON", async () => {
    const file = buildFile("report.json", "{ this is not valid JSON", "application/json");
    await expect(importLocalReportFile(file)).rejects.toMatchObject({ code: "malformed_json" });
  });

  it("rejects a shape matching neither the Kubernetes nor GitLab contract", async () => {
    // No own `platform` key, so `parseReport` attempts Kubernetes
    // validation (see report-import/parsers.ts) and that strict schema
    // rejects this shape -- `invalid_report`, not `unsupported_report`.
    const file = buildFile("report.json", { not: "a recognized report shape" });
    await expect(importLocalReportFile(file)).rejects.toThrow(ReportValidationError);
    await expect(importLocalReportFile(file)).rejects.toMatchObject({ code: "invalid_report" });
  });

  it("rejects an own platform value that is neither absent nor 'gitlab'", async () => {
    const file = buildFile("report.json", { platform: "windows", findings: [] });
    await expect(importLocalReportFile(file)).rejects.toThrow(ReportValidationError);
    await expect(importLocalReportFile(file)).rejects.toMatchObject({ code: "unsupported_report" });
  });

  it("rejects a summary that does not match the recomputed findings", async () => {
    const tampered = {
      ...syntheticKubernetesReport,
      summary: { critical: 99, high: 99, medium: 99, low: 99 },
    };
    const file = buildFile("report.json", tampered);
    await expect(importLocalReportFile(file)).rejects.toThrow(ReportValidationError);
    await expect(importLocalReportFile(file)).rejects.toMatchObject({ code: "summary_mismatch" });
  });

  it("rejects a report with more findings than the supported limit", async () => {
    const finding = syntheticKubernetesReport.findings[0]!;
    const manyFindings = Array.from({ length: 10_001 }, () => finding);
    const oversizedReport = { ...syntheticKubernetesReport, findings: manyFindings };
    const file = buildFile("report.json", oversizedReport);
    await expect(importLocalReportFile(file)).rejects.toThrow(ReportValidationError);
    await expect(importLocalReportFile(file)).rejects.toMatchObject({ code: "too_many_findings" });
  });

  it("maps a local file-read failure to a sanitized LocalImportError", async () => {
    const file = {
      name: "report.json",
      size: 10,
      text: vi.fn(async () => {
        throw new Error("some low-level I/O failure detail that must never surface");
      }),
    } as unknown as File;
    await expect(importLocalReportFile(file)).rejects.toMatchObject({ code: "read_failed" });
  });

  it("maps any other unexpected exception to a fixed generic failure", async () => {
    // A failure inside the file's own `.text()` call is already mapped to
    // "read_failed" (see the test above) -- to exercise the outer
    // catch-all instead, this throws from a property access that happens
    // *outside* any of the specific try/catch blocks (reading `file.name`,
    // the very first thing `importLocalReportFile` does).
    const file = {
      get name(): string {
        throw new TypeError("poisoned name getter, not a normal file-read failure");
      },
      size: 10,
      text: vi.fn(async () => "{}"),
    } as unknown as File;
    await expect(importLocalReportFile(file)).rejects.toMatchObject({ code: "unexpected_failure" });
  });
});

describe("importLocalReportFile: sanitized errors", () => {
  it("never reproduces the filename in any thrown error message", async () => {
    const secretName = "very-secret-internal-project-name-report.txt";
    const file = buildFile(secretName, syntheticKubernetesReport);
    try {
      await importLocalReportFile(file);
      expect.unreachable("should have thrown for a .txt file");
    } catch (error) {
      expect((error as Error).message).not.toContain(secretName);
      expect((error as Error).message).not.toContain("very-secret-internal-project-name");
    }
  });

  it("never reproduces the native JSON.parse error text", async () => {
    const file = buildFile("report.json", "{ unquoted: key }", "application/json");
    try {
      await importLocalReportFile(file);
      expect.unreachable("should have thrown for malformed JSON");
    } catch (error) {
      const message = (error as Error).message;
      expect(message).not.toMatch(/unexpected token/i);
      expect(message).not.toMatch(/position \d+/i);
      expect(message).not.toMatch(/JSON\.parse/i);
    }
  });

  it("never reproduces Zod issue output or raw report field values", async () => {
    const file = buildFile("report.json", {
      cluster_context: 12345, // wrong type -- triggers a Zod validation issue
      namespace_filter: null,
      generated_at: "2026-01-01T00:00:00Z",
      findings: [],
      summary: { critical: 0, high: 0, medium: 0, low: 0 },
    });
    try {
      await importLocalReportFile(file);
      expect.unreachable("should have thrown for an invalid field type");
    } catch (error) {
      const message = (error as Error).message;
      expect(message).not.toContain("12345");
      expect(message).not.toMatch(/zod/i);
      expect(message).not.toMatch(/expected string/i);
    }
  });

  it("never reproduces an arbitrary thrown exception's own message", async () => {
    const file = {
      name: "report.json",
      size: 10,
      text: vi.fn(async () => {
        throw new Error("ARBITRARY_MARKER_STRING_THAT_MUST_NOT_LEAK");
      }),
    } as unknown as File;
    try {
      await importLocalReportFile(file);
      expect.unreachable("should have thrown");
    } catch (error) {
      expect((error as Error).message).not.toContain("ARBITRARY_MARKER_STRING_THAT_MUST_NOT_LEAK");
    }
  });

  it("every LocalImportError/ReportValidationError message comes from the fixed table, never a caller-supplied string", async () => {
    const cases: Array<{ file: File }> = [
      { file: buildFile("report.txt", "irrelevant") },
      { file: buildFile("report.json", "not json at all {{{") },
      { file: buildFile("report.json", { unsupported: true }) },
    ];
    for (const { file } of cases) {
      try {
        await importLocalReportFile(file);
        expect.unreachable("should have thrown");
      } catch (error) {
        expect(error).toBeInstanceOf(Error);
        expect((error as Error).name === "LocalImportError" || (error as Error).name === "ReportValidationError").toBe(
          true,
        );
      }
    }
  });
});
