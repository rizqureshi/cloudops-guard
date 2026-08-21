/**
 * Sanitized, public validation errors for the report-import layer.
 *
 * A report file is untrusted input (see CLAUDE.md, "Web application
 * invariants"). Every error thrown out of this module -- and everywhere
 * else in `report-import` -- must be one of these, constructed only from a
 * fixed `ReportValidationErrorCode`. The message is always looked up from a
 * small, fixed table below; there is deliberately no way to construct a
 * `ReportValidationError` with a caller-supplied message, cause, or any
 * other payload. This is a structural guarantee, not just a convention:
 * nothing in this module ever has the opportunity to copy a raw input
 * value, a Zod issue, a field value, evidence text, a resource name, or a
 * URL/path taken from a report into a thrown error, because no code path
 * here accepts such a value as an error argument in the first place.
 */

export type ReportValidationErrorCode =
  | "unsupported_report"
  | "invalid_report"
  | "summary_mismatch"
  | "too_many_findings"
  | "file_too_large";

const SAFE_MESSAGES: Readonly<Record<ReportValidationErrorCode, string>> = {
  unsupported_report: "This file is not a supported CloudOps Guard report.",
  invalid_report: "This file does not match a supported CloudOps Guard report format.",
  summary_mismatch: "The report's summary counts do not match its findings.",
  too_many_findings: "This report has more findings than can be processed.",
  file_too_large: "This file is larger than the supported size limit.",
};

export class ReportValidationError extends Error {
  readonly code: ReportValidationErrorCode;

  constructor(code: ReportValidationErrorCode) {
    super(SAFE_MESSAGES[code]);
    this.name = "ReportValidationError";
    this.code = code;
  }
}
