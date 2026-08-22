import { describe, expect, it } from "vitest";

import {
  DEFAULT_FILTER_STATE,
  distinctCategories,
  distinctResourceKinds,
  filterFindings,
  matchesSearch,
} from "../../../src/features/report-workspace/filtering";
import { buildNormalizedKubernetesFinding } from "../../helpers/normalizedKubernetesFixtures";

describe("matchesSearch", () => {
  it("matches on check ID, case-insensitively", () => {
    const finding = buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001" });
    expect(matchesSearch(finding, "k8s-res-001")).toBe(true);
  });

  it("matches on title, namespace, resource kind, resource name, container name", () => {
    const finding = buildNormalizedKubernetesFinding({
      title: "Container has no CPU request",
      namespace: "payments-demo",
      resourceKind: "Deployment",
      resourceName: "checkout-api",
      containerName: "api",
    });
    expect(matchesSearch(finding, "CPU request")).toBe(true);
    expect(matchesSearch(finding, "payments-demo")).toBe(true);
    expect(matchesSearch(finding, "deployment")).toBe(true);
    expect(matchesSearch(finding, "checkout-api")).toBe(true);
    expect(matchesSearch(finding, "api")).toBe(true);
  });

  it("matches on evidence, impact, and recommendation", () => {
    const finding = buildNormalizedKubernetesFinding({
      evidence: "unique evidence phrase",
      impact: "unique impact phrase",
      recommendation: "unique recommendation phrase",
    });
    expect(matchesSearch(finding, "evidence phrase")).toBe(true);
    expect(matchesSearch(finding, "impact phrase")).toBe(true);
    expect(matchesSearch(finding, "recommendation phrase")).toBe(true);
  });

  it("trims surrounding whitespace from the query", () => {
    const finding = buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001" });
    expect(matchesSearch(finding, "  K8S-RES-001  ")).toBe(true);
  });

  it("treats an empty or whitespace-only query as matching everything", () => {
    const finding = buildNormalizedKubernetesFinding();
    expect(matchesSearch(finding, "")).toBe(true);
    expect(matchesSearch(finding, "   ")).toBe(true);
  });

  it("does not match unrelated text", () => {
    const finding = buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001" });
    expect(matchesSearch(finding, "nonexistent-query-xyz")).toBe(false);
  });
});

describe("filterFindings", () => {
  const cpuRequest = buildNormalizedKubernetesFinding({
    checkId: "K8S-RES-001",
    severity: "medium",
    resourceKind: "Deployment",
    resourceName: "checkout-api",
  });
  const memoryLimit = buildNormalizedKubernetesFinding({
    checkId: "K8S-RES-004",
    severity: "high",
    resourceKind: "Deployment",
    resourceName: "checkout-api",
  });
  const mutableImage = buildNormalizedKubernetesFinding({
    checkId: "K8S-IMG-001",
    severity: "high",
    resourceKind: "Pod",
    resourceName: "checkout-batch",
  });
  const excessiveRestarts = buildNormalizedKubernetesFinding({
    checkId: "K8S-REL-001",
    severity: "high",
    resourceKind: "Pod",
    resourceName: "cache-pod",
  });
  const allFindings = [cpuRequest, memoryLimit, mutableImage, excessiveRestarts];

  it("with the default filter state, returns every finding", () => {
    expect(filterFindings(allFindings, DEFAULT_FILTER_STATE)).toEqual(allFindings);
  });

  it("filters by severity", () => {
    const result = filterFindings(allFindings, { ...DEFAULT_FILTER_STATE, severity: "medium" });
    expect(result).toEqual([cpuRequest]);
  });

  it("filters by category (derived from check ID)", () => {
    const result = filterFindings(allFindings, {
      ...DEFAULT_FILTER_STATE,
      category: "Image security",
    });
    expect(result).toEqual([mutableImage]);
  });

  it("filters by resource kind", () => {
    const result = filterFindings(allFindings, { ...DEFAULT_FILTER_STATE, resourceKind: "Pod" });
    expect(result).toEqual([mutableImage, excessiveRestarts]);
  });

  it("combines search and severity filters", () => {
    const result = filterFindings(allFindings, {
      ...DEFAULT_FILTER_STATE,
      search: "checkout-api",
      severity: "high",
    });
    expect(result).toEqual([memoryLimit]);
  });

  it("combines search, severity, category, and resource-kind filters", () => {
    const result = filterFindings(allFindings, {
      search: "cache-pod",
      severity: "high",
      category: "Reliability",
      resourceKind: "Pod",
    });
    expect(result).toEqual([excessiveRestarts]);
  });

  it("returns an empty array when nothing matches", () => {
    const result = filterFindings(allFindings, { ...DEFAULT_FILTER_STATE, search: "no-such-finding" });
    expect(result).toEqual([]);
  });
});

describe("distinctResourceKinds / distinctCategories", () => {
  it("returns a stable, sorted, de-duplicated list of resource kinds", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ resourceKind: "Pod" }),
      buildNormalizedKubernetesFinding({ resourceKind: "Deployment" }),
      buildNormalizedKubernetesFinding({ resourceKind: "Pod" }),
    ];
    expect(distinctResourceKinds(findings)).toEqual(["Deployment", "Pod"]);
  });

  it("returns a stable, sorted, de-duplicated list of categories", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ checkId: "K8S-REL-001" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-IMG-001" }),
    ];
    expect(distinctCategories(findings)).toEqual(["Image security", "Reliability", "Resource management"]);
  });
});
