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
  ])("maps %s to %s", (checkId, expected) => {
    expect(deriveCategory(checkId)).toBe(expected);
  });

  it("falls back to 'Other' for an unrecognized prefix", () => {
    expect(deriveCategory("GL-BR-001")).toBe("Other");
    expect(deriveCategory("UNKNOWN-001")).toBe("Other");
  });

  it("is deterministic for the same input", () => {
    expect(deriveCategory("K8S-RES-001")).toBe(deriveCategory("K8S-RES-001"));
  });
});
