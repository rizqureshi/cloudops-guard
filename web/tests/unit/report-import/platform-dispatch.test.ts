import { describe, expect, it } from "vitest";

import { parseReport, ReportValidationError } from "../../../src/features/report-import";
import { buildGitLabReport, buildKubernetesReport } from "../../helpers/builders";

describe("parseReport: platform dispatch", () => {
  it("selects the GitLab parser when platform: 'gitlab' is present", () => {
    const report = parseReport(buildGitLabReport());
    expect(report.platform).toBe("gitlab");
  });

  it("selects the Kubernetes parser when no platform property is present", () => {
    const report = parseReport(buildKubernetesReport());
    expect(report.platform).toBe("kubernetes");
  });

  it("rejects an unsupported platform value without attempting either parser", () => {
    const candidate = buildKubernetesReport({ platform: "azure" });
    expect(() => parseReport(candidate)).toThrow(ReportValidationError);
    try {
      parseReport(candidate);
      throw new Error("expected parseReport to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ReportValidationError);
      expect((error as ReportValidationError).code).toBe("unsupported_report");
    }
  });

  it("rejects platform: 'kubernetes' as an unsupported value (never an alias for the unmarked shape)", () => {
    const candidate = buildKubernetesReport({ platform: "kubernetes" });
    try {
      parseReport(candidate);
      throw new Error("expected parseReport to throw");
    } catch (error) {
      expect((error as ReportValidationError).code).toBe("unsupported_report");
    }
  });

  it("rejects platform: 'gitlab' that fails GitLab validation, without falling back to Kubernetes", () => {
    // Same fields as a valid GitLab report, but gitlab_url is empty, which
    // the GitLab schema rejects (min_length=1). It also happens not to be
    // shaped like a valid Kubernetes report, but the important assertion is
    // the *code*: invalid_report (GitLab path), never a Kubernetes attempt.
    const candidate = buildGitLabReport({ gitlab_url: "" });
    try {
      parseReport(candidate);
      throw new Error("expected parseReport to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ReportValidationError);
      expect((error as ReportValidationError).code).toBe("invalid_report");
    }
  });

  it("rejects an object combining fields from both shapes as an ambiguous hybrid", () => {
    // No `platform` property -> eligible for Kubernetes parsing -> the
    // Kubernetes strict schema rejects the extra GitLab-only fields.
    const hybrid = buildKubernetesReport({
      project_id: 1,
      project_path: "group/project",
    });
    try {
      parseReport(hybrid);
      throw new Error("expected parseReport to throw");
    } catch (error) {
      expect((error as ReportValidationError).code).toBe("invalid_report");
    }
  });

  it("rejects a platform: 'gitlab' object that also carries Kubernetes-only fields", () => {
    const hybrid = buildGitLabReport({
      cluster_context: "test-cluster",
      namespace_filter: null,
    });
    try {
      parseReport(hybrid);
      throw new Error("expected parseReport to throw");
    } catch (error) {
      expect((error as ReportValidationError).code).toBe("invalid_report");
    }
  });

  it.each([
    ["null", null],
    ["an array", []],
    ["a string", "not a report"],
    ["HTML text", "<script>alert(1)</script>"],
    ["a number", 42],
    ["a boolean", true],
  ])("rejects %s as unsupported", (_label, value) => {
    try {
      parseReport(value);
      throw new Error("expected parseReport to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ReportValidationError);
      expect((error as ReportValidationError).code).toBe("unsupported_report");
    }
  });

  it("rejects an unrelated plain object as an invalid Kubernetes report, not as unsupported", () => {
    // { hello: "world" } has no `platform` property, so per the dispatch
    // rule it is *eligible* for Kubernetes parsing (unlike null/arrays/
    // strings, which never reach either parser) -- it then correctly fails
    // the Kubernetes schema itself, yielding invalid_report rather than
    // unsupported_report.
    try {
      parseReport({ hello: "world" });
      throw new Error("expected parseReport to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ReportValidationError);
      expect((error as ReportValidationError).code).toBe("invalid_report");
    }
  });

  it("rejects an own platform: undefined as unsupported (the key's presence, not its value, drives dispatch)", () => {
    // Object.hasOwn sees this key regardless of its value, so this must not
    // be treated the same as a genuinely absent `platform` property.
    const candidate = buildKubernetesReport({ platform: undefined });
    expect(Object.hasOwn(candidate, "platform")).toBe(true);
    try {
      parseReport(candidate);
      throw new Error("expected parseReport to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ReportValidationError);
      expect((error as ReportValidationError).code).toBe("unsupported_report");
    }
  });

  it("rejects an own platform: null as unsupported", () => {
    const candidate = buildKubernetesReport({ platform: null });
    try {
      parseReport(candidate);
      throw new Error("expected parseReport to throw");
    } catch (error) {
      expect((error as ReportValidationError).code).toBe("unsupported_report");
    }
  });

  it("dispatches on an object's own fields, ignoring an inherited platform: 'gitlab' property", () => {
    // The prototype carries a non-enumerable `platform: "gitlab"` -- non-
    // enumerable so it does not also trip the *Kubernetes* schema's own,
    // separate strict-unknown-key check (which considers inherited
    // enumerable properties, not just own ones -- itself a legitimate
    // additional protection, but not what this test is isolating). What
    // this test isolates is dispatch *routing*: `candidate` has no *own*
    // `platform` property, so `parseReport` must route it to the
    // Kubernetes parser, never to the GitLab parser, regardless of what a
    // plain property lookup resolves through the prototype chain.
    const prototypeWithGitlabPlatform: Record<string, unknown> = {};
    Object.defineProperty(prototypeWithGitlabPlatform, "platform", {
      value: "gitlab",
      enumerable: false,
    });
    const candidate = Object.assign(
      Object.create(prototypeWithGitlabPlatform),
      buildKubernetesReport(),
    ) as Record<string, unknown>;

    // Sanity check on the test fixture itself: a plain property lookup
    // *would* see the inherited value, proving this is a genuine inherited-
    // property scenario and not accidentally an own property.
    expect(candidate.platform).toBe("gitlab");
    expect(Object.hasOwn(candidate, "platform")).toBe(false);

    // If dispatch incorrectly routed on the inherited value, this would be
    // parsed (or rejected) as a GitLab report; instead it must parse
    // successfully as Kubernetes, proving the inherited property never
    // reached the routing decision.
    const report = parseReport(candidate);
    expect(report.platform).toBe("kubernetes");
  });
});
