/**
 * Sanitized, public comparison errors. Mirrors the design of
 * `../report-import/errors.ts`: every error thrown out of this feature is
 * constructed only from a fixed `ComparisonErrorCode`, with the message
 * always looked up from a small, fixed table -- there is no way to
 * construct a `ComparisonError` carrying a caller-supplied message or a
 * report-derived value (a timestamp, a URL, a project path, a cluster
 * context), so a rejected comparison can never leak report content into an
 * error.
 */

export type ComparisonErrorCode = "mixed_platform" | "non_positive_time_range" | "incompatible_target";

const SAFE_MESSAGES: Readonly<Record<ComparisonErrorCode, string>> = {
  mixed_platform: "Both reports must be from the same platform to be compared.",
  non_positive_time_range:
    "The newer report's timestamp must be strictly later than the older report's timestamp.",
  incompatible_target: "Both reports must describe the same audited target to be compared.",
};

export class ComparisonError extends Error {
  readonly code: ComparisonErrorCode;

  constructor(code: ComparisonErrorCode) {
    super(SAFE_MESSAGES[code]);
    this.name = "ComparisonError";
    this.code = code;
  }
}
