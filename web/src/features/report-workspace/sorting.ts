/**
 * Deterministic finding sort comparators.
 *
 * Every sort option shares the same disambiguation chain -- severity, check
 * ID, resource identity, then a fixed sequence of further displayed fields
 * (title, evidence, impact, recommendation, audited timestamp,
 * auto-remediable) -- only which key comes *first* changes per option. Two
 * findings compare unequal as soon as any field in this chain differs, so
 * ordering never falls back to JavaScript engine sort stability or the
 * input array's original order merely because severity, check ID, and
 * resource identity happen to match. Only genuinely identical findings --
 * equal in every one of these displayed fields -- can still compare equal;
 * that is fine, because such findings are indistinguishable in the UI
 * anyway, and each occurrence's on-screen identity is preserved separately
 * by a per-occurrence React key (see ReportWorkspace.tsx), not by sort
 * order. Comparison-status sorting does not exist here -- comparison is
 * out of scope until Phase 3F.
 *
 * String comparisons use plain code-unit ordering (`<`/`>`/`===`), never
 * `String.prototype.localeCompare`: locale-aware comparison is
 * environment-dependent (it can vary by browser, OS locale, and ICU data)
 * and therefore not guaranteed to produce the same order everywhere.
 *
 * Resource identity is compared **field by field**, never by joining
 * fields into a single delimited string first. Findings display untrusted,
 * report-derived text (namespace, resource name, container name, etc.), so
 * any fixed delimiter risks two different field combinations producing the
 * same joined string (e.g. namespace `"a/b"` + name `"c"` vs. namespace
 * `"a"` + name `"b/c"` joined with `/`). Comparing the fields in sequence,
 * each to full completion before moving to the next, has no such collision
 * case regardless of what characters a report contains.
 */

import type { NormalizedFinding, Severity } from "../report-import";

export type SortOption = "severity" | "checkId" | "resource";

export const SORT_OPTIONS: readonly SortOption[] = ["severity", "checkId", "resource"];

const SEVERITY_RANK: Readonly<Record<Severity, number>> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

type FindingComparator = (a: NormalizedFinding, b: NormalizedFinding) => number;

/**
 * Plain code-unit ordering -- never locale-aware. Exported so other modules
 * in this feature (e.g. `filtering.ts`'s dropdown-option ordering) share
 * this one implementation instead of each reimplementing `<`/`>`/`===`
 * comparison or falling back to `String.prototype.localeCompare`.
 */
export function compareOrdinal(a: string, b: string): number {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

function compareNumeric(a: number, b: number): number {
  return a - b;
}

/**
 * The fields that identify a finding's resource, kept as a plain field
 * sequence -- never joined into one string. Distinct per platform since
 * the identifying fields differ.
 */
function resourceIdentityFields(finding: NormalizedFinding): readonly string[] {
  if (finding.platform === "kubernetes") {
    return [finding.namespace, finding.resourceKind, finding.resourceName, finding.containerName ?? ""];
  }
  return [finding.projectPath, finding.resourceKind, finding.resourceName, finding.jobName ?? ""];
}

/** Compares two field sequences element by element, each to completion before the next. */
function compareFieldSequence(a: readonly string[], b: readonly string[]): number {
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const result = compareOrdinal(a[index] ?? "", b[index] ?? "");
    if (result !== 0) {
      return result;
    }
  }
  return 0;
}

function compareBySeverity(a: NormalizedFinding, b: NormalizedFinding): number {
  return compareNumeric(SEVERITY_RANK[a.severity], SEVERITY_RANK[b.severity]);
}

function compareByCheckId(a: NormalizedFinding, b: NormalizedFinding): number {
  return compareOrdinal(a.checkId, b.checkId);
}

function compareByResourceIdentity(a: NormalizedFinding, b: NormalizedFinding): number {
  return compareFieldSequence(resourceIdentityFields(a), resourceIdentityFields(b));
}

function compareByTitle(a: NormalizedFinding, b: NormalizedFinding): number {
  return compareOrdinal(a.title, b.title);
}

function compareByEvidence(a: NormalizedFinding, b: NormalizedFinding): number {
  return compareOrdinal(a.evidence, b.evidence);
}

function compareByImpact(a: NormalizedFinding, b: NormalizedFinding): number {
  return compareOrdinal(a.impact, b.impact);
}

function compareByRecommendation(a: NormalizedFinding, b: NormalizedFinding): number {
  return compareOrdinal(a.recommendation, b.recommendation);
}

function compareByAuditedAt(a: NormalizedFinding, b: NormalizedFinding): number {
  return compareOrdinal(a.auditedAt, b.auditedAt);
}

function compareByAutoRemediable(a: NormalizedFinding, b: NormalizedFinding): number {
  return compareNumeric(Number(a.autoRemediable), Number(b.autoRemediable));
}

function chain(...comparators: readonly FindingComparator[]): FindingComparator {
  return (a, b) => {
    for (const comparator of comparators) {
      const result = comparator(a, b);
      if (result !== 0) {
        return result;
      }
    }
    return 0;
  };
}

/**
 * Applied, in this fixed order, after whichever primary key a sort option
 * selects -- further deterministic tie-breakers so that two findings
 * sharing the same severity, check ID, and resource identity, but
 * differing in some other displayed field, never compare equal merely
 * because the first three keys matched.
 */
const FULL_DISAMBIGUATION_TAIL: readonly FindingComparator[] = [
  compareByTitle,
  compareByEvidence,
  compareByImpact,
  compareByRecommendation,
  compareByAuditedAt,
  compareByAutoRemediable,
];

const COMPARATOR_BY_SORT_OPTION: Readonly<Record<SortOption, FindingComparator>> = {
  severity: chain(compareBySeverity, compareByCheckId, compareByResourceIdentity, ...FULL_DISAMBIGUATION_TAIL),
  checkId: chain(compareByCheckId, compareBySeverity, compareByResourceIdentity, ...FULL_DISAMBIGUATION_TAIL),
  resource: chain(compareByResourceIdentity, compareBySeverity, compareByCheckId, ...FULL_DISAMBIGUATION_TAIL),
};

/**
 * Generic over `T` (constrained to `NormalizedFinding`) purely so the
 * return type matches whatever concrete finding type was passed in --
 * e.g. calling this with `NormalizedKubernetesFinding[]` returns
 * `NormalizedKubernetesFinding[]`, not the wider union. This reflects
 * what the function actually does (reorder references, never construct
 * new or different objects); it does not change its behavior.
 */
export function sortFindings<T extends NormalizedFinding>(
  findings: readonly T[],
  sortOption: SortOption,
): T[] {
  return [...findings].sort(COMPARATOR_BY_SORT_OPTION[sortOption]);
}
