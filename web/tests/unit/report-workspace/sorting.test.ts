import { describe, expect, it } from "vitest";

import { sortFindings } from "../../../src/features/report-workspace/sorting";
import { buildNormalizedKubernetesFinding } from "../../helpers/normalizedKubernetesFixtures";

describe("sortFindings", () => {
  it("sorts by severity, critical first, low last", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ checkId: "A", severity: "low", resourceName: "r1" }),
      buildNormalizedKubernetesFinding({ checkId: "B", severity: "critical", resourceName: "r2" }),
      buildNormalizedKubernetesFinding({ checkId: "C", severity: "high", resourceName: "r3" }),
      buildNormalizedKubernetesFinding({ checkId: "D", severity: "medium", resourceName: "r4" }),
    ];
    // Note: none of the six implemented Kubernetes checks currently
    // produce "critical" -- this fixture uses it anyway purely to exercise
    // the sort comparator's full ordering, independent of what the real
    // synthetic dataset contains.
    const sorted = sortFindings(findings, "severity");
    expect(sorted.map((f) => f.severity)).toEqual(["critical", "high", "medium", "low"]);
  });

  it("breaks a severity tie by check ID", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-004", severity: "high", resourceName: "r1" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-IMG-001", severity: "high", resourceName: "r2" }),
    ];
    const sorted = sortFindings(findings, "severity");
    expect(sorted.map((f) => f.checkId)).toEqual(["K8S-IMG-001", "K8S-RES-004"]);
  });

  it("breaks a severity+checkId tie by resource identity", () => {
    const findings = [
      buildNormalizedKubernetesFinding({
        checkId: "K8S-RES-001",
        severity: "medium",
        namespace: "z-namespace",
        resourceName: "api",
      }),
      buildNormalizedKubernetesFinding({
        checkId: "K8S-RES-001",
        severity: "medium",
        namespace: "a-namespace",
        resourceName: "api",
      }),
    ];
    const sorted = sortFindings(findings, "severity");
    expect(sorted.map((f) => (f.platform === "kubernetes" ? f.namespace : ""))).toEqual([
      "a-namespace",
      "z-namespace",
    ]);
  });

  it("sorts by check ID, with severity then resource as tie-breakers", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ checkId: "K8S-REL-001", resourceName: "r1" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-IMG-001", resourceName: "r2" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", resourceName: "r3" }),
    ];
    const sorted = sortFindings(findings, "checkId");
    expect(sorted.map((f) => f.checkId)).toEqual(["K8S-IMG-001", "K8S-REL-001", "K8S-RES-001"]);
  });

  it("sorts by resource identity, with severity then check ID as tie-breakers", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", namespace: "zeta", resourceName: "api" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", namespace: "alpha", resourceName: "api" }),
    ];
    const sorted = sortFindings(findings, "resource");
    expect(sorted.map((f) => (f.platform === "kubernetes" ? f.namespace : ""))).toEqual(["alpha", "zeta"]);
  });

  it("produces the same order regardless of input order (deterministic, not input-order-dependent)", () => {
    const a = buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", severity: "medium", resourceName: "r1" });
    const b = buildNormalizedKubernetesFinding({ checkId: "K8S-IMG-001", severity: "high", resourceName: "r2" });
    const c = buildNormalizedKubernetesFinding({ checkId: "K8S-REL-001", severity: "high", resourceName: "r3" });

    const sortedForward = sortFindings([a, b, c], "severity").map((f) => f.checkId);
    const sortedReversed = sortFindings([c, b, a], "severity").map((f) => f.checkId);
    expect(sortedForward).toEqual(sortedReversed);
  });

  it("does not mutate the input array", () => {
    const findings = [
      buildNormalizedKubernetesFinding({ checkId: "K8S-RES-001", severity: "low" }),
      buildNormalizedKubernetesFinding({ checkId: "K8S-IMG-001", severity: "high" }),
    ];
    const original = [...findings];
    sortFindings(findings, "severity");
    expect(findings).toEqual(original);
  });

  it("distinguishes resource fields that would collide if naively joined with a delimiter", () => {
    // If namespace/resourceName were joined with e.g. "|" before comparing,
    // namespace "a|b" + resourceName "c" and namespace "a" + resourceName
    // "b|c" would produce the identical joined string "a|b|c" (with
    // resourceKind and containerName held equal). Comparing fields
    // individually must still tell these apart.
    const collidesIfJoined = buildNormalizedKubernetesFinding({
      checkId: "K8S-RES-001",
      severity: "medium",
      namespace: "a|b",
      resourceKind: "Pod",
      resourceName: "c",
      containerName: "d",
    });
    const alsoCollidesIfJoined = buildNormalizedKubernetesFinding({
      checkId: "K8S-RES-001",
      severity: "medium",
      namespace: "a",
      resourceKind: "Pod",
      resourceName: "b|c",
      containerName: "d",
    });

    const sorted = sortFindings([collidesIfJoined, alsoCollidesIfJoined], "resource");

    // Field-by-field: namespace "a" < namespace "a|b" (shorter is a strict
    // prefix, so it sorts first) -- resolved on the very first field,
    // proving the two are compared as genuinely distinct field tuples,
    // never as one coincidentally-equal joined string.
    expect(sorted.map((f) => (f.platform === "kubernetes" ? f.namespace : ""))).toEqual(["a", "a|b"]);
  });

  it("uses plain code-unit ordering, not locale-aware comparison", () => {
    // Under ordinary code-unit ordering, every uppercase ASCII letter
    // (0x41-0x5A) sorts before every lowercase ASCII letter (0x61-0x7A), so
    // "Zebra" sorts *before* "apple". This is verified directly with the
    // same `<` operator the production comparator uses -- deliberately not
    // via String.prototype.localeCompare, whose result depends on the
    // running environment's default locale and ICU configuration and is
    // therefore not a portable thing to assert on.
    expect("Zebra" < "apple").toBe(true);

    const zebraFinding = buildNormalizedKubernetesFinding({
      checkId: "K8S-RES-001",
      severity: "medium",
      namespace: "Zebra",
      resourceName: "same",
    });
    const appleFinding = buildNormalizedKubernetesFinding({
      checkId: "K8S-RES-001",
      severity: "medium",
      namespace: "apple",
      resourceName: "same",
    });

    const sorted = sortFindings([appleFinding, zebraFinding], "resource");
    expect(sorted.map((f) => (f.platform === "kubernetes" ? f.namespace : ""))).toEqual(["Zebra", "apple"]);
  });

  it("distinguishes findings sharing check ID, severity, and resource identity by a further displayed field", () => {
    // Same check ID, severity, namespace, resource kind, resource name, and
    // container name -- differing only in evidence. The comparator must
    // not fall back to input order merely because the first three keys in
    // the chain match.
    const findingA = buildNormalizedKubernetesFinding({
      checkId: "K8S-IMG-001",
      severity: "high",
      namespace: "shared-namespace",
      resourceKind: "Pod",
      resourceName: "shared-pod",
      containerName: "shared-container",
      evidence: "aaa",
    });
    const findingB = buildNormalizedKubernetesFinding({
      checkId: "K8S-IMG-001",
      severity: "high",
      namespace: "shared-namespace",
      resourceKind: "Pod",
      resourceName: "shared-pod",
      containerName: "shared-container",
      evidence: "zzz",
    });

    const sortedForward = sortFindings([findingA, findingB], "severity").map((f) => f.evidence);
    const sortedReversed = sortFindings([findingB, findingA], "severity").map((f) => f.evidence);

    expect(sortedForward).toEqual(["aaa", "zzz"]);
    // Deterministic regardless of input order -- not input-order-dependent
    // just because severity/checkId/resource identity all matched.
    expect(sortedReversed).toEqual(["aaa", "zzz"]);
  });
});

describe("sorting.ts source file integrity", () => {
  it("contains zero literal NUL bytes and is recognized as text", async () => {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const sourcePath = fileURLToPath(new URL("../../../src/features/report-workspace/sorting.ts", import.meta.url));
    const bytes = readFileSync(sourcePath);
    let nulCount = 0;
    for (const byte of bytes) {
      if (byte === 0x00) {
        nulCount += 1;
      }
    }
    expect(nulCount).toBe(0);
  });
});
