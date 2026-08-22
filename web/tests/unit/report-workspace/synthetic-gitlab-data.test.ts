import { describe, expect, it } from "vitest";

import { parseGitLabReport } from "../../../src/features/report-import";
import unprotectedBranchReportRaw from "../../../src/data/synthetic-gitlab-report-unprotected-branch.json";
import protectedBranchReportRaw from "../../../src/data/synthetic-gitlab-report-protected-branch.json";

const IMPLEMENTED_GITLAB_CHECK_IDS = [
  "GL-BR-001",
  "GL-BR-002",
  "GL-BR-003",
  "GL-MR-001",
  "GL-SEC-001",
  "GL-SEC-002",
  "GL-SEC-003",
  "GL-COST-001",
  "GL-COST-002",
  "GL-REL-001",
  "GL-CI-001",
] as const;

describe("synthetic GitLab demo dataset", () => {
  it("both raw reports pass parseGitLabReport without modification bypassing validation", () => {
    expect(() => parseGitLabReport(unprotectedBranchReportRaw)).not.toThrow();
    expect(() => parseGitLabReport(protectedBranchReportRaw)).not.toThrow();
  });

  it("has a summary that exactly equals its recomputed findings for both reports (parseGitLabReport itself enforces this)", () => {
    for (const raw of [unprotectedBranchReportRaw, protectedBranchReportRaw]) {
      const report = parseGitLabReport(raw);
      const recomputed = { critical: 0, high: 0, medium: 0, low: 0 };
      for (const finding of report.findings) {
        recomputed[finding.severity] += 1;
      }
      expect(report.summary).toEqual({
        ...recomputed,
        total: recomputed.critical + recomputed.high + recomputed.medium + recomputed.low,
      });
    }
  });

  it("both reports share the same fictional target identity", () => {
    const unprotected = parseGitLabReport(unprotectedBranchReportRaw);
    const protectedBranch = parseGitLabReport(protectedBranchReportRaw);

    expect(protectedBranch.target).toEqual(unprotected.target);
  });

  it("the second report's generated timestamp is strictly later than the first's", () => {
    const unprotected = parseGitLabReport(unprotectedBranchReportRaw);
    const protectedBranch = parseGitLabReport(protectedBranchReportRaw);

    expect(Date.parse(protectedBranch.generatedAt)).toBeGreaterThan(Date.parse(unprotected.generatedAt));
  });

  it("the union of check IDs across both reports is exactly the eleven implemented GitLab checks", () => {
    const unprotected = parseGitLabReport(unprotectedBranchReportRaw);
    const protectedBranch = parseGitLabReport(protectedBranchReportRaw);

    const presentCheckIds = new Set([
      ...unprotected.findings.map((f) => f.checkId),
      ...protectedBranch.findings.map((f) => f.checkId),
    ]);

    for (const checkId of IMPLEMENTED_GITLAB_CHECK_IDS) {
      expect(presentCheckIds.has(checkId)).toBe(true);
    }
    for (const checkId of presentCheckIds) {
      expect(IMPLEMENTED_GITLAB_CHECK_IDS).toContain(checkId);
    }
  });

  it("isolates GL-BR-001 from GL-BR-002/GL-BR-003, matching the production evaluator's mutual exclusivity", () => {
    const unprotected = parseGitLabReport(unprotectedBranchReportRaw);
    const protectedBranch = parseGitLabReport(protectedBranchReportRaw);

    const unprotectedCheckIds = new Set(unprotected.findings.map((f) => f.checkId));
    const protectedCheckIds = new Set(protectedBranch.findings.map((f) => f.checkId));

    expect(unprotectedCheckIds.has("GL-BR-001")).toBe(true);
    expect(unprotectedCheckIds.has("GL-BR-002")).toBe(false);
    expect(unprotectedCheckIds.has("GL-BR-003")).toBe(false);

    expect(protectedCheckIds.has("GL-BR-001")).toBe(false);
    expect(protectedCheckIds.has("GL-BR-002")).toBe(true);
    expect(protectedCheckIds.has("GL-BR-003")).toBe(true);
  });

  it("does not fabricate a Critical-severity finding in either report", () => {
    for (const raw of [unprotectedBranchReportRaw, protectedBranchReportRaw]) {
      const report = parseGitLabReport(raw);
      expect(report.summary.critical).toBe(0);
      expect(report.findings.some((f) => f.severity === "critical")).toBe(false);
    }
  });

  it("covers all four GitLab resource kinds across both reports", () => {
    const unprotected = parseGitLabReport(unprotectedBranchReportRaw);
    const protectedBranch = parseGitLabReport(protectedBranchReportRaw);

    const resourceKinds = new Set([
      ...unprotected.findings.map((f) => f.resourceKind),
      ...protectedBranch.findings.map((f) => f.resourceKind),
    ]);

    expect(resourceKinds).toEqual(new Set(["Project", "ProtectedBranch", "CIJob", "CIService"]));
  });

  it("includes both a CIJob and a CIService GL-CI-001 example, each with a meaningful job identity", () => {
    const unprotected = parseGitLabReport(unprotectedBranchReportRaw);
    const protectedBranch = parseGitLabReport(protectedBranchReportRaw);
    const ciFindings = [...unprotected.findings, ...protectedBranch.findings].filter(
      (f) => f.checkId === "GL-CI-001",
    );

    const jobFinding = ciFindings.find((f) => f.resourceKind === "CIJob");
    const serviceFinding = ciFindings.find((f) => f.resourceKind === "CIService");

    expect(jobFinding).toBeDefined();
    expect(serviceFinding).toBeDefined();
    expect(jobFinding!.jobName).toBeTruthy();
    expect(serviceFinding!.jobName).toBeTruthy();
  });

  it("uses only fictional/reserved domains and identifiers, never a real organization or domain", () => {
    const unprotected = parseGitLabReport(unprotectedBranchReportRaw);
    const protectedBranch = parseGitLabReport(protectedBranchReportRaw);

    for (const report of [unprotected, protectedBranch]) {
      expect(report.target.gitlabUrl).toBe("https://gitlab.example.com");
      expect(report.target.projectPath).toMatch(/^[a-z-]+\/[a-z-]+$/);

      for (const finding of report.findings) {
        if (finding.checkId === "GL-CI-001" && finding.resourceKind === "CIJob") {
          expect(finding.resourceName).toMatch(/^registry\.example\.com\//);
        }
      }
    }
  });
});
