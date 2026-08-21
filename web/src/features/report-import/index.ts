/**
 * Public surface of the report-import feature (Phase 3C: schemas and
 * browser adapters only -- no UI, no file I/O, no network, no storage).
 */

export { MAX_FINDINGS_PER_REPORT, MAX_REPORT_FILE_BYTES } from "./constants";
export { ReportValidationError, type ReportValidationErrorCode } from "./errors";
export {
  assertReportFileSize,
  parseGitLabReport,
  parseKubernetesReport,
  parseReport,
} from "./parsers";
export type {
  GitLabResourceKind,
  KubernetesResourceKind,
  NormalizedFinding,
  NormalizedGitLabFinding,
  NormalizedGitLabReport,
  NormalizedGitLabTarget,
  NormalizedKubernetesFinding,
  NormalizedKubernetesReport,
  NormalizedKubernetesTarget,
  NormalizedSummary,
  NormalizedWebReport,
  Severity,
} from "./types";
