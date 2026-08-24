/**
 * Pure(ish) local file import pipeline: takes a browser `File` the visitor
 * selected and produces a `NormalizedWebReport`, or throws a sanitized
 * error. This is the *only* place in the local report explorer that reads
 * file bytes -- everything downstream (state management, rendering) only
 * ever sees the resulting `NormalizedWebReport` or a safe error message.
 *
 * No `fetch`/`XMLHttpRequest`/`WebSocket`/`sendBeacon` call, no object URL,
 * no base64 conversion, and no persistence of any kind happens here or
 * anywhere else in this feature -- the file is read locally
 * (`File.prototype.text()`) and never leaves this browser tab. Raw file
 * text is never retained after `JSON.parse` -- only the local `text`
 * variable exists transiently within this function call and is not stored
 * anywhere.
 */

import { assertReportFileSize, parseReport, ReportValidationError } from "../report-import";
import type { NormalizedWebReport } from "../report-import";
import { LocalImportError } from "./errors";

const JSON_EXTENSION_PATTERN = /\.json$/i;

/**
 * Case-insensitive `.json` filename-extension check. This is the security
 * boundary for "is this the right kind of file" -- never the browser's
 * reported MIME type, which can be empty or inconsistent across browsers
 * and operating systems.
 */
function hasJsonExtension(fileName: string): boolean {
  return JSON_EXTENSION_PATTERN.test(fileName);
}

/**
 * Validates and imports one locally selected file, in this exact order:
 *
 * 1. Case-insensitive `.json` filename-extension check.
 * 2. `assertReportFileSize(file.size)` -- before any content is read.
 * 3. Read the file locally via `File.prototype.text()`.
 * 4. `JSON.parse` the resulting text.
 * 5. Validate/normalize via the existing `parseReport` (schema, summary
 *    consistency, finding-count limit, platform dispatch).
 *
 * A `ReportValidationError` from step 2 or step 5 is already sanitized and
 * is re-thrown unchanged. Any other failure (wrong extension, a read
 * failure, malformed JSON, or any other unexpected exception) becomes a
 * fixed, sanitized `LocalImportError` -- never the filename, never the
 * native `JSON.parse` error text, never a file-system path, never a raw
 * exception message or stack.
 */
export async function importLocalReportFile(file: File): Promise<NormalizedWebReport> {
  try {
    if (!hasJsonExtension(file.name)) {
      throw new LocalImportError("wrong_extension");
    }

    assertReportFileSize(file.size);

    let text: string;
    try {
      text = await file.text();
    } catch {
      throw new LocalImportError("read_failed");
    }

    let parsedJson: unknown;
    try {
      parsedJson = JSON.parse(text);
    } catch {
      throw new LocalImportError("malformed_json");
    }

    return parseReport(parsedJson);
  } catch (error) {
    if (error instanceof LocalImportError || error instanceof ReportValidationError) {
      throw error;
    }
    throw new LocalImportError("unexpected_failure");
  }
}
