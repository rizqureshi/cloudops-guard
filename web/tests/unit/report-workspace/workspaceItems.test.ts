import { describe, expect, it } from "vitest";

import { DEFAULT_FILTER_STATE } from "../../../src/features/report-workspace/filtering";
import {
  buildSingleReportItems,
  filterWorkspaceItems,
  sortWorkspaceItems,
  type WorkspaceItem,
} from "../../../src/features/report-workspace/workspaceItems";
import { buildNormalizedKubernetesFinding } from "../../helpers/normalizedKubernetesFixtures";

const newFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-REL-001",
  severity: "high",
  namespace: "commerce-demo",
  resourceName: "cache-pod",
});
const persistentFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-RES-001",
  severity: "medium",
  namespace: "payments-demo",
  resourceName: "checkout-api",
});
const resolvedFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-RES-004",
  severity: "high",
  namespace: "payments-demo",
  resourceName: "checkout-api",
});

const items: WorkspaceItem[] = [
  { finding: newFinding, status: "new" },
  { finding: persistentFinding, status: "persistent" },
  { finding: resolvedFinding, status: "resolved" },
];

describe("buildSingleReportItems", () => {
  it("wraps every finding with a null status", () => {
    const result = buildSingleReportItems([newFinding, persistentFinding]);
    expect(result).toEqual([
      { finding: newFinding, status: null },
      { finding: persistentFinding, status: null },
    ]);
  });
});

describe("filterWorkspaceItems: comparison-status filtering", () => {
  it("returns every item when comparisonStatus is 'all'", () => {
    const result = filterWorkspaceItems(items, DEFAULT_FILTER_STATE);
    expect(result).toHaveLength(3);
  });

  it("filters to only 'new' items", () => {
    const result = filterWorkspaceItems(items, { ...DEFAULT_FILTER_STATE, comparisonStatus: "new" });
    expect(result).toEqual([{ finding: newFinding, status: "new" }]);
  });

  it("filters to only 'persistent' items", () => {
    const result = filterWorkspaceItems(items, { ...DEFAULT_FILTER_STATE, comparisonStatus: "persistent" });
    expect(result).toEqual([{ finding: persistentFinding, status: "persistent" }]);
  });

  it("filters to only 'resolved' items", () => {
    const result = filterWorkspaceItems(items, { ...DEFAULT_FILTER_STATE, comparisonStatus: "resolved" });
    expect(result).toEqual([{ finding: resolvedFinding, status: "resolved" }]);
  });

  it("combines comparison-status filtering with severity filtering", () => {
    const result = filterWorkspaceItems(items, {
      ...DEFAULT_FILTER_STATE,
      comparisonStatus: "resolved",
      severity: "high",
    });
    expect(result).toEqual([{ finding: resolvedFinding, status: "resolved" }]);
  });

  it("combines comparison-status filtering with search", () => {
    const result = filterWorkspaceItems(items, {
      ...DEFAULT_FILTER_STATE,
      comparisonStatus: "new",
      search: "cache-pod",
    });
    expect(result).toEqual([{ finding: newFinding, status: "new" }]);

    const noMatch = filterWorkspaceItems(items, {
      ...DEFAULT_FILTER_STATE,
      comparisonStatus: "new",
      search: "checkout-api",
    });
    expect(noMatch).toEqual([]);
  });

  it("clearing back to DEFAULT_FILTER_STATE restores all comparison results", () => {
    const filtered = filterWorkspaceItems(items, { ...DEFAULT_FILTER_STATE, comparisonStatus: "new" });
    expect(filtered).toHaveLength(1);
    const restored = filterWorkspaceItems(items, DEFAULT_FILTER_STATE);
    expect(restored).toHaveLength(3);
  });
});

describe("sortWorkspaceItems: comparison-status ordering", () => {
  it("orders items new -> persistent -> resolved for the 'comparisonStatus' option", () => {
    const sorted = sortWorkspaceItems(items, "comparisonStatus");
    expect(sorted.map((item) => item.status)).toEqual(["new", "persistent", "resolved"]);
  });

  it("is deterministic within each status group (uses the severity chain as a secondary key)", () => {
    const anotherNew = buildNormalizedKubernetesFinding({
      checkId: "K8S-IMG-001",
      severity: "high",
      namespace: "commerce-demo",
      resourceName: "audit-log",
    });
    const withTwoNew: WorkspaceItem[] = [
      { finding: anotherNew, status: "new" },
      { finding: newFinding, status: "new" },
      { finding: persistentFinding, status: "persistent" },
      { finding: resolvedFinding, status: "resolved" },
    ];
    const forward = sortWorkspaceItems(withTwoNew, "comparisonStatus");
    const reversed = sortWorkspaceItems([...withTwoNew].reverse(), "comparisonStatus");
    expect(reversed.map((item) => item.finding.checkId)).toEqual(forward.map((item) => item.finding.checkId));
  });

  it("delegates to the finding-level comparator for the 'severity'/'checkId'/'resource' options, preserving status", () => {
    const sorted = sortWorkspaceItems(items, "severity");
    // Severity order: high, high, medium -- resolvedFinding/newFinding are
    // both high, tie-broken by check ID ("K8S-REL-001" < "K8S-RES-004").
    expect(sorted.map((item) => item.finding.checkId)).toEqual(["K8S-REL-001", "K8S-RES-004", "K8S-RES-001"]);
    expect(sorted.map((item) => item.status)).toEqual(["new", "resolved", "persistent"]);
  });
});
