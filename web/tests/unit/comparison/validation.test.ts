import { describe, expect, it } from "vitest";

import { ComparisonError } from "../../../src/features/comparison/errors";
import { assertComparable } from "../../../src/features/comparison/validation";
import { buildNormalizedGitLabReport } from "../../helpers/normalizedGitLabFixtures";
import { buildNormalizedKubernetesReport } from "../../helpers/normalizedKubernetesFixtures";

describe("assertComparable", () => {
  it("accepts a strictly later, target-compatible Kubernetes pair", () => {
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" });
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" });
    expect(() => assertComparable(older, newer)).not.toThrow();
  });

  it("accepts a strictly later, target-compatible GitLab pair", () => {
    const older = buildNormalizedGitLabReport({ generatedAt: "2026-01-01T00:00:00Z" });
    const newer = buildNormalizedGitLabReport({ generatedAt: "2026-01-02T00:00:00Z" });
    expect(() => assertComparable(older, newer)).not.toThrow();
  });

  it("rejects mixed platforms", () => {
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" });
    const newer = buildNormalizedGitLabReport({ generatedAt: "2026-01-02T00:00:00Z" });
    expect(() => assertComparable(older, newer)).toThrow(ComparisonError);
    try {
      assertComparable(older, newer);
    } catch (error) {
      expect((error as ComparisonError).code).toBe("mixed_platform");
    }
  });

  it("rejects equal timestamps", () => {
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" });
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" });
    expect(() => assertComparable(older, newer)).toThrow(ComparisonError);
  });

  it("rejects offset-equivalent timestamps representing the same instant", () => {
    // 09:00 UTC == 14:30 UTC+5:30 -- same instant, different formatting.
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T09:00:00Z" });
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T14:30:00+05:30" });
    expect(() => assertComparable(older, newer)).toThrow(ComparisonError);
  });

  it("rejects reversed (older after newer) timestamps", () => {
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" });
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" });
    expect(() => assertComparable(older, newer)).toThrow(ComparisonError);
    try {
      assertComparable(older, newer);
    } catch (error) {
      expect((error as ComparisonError).code).toBe("non_positive_time_range");
    }
  });

  it("rejects a Kubernetes clusterContext mismatch", () => {
    const older = buildNormalizedKubernetesReport({
      generatedAt: "2026-01-01T00:00:00Z",
      target: { clusterContext: "cluster-a", namespaceFilter: null },
    });
    const newer = buildNormalizedKubernetesReport({
      generatedAt: "2026-01-02T00:00:00Z",
      target: { clusterContext: "cluster-b", namespaceFilter: null },
    });
    expect(() => assertComparable(older, newer)).toThrow(ComparisonError);
  });

  it("rejects null vs. a named namespace as a Kubernetes target mismatch", () => {
    const older = buildNormalizedKubernetesReport({
      generatedAt: "2026-01-01T00:00:00Z",
      target: { clusterContext: "cluster-a", namespaceFilter: null },
    });
    const newer = buildNormalizedKubernetesReport({
      generatedAt: "2026-01-02T00:00:00Z",
      target: { clusterContext: "cluster-a", namespaceFilter: "payments" },
    });
    expect(() => assertComparable(older, newer)).toThrow(ComparisonError);
  });

  it("rejects a GitLab gitlabUrl mismatch", () => {
    const older = buildNormalizedGitLabReport({
      generatedAt: "2026-01-01T00:00:00Z",
      target: {
        gitlabUrl: "https://gitlab.example.com",
        projectId: 1,
        projectPath: "a/b",
        defaultBranch: "main",
      },
    });
    const newer = buildNormalizedGitLabReport({
      generatedAt: "2026-01-02T00:00:00Z",
      target: {
        gitlabUrl: "https://gitlab.other.example.com",
        projectId: 1,
        projectPath: "a/b",
        defaultBranch: "main",
      },
    });
    expect(() => assertComparable(older, newer)).toThrow(ComparisonError);
  });

  it("rejects a GitLab projectId mismatch", () => {
    const older = buildNormalizedGitLabReport({
      generatedAt: "2026-01-01T00:00:00Z",
      target: { gitlabUrl: "https://gitlab.example.com", projectId: 1, projectPath: "a/b", defaultBranch: "main" },
    });
    const newer = buildNormalizedGitLabReport({
      generatedAt: "2026-01-02T00:00:00Z",
      target: { gitlabUrl: "https://gitlab.example.com", projectId: 2, projectPath: "a/b", defaultBranch: "main" },
    });
    expect(() => assertComparable(older, newer)).toThrow(ComparisonError);
  });

  it("rejects a GitLab projectPath mismatch", () => {
    const older = buildNormalizedGitLabReport({
      generatedAt: "2026-01-01T00:00:00Z",
      target: { gitlabUrl: "https://gitlab.example.com", projectId: 1, projectPath: "a/b", defaultBranch: "main" },
    });
    const newer = buildNormalizedGitLabReport({
      generatedAt: "2026-01-02T00:00:00Z",
      target: { gitlabUrl: "https://gitlab.example.com", projectId: 1, projectPath: "a/c", defaultBranch: "main" },
    });
    expect(() => assertComparable(older, newer)).toThrow(ComparisonError);
  });

  it("does NOT reject a GitLab defaultBranch difference alone -- it is not part of the compatibility rule", () => {
    const older = buildNormalizedGitLabReport({
      generatedAt: "2026-01-01T00:00:00Z",
      target: { gitlabUrl: "https://gitlab.example.com", projectId: 1, projectPath: "a/b", defaultBranch: "main" },
    });
    const newer = buildNormalizedGitLabReport({
      generatedAt: "2026-01-02T00:00:00Z",
      target: {
        gitlabUrl: "https://gitlab.example.com",
        projectId: 1,
        projectPath: "a/b",
        defaultBranch: "develop",
      },
    });
    expect(() => assertComparable(older, newer)).not.toThrow();
  });

  it("produces a sanitized error whose message never reproduces a report-supplied value", () => {
    const older = buildNormalizedKubernetesReport({
      generatedAt: "2026-01-01T00:00:00Z",
      target: { clusterContext: "super-secret-cluster-name-xyz", namespaceFilter: null },
    });
    const newer = buildNormalizedKubernetesReport({
      generatedAt: "2026-01-02T00:00:00Z",
      target: { clusterContext: "different-cluster-name-abc", namespaceFilter: null },
    });
    try {
      assertComparable(older, newer);
      expect.unreachable("assertComparable should have thrown");
    } catch (error) {
      const message = (error as Error).message;
      expect(message).not.toContain("super-secret-cluster-name-xyz");
      expect(message).not.toContain("different-cluster-name-abc");
    }
  });

  it("does not mutate either input report", () => {
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" });
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" });
    const olderCopy = structuredClone(older);
    const newerCopy = structuredClone(newer);
    assertComparable(older, newer);
    expect(older).toEqual(olderCopy);
    expect(newer).toEqual(newerCopy);
  });
});
