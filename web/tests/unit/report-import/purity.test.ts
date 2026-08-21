import { describe, expect, it } from "vitest";

import { parseGitLabReport, parseKubernetesReport, parseReport } from "../../../src/features/report-import";
import { buildGitLabReport, buildKubernetesReport } from "../../helpers/builders";

describe("parsers do not mutate their input", () => {
  it("parseKubernetesReport leaves a deep-frozen valid input untouched", () => {
    const input = deepFreeze(buildKubernetesReport());
    expect(() => parseKubernetesReport(input)).not.toThrow();
  });

  it("parseGitLabReport leaves a deep-frozen valid input untouched", () => {
    const input = deepFreeze(buildGitLabReport());
    expect(() => parseGitLabReport(input)).not.toThrow();
  });

  it("parseReport leaves a deep-frozen valid Kubernetes input untouched", () => {
    const input = deepFreeze(buildKubernetesReport());
    expect(() => parseReport(input)).not.toThrow();
  });

  it("parseReport leaves a deep-frozen valid GitLab input untouched", () => {
    const input = deepFreeze(buildGitLabReport());
    expect(() => parseReport(input)).not.toThrow();
  });

  it("leaves a snapshot-equal copy of the input after a successful parse", () => {
    const input = buildKubernetesReport();
    const snapshot = structuredClone(input);
    parseKubernetesReport(input);
    expect(input).toEqual(snapshot);
  });

  it("leaves a snapshot-equal copy of the input after a rejected parse", () => {
    const input = buildKubernetesReport({ cluster_context: 123 });
    const snapshot = structuredClone(input);
    try {
      parseKubernetesReport(input);
    } catch {
      // expected -- the assertion below is what matters here.
    }
    expect(input).toEqual(snapshot);
  });
});

function deepFreeze<T>(value: T): T {
  if (value !== null && (typeof value === "object" || typeof value === "function")) {
    for (const key of Object.getOwnPropertyNames(value)) {
      deepFreeze((value as Record<string, unknown>)[key]);
    }
    Object.freeze(value);
  }
  return value;
}
