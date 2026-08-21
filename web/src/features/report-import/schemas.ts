/**
 * Zod schemas mirroring the currently released Kubernetes and GitLab
 * `report.json` contracts, field-for-field, as produced by
 * `src/cloudops_guard/models.py` (`AuditReport`/`Finding`,
 * `GitLabAuditReport`/`GitLabFinding`) and `src/cloudops_guard/reports/
 * generator.py`. These schemas deliberately support only that one released
 * contract per platform -- there is no versioning or backward-compatibility
 * layer here. If a future CloudOps Guard release changes the Python report
 * contract, these schemas must be updated to match; they are not expected
 * to gracefully degrade for an older or newer shape.
 *
 * Every object schema is a strict Zod object (`z.strictObject`): an unknown
 * field at the report, summary, or finding level is a validation failure,
 * not silently ignored data. Every scalar type is exactly what the JSON
 * contract produces -- no coercion (a numeric string is not a number, `0`/`1`
 * is not a boolean, and so on).
 */

import { z } from "zod";

import { MAX_FINDINGS_PER_REPORT } from "./constants";

/**
 * RFC 3339 / ISO-8601 date-time, requiring an explicit `Z` or numeric
 * offset. Rejects naive (offset-free) timestamps, date-only strings, and
 * calendar-impossible dates (e.g. month 13, February 30, hour 25) -- this
 * is Zod's own dedicated ISO date-time format check, not `Date.parse()`,
 * which is far more permissive and would silently "repair" many of the
 * inputs this schema must reject.
 */
const isoDateTimeSchema = z.iso.datetime({ offset: true });

export const severitySchema = z.enum(["critical", "high", "medium", "low"]);

/**
 * The raw `summary` object exactly as serialized by Pydantic's
 * `AuditSummary` -- note `total` is a Python `@property`, never serialized,
 * so it must not appear here (an incoming `total` key would be rejected by
 * this strict schema as an unknown field).
 */
export const rawSummarySchema = z.strictObject({
  critical: z.number().int().nonnegative(),
  high: z.number().int().nonnegative(),
  medium: z.number().int().nonnegative(),
  low: z.number().int().nonnegative(),
});

// --- Kubernetes ---------------------------------------------------------

export const kubernetesResourceKindSchema = z.enum(["Namespace", "Pod", "Deployment"]);

/**
 * Mirrors `Finding` in models.py exactly. The released Kubernetes model
 * imposes no non-empty-string constraint on any of these string fields
 * (unlike the GitLab model below) -- this schema deliberately does not add
 * one either.
 */
export const kubernetesFindingSchema = z.strictObject({
  check_id: z.string(),
  title: z.string(),
  severity: severitySchema,
  cluster_context: z.string(),
  namespace: z.string(),
  resource_kind: kubernetesResourceKindSchema,
  resource_name: z.string(),
  container_name: z.string().nullable(),
  evidence: z.string(),
  impact: z.string(),
  recommendation: z.string(),
  auto_remediable: z.boolean(),
  audited_at: isoDateTimeSchema,
});

/** Mirrors `AuditReport` in models.py exactly -- note: no `platform` field. */
export const kubernetesReportSchema = z.strictObject({
  cluster_context: z.string(),
  namespace_filter: z.string().nullable(),
  generated_at: isoDateTimeSchema,
  findings: z.array(kubernetesFindingSchema).max(MAX_FINDINGS_PER_REPORT),
  summary: rawSummarySchema,
});

export type RawKubernetesFinding = z.infer<typeof kubernetesFindingSchema>;
export type RawKubernetesReport = z.infer<typeof kubernetesReportSchema>;

// --- GitLab ---------------------------------------------------------------

export const gitlabResourceKindSchema = z.enum([
  "Project",
  "ProtectedBranch",
  "CIJob",
  "CIService",
]);

/**
 * Mirrors `GitLabFinding` in models.py exactly, including its non-empty
 * `min_length=1` constraints on `check_id`, `project_path`, `resource_name`,
 * and (when non-null) `job_name`.
 */
export const gitlabFindingSchema = z.strictObject({
  check_id: z.string().min(1),
  title: z.string(),
  severity: severitySchema,
  project_path: z.string().min(1),
  resource_kind: gitlabResourceKindSchema,
  resource_name: z.string().min(1),
  job_name: z.string().min(1).nullable(),
  evidence: z.string(),
  impact: z.string(),
  recommendation: z.string(),
  auto_remediable: z.boolean(),
  audited_at: isoDateTimeSchema,
});

/**
 * Mirrors `GitLabAuditReport` in models.py exactly, including its
 * non-empty `min_length=1` constraints on `gitlab_url`, `project_path`, and
 * `default_branch`, and `project_id`'s `gt=0` constraint.
 */
export const gitlabReportSchema = z.strictObject({
  platform: z.literal("gitlab"),
  gitlab_url: z.string().min(1),
  project_id: z.number().int().positive(),
  project_path: z.string().min(1),
  default_branch: z.string().min(1),
  generated_at: isoDateTimeSchema,
  findings: z.array(gitlabFindingSchema).max(MAX_FINDINGS_PER_REPORT),
  summary: rawSummarySchema,
});

export type RawGitLabFinding = z.infer<typeof gitlabFindingSchema>;
export type RawGitLabReport = z.infer<typeof gitlabReportSchema>;
