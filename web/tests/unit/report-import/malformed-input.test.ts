import { describe, expect, it } from "vitest";

import {
  parseGitLabReport,
  parseKubernetesReport,
  ReportValidationError,
} from "../../../src/features/report-import";
import { buildGitLabFinding, buildGitLabReport, buildKubernetesFinding, buildKubernetesReport } from "../../helpers/builders";

function expectInvalid(fn: () => unknown): void {
  try {
    fn();
    throw new Error("expected parser to throw");
  } catch (error) {
    expect(error).toBeInstanceOf(ReportValidationError);
    expect((error as ReportValidationError).code).toBe("invalid_report");
  }
}

describe("Kubernetes: missing/unknown fields", () => {
  it("rejects a report missing a required top-level field", () => {
    const report = buildKubernetesReport() as Record<string, unknown>;
    delete report.cluster_context;
    expectInvalid(() => parseKubernetesReport(report));
  });

  it("rejects a finding missing a required field", () => {
    const finding = buildKubernetesFinding() as Record<string, unknown>;
    delete finding.evidence;
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({}, [finding])));
  });

  it("rejects an unknown field at the report level", () => {
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({ extra: "nope" })));
  });

  it("rejects an unknown field at the summary level", () => {
    const report = buildKubernetesReport() as Record<string, unknown>;
    report.summary = { ...(report.summary as object), total: 1 };
    expectInvalid(() => parseKubernetesReport(report));
  });

  it("rejects an unknown field at the finding level", () => {
    const finding = buildKubernetesFinding({ unexpected: "field" });
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({}, [finding])));
  });
});

describe("Kubernetes: types, enums, and nullables", () => {
  it("rejects an invalid severity value", () => {
    const finding = buildKubernetesFinding({ severity: "urgent" });
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({}, [finding])));
  });

  it("rejects an invalid resource_kind value", () => {
    const finding = buildKubernetesFinding({ resource_kind: "StatefulSet" });
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({}, [finding])));
  });

  it("does not coerce a numeric string for a required string field", () => {
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({ cluster_context: 123 })));
  });

  it("does not coerce a boolean for a required string field", () => {
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({ cluster_context: true })));
  });

  it("does not coerce a string for a required boolean field", () => {
    const finding = buildKubernetesFinding({ auto_remediable: "false" });
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({}, [finding])));
  });

  it("accepts a null containerName", () => {
    const finding = buildKubernetesFinding({ container_name: null });
    const report = parseKubernetesReport(buildKubernetesReport({}, [finding]));
    expect(report.findings[0]!.containerName).toBeNull();
  });

  it("accepts an empty string containerName (the Kubernetes model has no non-empty constraint)", () => {
    const finding = buildKubernetesFinding({ container_name: "" });
    const report = parseKubernetesReport(buildKubernetesReport({}, [finding]));
    expect(report.findings[0]!.containerName).toBe("");
  });

  it("rejects a missing (undefined) containerName -- the key must be present", () => {
    const finding = buildKubernetesFinding() as Record<string, unknown>;
    delete finding.container_name;
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({}, [finding])));
  });

  it("accepts an empty-string cluster_context/namespace/resource_name (no non-empty constraint)", () => {
    const report = parseKubernetesReport(
      buildKubernetesReport({ cluster_context: "", namespace_filter: "" }),
    );
    expect(report.target.clusterContext).toBe("");
    expect(report.target.namespaceFilter).toBe("");
  });
});

describe("Kubernetes: timestamps", () => {
  it("rejects a naive (offset-free) generated_at", () => {
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({ generated_at: "2026-01-01T00:00:00" })));
  });

  it("rejects a naive audited_at on a finding", () => {
    const finding = buildKubernetesFinding({ audited_at: "2026-01-01T00:00:00" });
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({}, [finding])));
  });

  it("rejects a date-only string", () => {
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({ generated_at: "2026-01-01" })));
  });

  it("rejects an impossible calendar date", () => {
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({ generated_at: "2026-02-30T00:00:00Z" })));
  });

  it("rejects an invalid month", () => {
    expectInvalid(() => parseKubernetesReport(buildKubernetesReport({ generated_at: "2026-13-01T00:00:00Z" })));
  });

  it("rejects a malformed timezone offset", () => {
    expectInvalid(() =>
      parseKubernetesReport(buildKubernetesReport({ generated_at: "2026-01-01T00:00:00+25:00" })),
    );
  });

  it("rejects a value only JavaScript's permissive Date.parse() would repair", () => {
    // Date.parse() accepts this (interprets it loosely); the strict ISO
    // schema must not.
    expect(Number.isNaN(Date.parse("2026-01-01 00:00:00"))).toBe(false);
    expectInvalid(() =>
      parseKubernetesReport(buildKubernetesReport({ generated_at: "2026-01-01 00:00:00" })),
    );
  });

  it("accepts a Z-suffixed timestamp", () => {
    const report = parseKubernetesReport(buildKubernetesReport({ generated_at: "2026-01-01T00:00:00Z" }));
    expect(report.generatedAt).toBe("2026-01-01T00:00:00Z");
  });

  it("accepts a positive-offset timestamp, retained verbatim", () => {
    const report = parseKubernetesReport(
      buildKubernetesReport({ generated_at: "2026-01-01T00:00:00+05:30" }),
    );
    expect(report.generatedAt).toBe("2026-01-01T00:00:00+05:30");
  });

  it("accepts a negative-offset timestamp, retained verbatim", () => {
    const report = parseKubernetesReport(
      buildKubernetesReport({ generated_at: "2026-01-01T00:00:00-08:00" }),
    );
    expect(report.generatedAt).toBe("2026-01-01T00:00:00-08:00");
  });
});

describe("GitLab: missing/unknown fields", () => {
  it("rejects a report missing a required top-level field", () => {
    const report = buildGitLabReport() as Record<string, unknown>;
    delete report.default_branch;
    expectInvalid(() => parseGitLabReport(report));
  });

  it("rejects a finding missing a required field", () => {
    const finding = buildGitLabFinding() as Record<string, unknown>;
    delete finding.evidence;
    expectInvalid(() => parseGitLabReport(buildGitLabReport({}, [finding])));
  });

  it("rejects an unknown field at the report level", () => {
    expectInvalid(() => parseGitLabReport(buildGitLabReport({ extra: "nope" })));
  });

  it("rejects an unknown field at the summary level", () => {
    const report = buildGitLabReport() as Record<string, unknown>;
    report.summary = { ...(report.summary as object), total: 1 };
    expectInvalid(() => parseGitLabReport(report));
  });

  it("rejects an unknown field at the finding level", () => {
    const finding = buildGitLabFinding({ unexpected: "field" });
    expectInvalid(() => parseGitLabReport(buildGitLabReport({}, [finding])));
  });
});

describe("GitLab: types, enums, and nullables", () => {
  it("rejects an invalid severity value", () => {
    const finding = buildGitLabFinding({ severity: "urgent" });
    expectInvalid(() => parseGitLabReport(buildGitLabReport({}, [finding])));
  });

  it("rejects an invalid resource_kind value", () => {
    const finding = buildGitLabFinding({ resource_kind: "Pipeline" });
    expectInvalid(() => parseGitLabReport(buildGitLabReport({}, [finding])));
  });

  it("does not coerce a numeric string for project_id", () => {
    expectInvalid(() => parseGitLabReport(buildGitLabReport({ project_id: "4821" })));
  });

  it("does not coerce a string for auto_remediable", () => {
    const finding = buildGitLabFinding({ auto_remediable: "false" });
    expectInvalid(() => parseGitLabReport(buildGitLabReport({}, [finding])));
  });

  it.each([
    ["zero", 0],
    ["negative", -1],
    ["fractional", 4.5],
    ["a string", "4821"],
    ["a boolean", true],
  ])("rejects project_id: %s", (_label, project_id) => {
    expectInvalid(() => parseGitLabReport(buildGitLabReport({ project_id })));
  });

  it("accepts a null jobName", () => {
    const finding = buildGitLabFinding({ job_name: null });
    const report = parseGitLabReport(buildGitLabReport({}, [finding]));
    expect(report.findings[0]!.jobName).toBeNull();
  });

  it("accepts a non-empty jobName", () => {
    const finding = buildGitLabFinding({ job_name: "build" });
    const report = parseGitLabReport(buildGitLabReport({}, [finding]));
    expect(report.findings[0]!.jobName).toBe("build");
  });

  it("rejects an empty-string jobName (the GitLab model requires non-empty when present)", () => {
    const finding = buildGitLabFinding({ job_name: "" });
    expectInvalid(() => parseGitLabReport(buildGitLabReport({}, [finding])));
  });

  it.each(["gitlab_url", "project_path", "default_branch"])(
    "rejects an empty %s (the GitLab model requires non-empty)",
    (field) => {
      expectInvalid(() => parseGitLabReport(buildGitLabReport({ [field]: "" })));
    },
  );

  it.each(["check_id", "project_path", "resource_name"])(
    "rejects an empty finding.%s (the GitLab model requires non-empty)",
    (field) => {
      const finding = buildGitLabFinding({ [field]: "" });
      expectInvalid(() => parseGitLabReport(buildGitLabReport({}, [finding])));
    },
  );
});

describe("GitLab: timestamps", () => {
  it("rejects a naive generated_at", () => {
    expectInvalid(() => parseGitLabReport(buildGitLabReport({ generated_at: "2026-01-01T00:00:00" })));
  });

  it("rejects a naive audited_at on a finding", () => {
    const finding = buildGitLabFinding({ audited_at: "2026-01-01T00:00:00" });
    expectInvalid(() => parseGitLabReport(buildGitLabReport({}, [finding])));
  });

  it("accepts a Z-suffixed timestamp", () => {
    const report = parseGitLabReport(buildGitLabReport({ generated_at: "2026-01-01T00:00:00Z" }));
    expect(report.generatedAt).toBe("2026-01-01T00:00:00Z");
  });

  it("accepts a positive-offset timestamp, retained verbatim", () => {
    const report = parseGitLabReport(
      buildGitLabReport({ generated_at: "2026-01-01T00:00:00+05:30" }),
    );
    expect(report.generatedAt).toBe("2026-01-01T00:00:00+05:30");
  });

  it("accepts a negative-offset timestamp, retained verbatim", () => {
    const report = parseGitLabReport(
      buildGitLabReport({ generated_at: "2026-01-01T00:00:00-08:00" }),
    );
    expect(report.generatedAt).toBe("2026-01-01T00:00:00-08:00");
  });
});
