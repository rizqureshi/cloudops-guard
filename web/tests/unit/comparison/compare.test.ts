import { describe, expect, it } from "vitest";

import { compareGitLabReports, compareKubernetesReports, compareReports } from "../../../src/features/comparison/compare";
import { ComparisonError } from "../../../src/features/comparison/errors";
import type { ComparisonFindingResult } from "../../../src/features/comparison/types";
import type { NormalizedGitLabFinding, NormalizedKubernetesFinding } from "../../../src/features/report-import";
import { buildNormalizedGitLabFinding, buildNormalizedGitLabReport } from "../../helpers/normalizedGitLabFixtures";
import {
  buildNormalizedKubernetesFinding,
  buildNormalizedKubernetesReport,
} from "../../helpers/normalizedKubernetesFixtures";

function statusCounts(
  results: readonly ComparisonFindingResult<unknown>[],
): Record<"new" | "persistent" | "resolved", number> {
  const counts: Record<"new" | "persistent" | "resolved", number> = { new: 0, persistent: 0, resolved: 0 };
  for (const result of results) {
    counts[result.status] += 1;
  }
  return counts;
}

describe("compareKubernetesReports: classification", () => {
  const persistentFinding = buildNormalizedKubernetesFinding({
    checkId: "K8S-RES-001",
    namespace: "payments-demo",
    resourceName: "checkout-api",
    containerName: "api",
  });
  const resolvedFinding = buildNormalizedKubernetesFinding({
    checkId: "K8S-RES-004",
    namespace: "payments-demo",
    resourceName: "checkout-api",
    containerName: "api",
  });
  const newFinding = buildNormalizedKubernetesFinding({
    checkId: "K8S-REL-001",
    namespace: "commerce-demo",
    resourceName: "cache-pod",
    containerName: "redis",
  });

  it("classifies matched, older-only, and newer-only findings correctly", () => {
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" }, [
      persistentFinding,
      resolvedFinding,
    ]);
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" }, [
      persistentFinding,
      newFinding,
    ]);

    const result = compareKubernetesReports(older, newer);

    expect(statusCounts(result.results)).toEqual({ new: 1, persistent: 1, resolved: 1 });

    const persistentResult = result.results.find((r) => r.status === "persistent")!;
    expect(persistentResult.displayFinding).toBe(persistentFinding);
    expect(persistentResult.olderFinding).toBe(persistentFinding);
    expect(persistentResult.newerFinding).toBe(persistentFinding);

    const resolvedResult = result.results.find((r) => r.status === "resolved")!;
    expect(resolvedResult.displayFinding).toBe(resolvedFinding);
    expect(resolvedResult.newerFinding).toBeNull();

    const newResult = result.results.find((r) => r.status === "new")!;
    expect(newResult.displayFinding).toBe(newFinding);
    expect(newResult.olderFinding).toBeNull();
  });

  it("two older duplicate occurrences vs. one newer occurrence produce one persistent and one resolved", () => {
    const dup1 = buildNormalizedKubernetesFinding({ evidence: "occurrence 1" });
    const dup2 = buildNormalizedKubernetesFinding({ evidence: "occurrence 2" });
    const newerSingle = buildNormalizedKubernetesFinding({ evidence: "the only newer occurrence" });

    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" }, [dup1, dup2]);
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" }, [newerSingle]);

    const result = compareKubernetesReports(older, newer);
    expect(statusCounts(result.results)).toEqual({ new: 0, persistent: 1, resolved: 1 });
    expect(result.results).toHaveLength(2);
  });

  it("one older occurrence vs. two newer duplicate occurrences produce one persistent and one new", () => {
    const olderSingle = buildNormalizedKubernetesFinding({ evidence: "the only older occurrence" });
    const dup1 = buildNormalizedKubernetesFinding({ evidence: "occurrence 1" });
    const dup2 = buildNormalizedKubernetesFinding({ evidence: "occurrence 2" });

    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" }, [olderSingle]);
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" }, [dup1, dup2]);

    const result = compareKubernetesReports(older, newer);
    expect(statusCounts(result.results)).toEqual({ new: 1, persistent: 1, resolved: 0 });
    expect(result.results).toHaveLength(3 - 1); // one persistent + one new
  });

  it("never collapses duplicate findings: three identical occurrences in both reports produce three persistent results", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ evidence: "a" }),
      buildNormalizedKubernetesFinding({ evidence: "b" }),
      buildNormalizedKubernetesFinding({ evidence: "c" }),
    ];
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" }, findings);
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" }, findings);

    const result = compareKubernetesReports(older, newer);
    expect(result.results).toHaveLength(3);
    expect(statusCounts(result.results)).toEqual({ new: 0, persistent: 3, resolved: 0 });
  });

  it("finding order does not affect the classification or totals", () => {
    const findingsOlder = [persistentFinding, resolvedFinding];
    const findingsNewer = [persistentFinding, newFinding];

    const forward = compareKubernetesReports(
      buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" }, findingsOlder),
      buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" }, findingsNewer),
    );
    const reversed = compareKubernetesReports(
      buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" }, [...findingsOlder].reverse()),
      buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" }, [...findingsNewer].reverse()),
    );

    expect(reversed.statusTotals).toEqual(forward.statusTotals);
    // Compare by (status, checkId) pairs -- order-independent, but proves
    // the same classification regardless of input order.
    const summarize = (results: readonly ComparisonFindingResult<NormalizedKubernetesFinding>[]) =>
      results.map((r) => `${r.status}:${r.displayFinding.checkId}`).sort();
    expect(summarize(reversed.results)).toEqual(summarize(forward.results));
    // The `results` array itself is also order-independent (fingerprints
    // are sorted ordinally before iterating), not just the classification.
    expect(reversed.results.map((r) => r.status)).toEqual(forward.results.map((r) => r.status));
  });

  it("does not mutate either input report or its findings", () => {
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" }, [
      persistentFinding,
      resolvedFinding,
    ]);
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" }, [
      persistentFinding,
      newFinding,
    ]);
    const olderCopy = structuredClone(older);
    const newerCopy = structuredClone(newer);

    compareKubernetesReports(older, newer);

    expect(older).toEqual(olderCopy);
    expect(newer).toEqual(newerCopy);
  });
});

describe("compareGitLabReports: GL-CI-001 image-reference-change limitation", () => {
  it("produces one resolved and one new result (never persistent) when a job's image reference changes", () => {
    const before = buildNormalizedGitLabFinding({
      checkId: "GL-CI-001",
      resourceKind: "CIJob",
      resourceName: "registry.example.com/inventory/build:latest",
      jobName: "build",
    });
    const after = buildNormalizedGitLabFinding({
      checkId: "GL-CI-001",
      resourceKind: "CIJob",
      resourceName: "registry.example.com/inventory-build/build:latest",
      jobName: "build",
    });

    const older = buildNormalizedGitLabReport({ generatedAt: "2026-01-01T00:00:00Z" }, [before]);
    const newer = buildNormalizedGitLabReport({ generatedAt: "2026-01-02T00:00:00Z" }, [after]);

    const result = compareGitLabReports(older, newer);
    expect(statusCounts(result.results)).toEqual({ new: 1, persistent: 0, resolved: 1 });

    const resolvedResult = result.results.find((r) => r.status === "resolved")!;
    const newResult = result.results.find((r) => r.status === "new")!;
    expect((resolvedResult.displayFinding as NormalizedGitLabFinding).resourceName).toBe(
      "registry.example.com/inventory/build:latest",
    );
    expect((newResult.displayFinding as NormalizedGitLabFinding).resourceName).toBe(
      "registry.example.com/inventory-build/build:latest",
    );
  });

  it("stays persistent when the job's image reference is unchanged", () => {
    const finding = buildNormalizedGitLabFinding({
      checkId: "GL-CI-001",
      resourceKind: "CIJob",
      resourceName: "registry.example.com/inventory/build:latest",
      jobName: "build",
    });
    const older = buildNormalizedGitLabReport({ generatedAt: "2026-01-01T00:00:00Z" }, [finding]);
    const newer = buildNormalizedGitLabReport({ generatedAt: "2026-01-02T00:00:00Z" }, [finding]);

    const result = compareGitLabReports(older, newer);
    expect(statusCounts(result.results)).toEqual({ new: 0, persistent: 1, resolved: 0 });
  });
});

describe("compareReports: shared platform-dispatch (Phase 3G)", () => {
  it("dispatches to compareKubernetesReports for a Kubernetes pair, producing the same result", () => {
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" });
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" });

    const dispatched = compareReports(older, newer);
    const direct = compareKubernetesReports(older, newer);
    expect(dispatched).toEqual(direct);
    expect(dispatched.platform).toBe("kubernetes");
  });

  it("dispatches to compareGitLabReports for a GitLab pair, producing the same result", () => {
    const older = buildNormalizedGitLabReport({ generatedAt: "2026-01-01T00:00:00Z" });
    const newer = buildNormalizedGitLabReport({ generatedAt: "2026-01-02T00:00:00Z" });

    const dispatched = compareReports(older, newer);
    const direct = compareGitLabReports(older, newer);
    expect(dispatched).toEqual(direct);
    expect(dispatched.platform).toBe("gitlab");
  });

  it("rejects a mixed-platform pair with a sanitized ComparisonError, regardless of which side is which platform", () => {
    const kubernetesReport = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" });
    const gitlabReport = buildNormalizedGitLabReport({ generatedAt: "2026-01-02T00:00:00Z" });

    expect(() => compareReports(kubernetesReport, gitlabReport)).toThrow(ComparisonError);
    expect(() => compareReports(gitlabReport, kubernetesReport)).toThrow(ComparisonError);
    try {
      compareReports(kubernetesReport, gitlabReport);
      expect.unreachable("compareReports should have thrown");
    } catch (error) {
      expect((error as ComparisonError).code).toBe("mixed_platform");
    }
  });

  it("is pure and deterministic: calling it twice with the same inputs produces equal results and does not mutate either input", () => {
    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-01-01T00:00:00Z" });
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-01-02T00:00:00Z" });
    const olderCopy = structuredClone(older);
    const newerCopy = structuredClone(newer);

    const first = compareReports(older, newer);
    const second = compareReports(older, newer);

    expect(first).toEqual(second);
    expect(older).toEqual(olderCopy);
    expect(newer).toEqual(newerCopy);
  });
});
