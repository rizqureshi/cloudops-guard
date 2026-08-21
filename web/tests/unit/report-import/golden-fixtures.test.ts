import { describe, expect, it } from "vitest";

import {
  parseGitLabReport,
  parseKubernetesReport,
  parseReport,
} from "../../../src/features/report-import";
import { loadGoldenGitLabReport, loadGoldenKubernetesReport } from "../../helpers/goldenFixtures";

describe("golden fixture: Kubernetes", () => {
  const raw = loadGoldenKubernetesReport();

  it("parses with parseKubernetesReport", () => {
    expect(() => parseKubernetesReport(raw)).not.toThrow();
  });

  it("parses identically via the parseReport dispatcher", () => {
    const viaSpecific = parseKubernetesReport(raw);
    const viaDispatch = parseReport(raw);
    expect(viaDispatch).toEqual(viaSpecific);
  });

  it("normalizes the platform discriminator and target identity", () => {
    const report = parseKubernetesReport(raw);
    expect(report.platform).toBe("kubernetes");
    expect(report.target).toEqual({
      clusterContext: "prod-demo-cluster",
      namespaceFilter: "payments",
    });
  });

  it("normalizes the timestamp verbatim", () => {
    const report = parseKubernetesReport(raw);
    expect(report.generatedAt).toBe("2026-01-15T09:30:00Z");
    for (const finding of report.findings) {
      expect(finding.auditedAt).toBe("2026-01-15T09:30:00Z");
    }
  });

  it("recomputes the summary and derives total", () => {
    const report = parseKubernetesReport(raw);
    expect(report.summary).toEqual({ critical: 0, high: 3, medium: 1, low: 0, total: 4 });
  });

  it("retains every finding with its resource kind and evidence/impact/recommendation", () => {
    const report = parseKubernetesReport(raw);
    expect(report.findings).toHaveLength(4);

    const resourceKinds = report.findings.map((f) => f.resourceKind);
    expect(resourceKinds).toEqual(["Deployment", "Deployment", "Pod", "Pod"]);

    const resReq = report.findings[0]!;
    expect(resReq.checkId).toBe("K8S-RES-001");
    expect(resReq.evidence).toBe(
      "Container 'api' (image: example.com/checkout-api:2.3.1) does not set resources.requests.cpu",
    );
    expect(resReq.impact).toBe(
      "Without a CPU request the scheduler cannot reserve capacity for this container.",
    );
    expect(resReq.recommendation).toBe("Set resources.requests.cpu based on observed usage.");
    expect(resReq.containerName).toBe("api");
  });

  it("preserves Unicode content in resource names without alteration", () => {
    const report = parseKubernetesReport(raw);
    const imageFinding = report.findings.find((f) => f.checkId === "K8S-IMG-001");
    expect(imageFinding?.resourceName).toBe("wébapp-batch-7f9c2");
  });

  it("normalizes a null containerName", () => {
    const report = parseKubernetesReport(raw);
    const restartFinding = report.findings.find((f) => f.checkId === "K8S-REL-001");
    expect(restartFinding?.containerName).toBeNull();
  });
});

describe("golden fixture: GitLab", () => {
  const raw = loadGoldenGitLabReport();

  it("parses with parseGitLabReport", () => {
    expect(() => parseGitLabReport(raw)).not.toThrow();
  });

  it("parses identically via the parseReport dispatcher", () => {
    const viaSpecific = parseGitLabReport(raw);
    const viaDispatch = parseReport(raw);
    expect(viaDispatch).toEqual(viaSpecific);
  });

  it("normalizes the platform discriminator and full project target identity", () => {
    const report = parseGitLabReport(raw);
    expect(report.platform).toBe("gitlab");
    expect(report.target).toEqual({
      gitlabUrl: "https://gitlab.example.com",
      projectId: 4821,
      projectPath: "engineering/checkout-service",
      defaultBranch: "main",
    });
  });

  it("recomputes the summary and derives total", () => {
    const report = parseGitLabReport(raw);
    expect(report.summary).toEqual({ critical: 0, high: 3, medium: 2, low: 1, total: 6 });
  });

  it("retains every finding with its resource kind", () => {
    const report = parseGitLabReport(raw);
    expect(report.findings).toHaveLength(6);
    const resourceKinds = report.findings.map((f) => f.resourceKind);
    expect(resourceKinds).toEqual([
      "ProtectedBranch",
      "Project",
      "Project",
      "Project",
      "CIJob",
      "CIService",
    ]);
  });

  it("normalizes a null jobName for project/branch-level findings", () => {
    const report = parseGitLabReport(raw);
    const branchFinding = report.findings.find((f) => f.checkId === "GL-BR-001");
    expect(branchFinding?.jobName).toBeNull();
  });

  it("normalizes a non-null jobName for CI findings", () => {
    const report = parseGitLabReport(raw);
    const ciFindings = report.findings.filter((f) => f.checkId === "GL-CI-001");
    expect(ciFindings).toHaveLength(2);
    for (const finding of ciFindings) {
      expect(finding.jobName).toBe("build");
    }
  });

  it("preserves evidence, impact, and recommendation text", () => {
    const report = parseGitLabReport(raw);
    const branchFinding = report.findings.find((f) => f.checkId === "GL-BR-001")!;
    expect(branchFinding.evidence).toContain("No exact, wildcard, or inherited protected-branch");
    expect(branchFinding.impact).toContain("unprotected default branch");
    expect(branchFinding.recommendation).toBe(
      "Create a protected-branch rule whose name matches the default branch, with push and merge access restricted appropriately.",
    );
  });

  it("normalizes the timestamp verbatim", () => {
    const report = parseGitLabReport(raw);
    expect(report.generatedAt).toBe("2026-03-10T14:00:00Z");
  });
});
