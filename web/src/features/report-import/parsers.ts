/**
 * Pure, deterministic parsers from untrusted, already-parsed JSON to a
 * `NormalizedWebReport`.
 *
 * None of the functions in this module mutate their input, access the
 * network, access any browser storage, log report contents, or persist
 * anything -- they only read `json` and construct new, independent output
 * objects. They perform no file I/O either: `parseKubernetesReport`/
 * `parseGitLabReport`/`parseReport` all accept an already-`JSON.parse`d
 * value, never a `File`/`FileReader`/raw text (that belongs to the Phase
 * 3G import UI, which will call `assertReportFileSize` below before ever
 * reading a selected file's contents).
 */

import { ReportValidationError } from "./errors";
import { MAX_FINDINGS_PER_REPORT, MAX_REPORT_FILE_BYTES } from "./constants";
import {
  gitlabReportSchema,
  kubernetesReportSchema,
  type RawGitLabFinding,
  type RawKubernetesFinding,
} from "./schemas";
import type {
  NormalizedGitLabFinding,
  NormalizedGitLabReport,
  NormalizedKubernetesFinding,
  NormalizedKubernetesReport,
  NormalizedSummary,
  NormalizedWebReport,
  Severity,
} from "./types";

/**
 * Rejects an oversized `findings` array before any per-finding schema
 * validation runs, so a 10,001-finding file is rejected cheaply and with
 * the specific `too_many_findings` code, rather than surfacing as a slow,
 * generic `invalid_report` after fully validating every finding. Reads
 * only `candidate.findings.length` -- never any finding's content -- so it
 * cannot leak report data even indirectly.
 */
function assertFindingsCountWithinLimit(candidate: unknown): void {
  if (typeof candidate !== "object" || candidate === null || Array.isArray(candidate)) {
    return;
  }
  const findings = (candidate as Record<string, unknown>).findings;
  if (Array.isArray(findings) && findings.length > MAX_FINDINGS_PER_REPORT) {
    throw new ReportValidationError("too_many_findings");
  }
}

/**
 * Recomputes severity counts from `findings` and rejects the report if any
 * of the four counts disagrees with `supplied` -- the file's own `summary`
 * object is never trusted as given. Returns the recomputed counts plus a
 * derived `total` (the Python `AuditSummary.total` property is never
 * serialized on the wire, so it is always derived here, never read from
 * input).
 */
function recomputeAndVerifySummary(
  findings: ReadonlyArray<{ severity: Severity }>,
  supplied: { critical: number; high: number; medium: number; low: number },
): NormalizedSummary {
  const recomputed = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const finding of findings) {
    recomputed[finding.severity] += 1;
  }
  if (
    recomputed.critical !== supplied.critical ||
    recomputed.high !== supplied.high ||
    recomputed.medium !== supplied.medium ||
    recomputed.low !== supplied.low
  ) {
    throw new ReportValidationError("summary_mismatch");
  }
  return {
    critical: recomputed.critical,
    high: recomputed.high,
    medium: recomputed.medium,
    low: recomputed.low,
    total: recomputed.critical + recomputed.high + recomputed.medium + recomputed.low,
  };
}

function normalizeKubernetesFinding(finding: RawKubernetesFinding): NormalizedKubernetesFinding {
  return {
    platform: "kubernetes",
    checkId: finding.check_id,
    title: finding.title,
    severity: finding.severity,
    clusterContext: finding.cluster_context,
    namespace: finding.namespace,
    resourceKind: finding.resource_kind,
    resourceName: finding.resource_name,
    containerName: finding.container_name,
    evidence: finding.evidence,
    impact: finding.impact,
    recommendation: finding.recommendation,
    autoRemediable: finding.auto_remediable,
    auditedAt: finding.audited_at,
  };
}

function normalizeGitLabFinding(finding: RawGitLabFinding): NormalizedGitLabFinding {
  return {
    platform: "gitlab",
    checkId: finding.check_id,
    title: finding.title,
    severity: finding.severity,
    projectPath: finding.project_path,
    resourceKind: finding.resource_kind,
    resourceName: finding.resource_name,
    jobName: finding.job_name,
    evidence: finding.evidence,
    impact: finding.impact,
    recommendation: finding.recommendation,
    autoRemediable: finding.auto_remediable,
    auditedAt: finding.audited_at,
  };
}

/**
 * Validates `json` against the released Kubernetes `report.json` contract
 * and normalizes it. `json` must be exactly a Kubernetes `AuditReport`
 * shape -- in particular, it must not carry a `platform` field at all (the
 * strict schema rejects any unrecognized key, `platform` included), so an
 * object combining Kubernetes and GitLab fields is always rejected here,
 * never silently accepted as a hybrid.
 */
export function parseKubernetesReport(json: unknown): NormalizedKubernetesReport {
  assertFindingsCountWithinLimit(json);

  const result = kubernetesReportSchema.safeParse(json);
  if (!result.success) {
    throw new ReportValidationError("invalid_report");
  }
  const raw = result.data;

  const summary = recomputeAndVerifySummary(raw.findings, raw.summary);

  return {
    platform: "kubernetes",
    generatedAt: raw.generated_at,
    target: {
      clusterContext: raw.cluster_context,
      namespaceFilter: raw.namespace_filter,
    },
    findings: raw.findings.map(normalizeKubernetesFinding),
    summary,
  };
}

/**
 * Validates `json` against the released GitLab `report.json` contract and
 * normalizes it. `json` must carry `platform: "gitlab"` and satisfy every
 * other GitLab-specific constraint (including the non-empty-string fields
 * the released model requires) -- a `platform: "gitlab"` object that fails
 * any other part of the GitLab shape is rejected outright and is never
 * reinterpreted as a Kubernetes report.
 */
export function parseGitLabReport(json: unknown): NormalizedGitLabReport {
  assertFindingsCountWithinLimit(json);

  const result = gitlabReportSchema.safeParse(json);
  if (!result.success) {
    throw new ReportValidationError("invalid_report");
  }
  const raw = result.data;

  const summary = recomputeAndVerifySummary(raw.findings, raw.summary);

  return {
    platform: "gitlab",
    generatedAt: raw.generated_at,
    target: {
      gitlabUrl: raw.gitlab_url,
      projectId: raw.project_id,
      projectPath: raw.project_path,
      defaultBranch: raw.default_branch,
    },
    findings: raw.findings.map(normalizeGitLabFinding),
    summary,
  };
}

/**
 * Deterministic platform dispatch, per docs/milestones/
 * v0.3.0-interactive-web-demo.md section E:
 *
 * - An own top-level `platform: "gitlab"` property selects the GitLab
 *   parser -- and only the GitLab parser; a `platform: "gitlab"` object
 *   that fails GitLab validation is rejected, never retried against the
 *   Kubernetes schema.
 * - A top-level object with no *own* `platform` property is eligible for
 *   Kubernetes parsing (and is then fully validated by
 *   `parseKubernetesReport`, which itself rejects any object carrying a
 *   stray `platform` key or any other unrecognized field). An *inherited*
 *   `platform` property (e.g. from a prototype) is deliberately ignored --
 *   dispatch reads only `json`'s own property, via `Object.hasOwn`, never
 *   whatever a plain property lookup would resolve through the prototype
 *   chain.
 * - Any other own `platform` value -- including `undefined` or `null`
 *   explicitly set as the value of an own `platform` key, or a string such
 *   as `"kubernetes"` -- or any input that is not a plain object (an array,
 *   `null`, a string, etc.), is unsupported.
 */
export function parseReport(json: unknown): NormalizedWebReport {
  if (json === null || typeof json !== "object" || Array.isArray(json)) {
    throw new ReportValidationError("unsupported_report");
  }

  if (!Object.hasOwn(json, "platform")) {
    return parseKubernetesReport(json);
  }

  const platform = (json as Record<string, unknown>).platform;
  if (platform === "gitlab") {
    return parseGitLabReport(json);
  }
  throw new ReportValidationError("unsupported_report");
}

/**
 * Validates a candidate file size in bytes, ahead of ever reading a
 * selected file's contents. Only integer sizes from `0` through exactly
 * `MAX_REPORT_FILE_BYTES` are accepted; a negative, fractional, or
 * non-finite (`NaN`/`Infinity`) value, or a value over the limit, is
 * rejected with the same sanitized `file_too_large` code. This function
 * does not itself touch a `File`/`FileReader`/Blob -- Phase 3G's import UI
 * calls it with a size it has already obtained.
 */
export function assertReportFileSize(byteLength: number): void {
  if (!Number.isInteger(byteLength) || byteLength < 0 || byteLength > MAX_REPORT_FILE_BYTES) {
    throw new ReportValidationError("file_too_large");
  }
}
