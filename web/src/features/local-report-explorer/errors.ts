/**
 * Sanitized, public errors for local file selection/reading, kept separate
 * from `../report-import/errors.ts` (`ReportValidationError`) because
 * `report-import` is deliberately I/O-free (schemas/parsers only -- see
 * its module docstring). Everything about *getting bytes off the local
 * disk into a string* -- extension checks, `File.text()` failures,
 * `JSON.parse` failures, and any other unexpected exception -- belongs
 * here instead.
 *
 * Mirrors `ReportValidationError`'s design exactly: every error is
 * constructed only from a fixed `LocalImportErrorCode`, with the message
 * always looked up from a small, fixed table below. There is no way to
 * construct a `LocalImportError` carrying a caller-supplied message, a
 * filename, a parser error string, or a stack trace -- nothing in this
 * module ever has the opportunity to copy such a value into a thrown
 * error, because no code path here accepts one as an error argument.
 */

export type LocalImportErrorCode = "wrong_extension" | "malformed_json" | "read_failed" | "unexpected_failure";

const SAFE_MESSAGES: Readonly<Record<LocalImportErrorCode, string>> = {
  wrong_extension: "Only a CloudOps Guard report.json file (a .json file) can be selected here.",
  malformed_json: "This file is not valid JSON.",
  read_failed: "This file could not be read.",
  unexpected_failure: "This file could not be imported.",
};

export class LocalImportError extends Error {
  readonly code: LocalImportErrorCode;

  constructor(code: LocalImportErrorCode) {
    super(SAFE_MESSAGES[code]);
    this.name = "LocalImportError";
    this.code = code;
  }
}
