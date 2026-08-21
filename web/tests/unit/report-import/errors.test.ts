import { describe, expect, it } from "vitest";

import {
  parseGitLabReport,
  parseKubernetesReport,
  parseReport,
  ReportValidationError,
} from "../../../src/features/report-import";
import { buildGitLabFinding, buildGitLabReport, buildKubernetesFinding, buildKubernetesReport } from "../../helpers/builders";

/**
 * A marker that must never appear anywhere a thrown error exposes to a
 * caller. Deliberately not shaped like any real credential/token format
 * (no `ghp_`/`github_pat_`/`glpat-`/`sk-`/`AKIA`-style prefix or
 * token-like body) -- it only needs to be unique and traceable, not
 * credential-shaped, for this test's purpose.
 */
const SENSITIVE_MARKER = "cloudops-guard-test-marker-f3a9c7e21db4485";

function assertNoLeak(error: unknown): void {
  expect(error).toBeInstanceOf(ReportValidationError);
  const validationError = error as ReportValidationError;

  expect(validationError.message).not.toContain(SENSITIVE_MARKER);
  expect(validationError.code).not.toContain(SENSITIVE_MARKER);
  expect(validationError.name).not.toContain(SENSITIVE_MARKER);
  expect(String(validationError.stack ?? "")).not.toContain(SENSITIVE_MARKER);
  expect(JSON.stringify(validationError)).not.toContain(SENSITIVE_MARKER);
  expect(Object.keys(validationError)).not.toContain("cause");
  expect((validationError as unknown as { cause?: unknown }).cause).toBeUndefined();

  // Every own enumerable property's value must also be free of the marker
  // (guards against a future field being added that copies input data).
  for (const value of Object.values(validationError)) {
    expect(String(value)).not.toContain(SENSITIVE_MARKER);
  }
}

describe("ReportValidationError sanitization", () => {
  it("never exposes a sensitive marker placed in an invalid finding field", () => {
    const finding = buildKubernetesFinding({ evidence: SENSITIVE_MARKER, resource_name: SENSITIVE_MARKER });
    // Also invalidate the report so the marker-bearing finding is rejected.
    delete (finding as Record<string, unknown>).impact;
    try {
      parseKubernetesReport(buildKubernetesReport({}, [finding]));
      throw new Error("expected parser to throw");
    } catch (error) {
      assertNoLeak(error);
    }
  });

  it("never exposes a sensitive marker placed in an unknown/extra field", () => {
    try {
      parseKubernetesReport(buildKubernetesReport({ [SENSITIVE_MARKER]: SENSITIVE_MARKER }));
      throw new Error("expected parser to throw");
    } catch (error) {
      assertNoLeak(error);
    }
  });

  it("never exposes a sensitive marker placed in a malformed timestamp", () => {
    try {
      parseKubernetesReport(
        buildKubernetesReport({ generated_at: `not-a-timestamp-${SENSITIVE_MARKER}` }),
      );
      throw new Error("expected parser to throw");
    } catch (error) {
      assertNoLeak(error);
    }
  });

  it("never exposes a sensitive marker placed in a GitLab project_path", () => {
    const finding = buildGitLabFinding({ project_path: SENSITIVE_MARKER });
    delete (finding as Record<string, unknown>).evidence;
    try {
      parseGitLabReport(buildGitLabReport({}, [finding]));
      throw new Error("expected parser to throw");
    } catch (error) {
      assertNoLeak(error);
    }
  });

  it("never exposes a sensitive marker placed in a gitlab_url on a summary-mismatch rejection", () => {
    const report = buildGitLabReport({
      gitlab_url: `https://${SENSITIVE_MARKER}.example.com`,
      summary: { critical: 0, high: 99, medium: 0, low: 0 },
    });
    try {
      parseGitLabReport(report);
      throw new Error("expected parser to throw");
    } catch (error) {
      assertNoLeak(error);
    }
  });

  it("never exposes a sensitive marker for an unsupported top-level shape", () => {
    try {
      parseReport(`<script>${SENSITIVE_MARKER}</script>`);
      throw new Error("expected parser to throw");
    } catch (error) {
      assertNoLeak(error);
    }
  });

  it("produces one of exactly the five fixed, safe messages regardless of input", () => {
    const knownMessages = [
      "This file is not a supported CloudOps Guard report.",
      "This file does not match a supported CloudOps Guard report format.",
      "The report's summary counts do not match its findings.",
      "This report has more findings than can be processed.",
      "This file is larger than the supported size limit.",
    ];
    try {
      parseKubernetesReport(SENSITIVE_MARKER);
      throw new Error("expected parser to throw");
    } catch (error) {
      expect(knownMessages).toContain((error as ReportValidationError).message);
    }
  });
});
