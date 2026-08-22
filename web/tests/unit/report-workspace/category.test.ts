import { describe, expect, it } from "vitest";

import { deriveCategory } from "../../../src/features/report-workspace/category";

describe("deriveCategory", () => {
  it.each([
    ["K8S-RES-001", "Resource management"],
    ["K8S-RES-002", "Resource management"],
    ["K8S-RES-003", "Resource management"],
    ["K8S-RES-004", "Resource management"],
    ["K8S-IMG-001", "Image security"],
    ["K8S-REL-001", "Reliability"],
  ])("maps Kubernetes check %s to %s", (checkId, expected) => {
    expect(deriveCategory(checkId)).toBe(expected);
  });

  it.each([
    ["GL-BR-001", "Branch protection"],
    ["GL-BR-002", "Branch protection"],
    ["GL-BR-003", "Branch protection"],
    ["GL-MR-001", "Merge safeguards"],
    ["GL-SEC-001", "Security"],
    ["GL-SEC-002", "Security"],
    ["GL-SEC-003", "Security"],
    ["GL-COST-001", "Cost efficiency"],
    ["GL-COST-002", "Cost efficiency"],
    ["GL-REL-001", "Reliability"],
    ["GL-CI-001", "Image security"],
  ])("maps GitLab check %s to %s", (checkId, expected) => {
    expect(deriveCategory(checkId)).toBe(expected);
  });

  it("falls back to 'Other' for an unrecognized prefix", () => {
    expect(deriveCategory("UNKNOWN-001")).toBe("Other");
    expect(deriveCategory("GL-UNKNOWN-001")).toBe("Other");
    expect(deriveCategory("")).toBe("Other");
  });

  it("is deterministic for the same input", () => {
    expect(deriveCategory("K8S-RES-001")).toBe(deriveCategory("K8S-RES-001"));
    expect(deriveCategory("GL-BR-001")).toBe(deriveCategory("GL-BR-001"));
  });
});
