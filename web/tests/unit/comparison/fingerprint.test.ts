import { describe, expect, it } from "vitest";

import { computeFingerprint } from "../../../src/features/comparison/fingerprint";
import { buildNormalizedGitLabFinding } from "../../helpers/normalizedGitLabFixtures";
import { buildNormalizedKubernetesFinding } from "../../helpers/normalizedKubernetesFixtures";

describe("computeFingerprint (Kubernetes)", () => {
  it("is identical for two findings that differ only in fields the fingerprint ignores", () => {
    const a = buildNormalizedKubernetesFinding({
      severity: "medium",
      title: "Container has no CPU request",
      evidence: "evidence A",
      impact: "impact A",
      recommendation: "recommendation A",
      autoRemediable: false,
      auditedAt: "2026-01-01T00:00:00Z",
    });
    const b = buildNormalizedKubernetesFinding({
      severity: "high",
      title: "Something else entirely",
      evidence: "evidence B",
      impact: "impact B",
      recommendation: "recommendation B",
      autoRemediable: true,
      auditedAt: "2026-02-02T00:00:00Z",
    });
    expect(computeFingerprint(a)).toBe(computeFingerprint(b));
  });

  it("differs when checkId, clusterContext, namespace, resourceKind, resourceName, or containerName differs", () => {
    const base = buildNormalizedKubernetesFinding();
    const variants = [
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-002" }),
      buildNormalizedKubernetesFinding({ clusterContext: "other-cluster" }),
      buildNormalizedKubernetesFinding({ namespace: "other-namespace" }),
      buildNormalizedKubernetesFinding({ resourceKind: "Pod" }),
      buildNormalizedKubernetesFinding({ resourceName: "other-resource" }),
      buildNormalizedKubernetesFinding({ containerName: "other-container" }),
    ];
    for (const variant of variants) {
      expect(computeFingerprint(variant)).not.toBe(computeFingerprint(base));
    }
  });

  it("does not collide for delimiter-shaped field values that would collide under naive string joining", () => {
    const a = buildNormalizedKubernetesFinding({ namespace: "a|b", resourceName: "c" });
    const b = buildNormalizedKubernetesFinding({ namespace: "a", resourceName: "b|c" });
    expect(computeFingerprint(a)).not.toBe(computeFingerprint(b));

    const c = buildNormalizedKubernetesFinding({ namespace: 'a","b', resourceName: "c" });
    const d = buildNormalizedKubernetesFinding({ namespace: "a", resourceName: '","b","c' });
    expect(computeFingerprint(c)).not.toBe(computeFingerprint(d));
  });

  it("distinguishes null containerName from an empty-string containerName", () => {
    const withNull = buildNormalizedKubernetesFinding({ containerName: null });
    const withEmpty = buildNormalizedKubernetesFinding({ containerName: "" });
    expect(computeFingerprint(withNull)).not.toBe(computeFingerprint(withEmpty));
  });
});

describe("computeFingerprint (GitLab)", () => {
  it("is identical for two findings that differ only in fields the fingerprint ignores", () => {
    const a = buildNormalizedGitLabFinding({
      severity: "high",
      title: "title A",
      evidence: "evidence A",
      impact: "impact A",
      recommendation: "recommendation A",
      autoRemediable: false,
      auditedAt: "2026-01-01T00:00:00Z",
    });
    const b = buildNormalizedGitLabFinding({
      severity: "low",
      title: "title B",
      evidence: "evidence B",
      impact: "impact B",
      recommendation: "recommendation B",
      autoRemediable: true,
      auditedAt: "2026-02-02T00:00:00Z",
    });
    expect(computeFingerprint(a)).toBe(computeFingerprint(b));
  });

  it("differs when checkId, projectPath, resourceKind, resourceName, or jobName differs", () => {
    const base = buildNormalizedGitLabFinding();
    const variants = [
      buildNormalizedGitLabFinding({ checkId: "GL-BR-002" }),
      buildNormalizedGitLabFinding({ projectPath: "other/project" }),
      buildNormalizedGitLabFinding({ resourceKind: "Project" }),
      buildNormalizedGitLabFinding({ resourceName: "other-resource" }),
      buildNormalizedGitLabFinding({ jobName: "some-job" }),
    ];
    for (const variant of variants) {
      expect(computeFingerprint(variant)).not.toBe(computeFingerprint(base));
    }
  });

  it("changes when an image-reference resourceName changes, demonstrating the GL-CI-001 limitation", () => {
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
    expect(computeFingerprint(before)).not.toBe(computeFingerprint(after));
  });

  it("does not collide for delimiter-shaped field values", () => {
    const a = buildNormalizedGitLabFinding({ projectPath: "a|b", resourceName: "c" });
    const b = buildNormalizedGitLabFinding({ projectPath: "a", resourceName: "b|c" });
    expect(computeFingerprint(a)).not.toBe(computeFingerprint(b));
  });

  it("distinguishes null jobName from an empty-string jobName", () => {
    const withNull = buildNormalizedGitLabFinding({ jobName: null });
    const withEmpty = buildNormalizedGitLabFinding({ jobName: "" });
    expect(computeFingerprint(withNull)).not.toBe(computeFingerprint(withEmpty));
  });
});
