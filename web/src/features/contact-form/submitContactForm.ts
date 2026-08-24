/**
 * The single client-side entry point that talks to `/api/contact`. Never
 * called from any report-related code (see the isolation test).
 *
 * No value passed in or received back is ever logged: a network failure,
 * a non-JSON response, or an unexpected status/body pairing all collapse
 * to the fixed `"unexpected_error"` result, with the original error/value
 * discarded.
 *
 * The HTTP status is treated as part of the contract, not decoration: a
 * response body is only trusted when its shape *and* its status match one
 * of the Worker's own fixed, documented pairings (`worker/contact.ts`).
 * A status/body mismatch -- e.g. a `200` carrying an error body, or a
 * `500` carrying `{ ok: true }` -- is never trusted as success or as any
 * other known outcome; it is `"unexpected_error"`, the same as a network
 * failure. This was fixed after an independently reproduced bug: `500` +
 * `{ ok: true }` used to be accepted as `"success"` because only the
 * response body's shape was checked, never the status.
 */

import { z } from "zod";

import type { ContactFormInput } from "./contract";
import type { ContactApiErrorCode } from "./responses";

export type ContactSubmissionResult =
  | { readonly kind: "success" }
  | { readonly kind: "validation_error"; readonly message: string }
  | { readonly kind: "temporarily_unavailable"; readonly fallbackEmail: string | null }
  | { readonly kind: "unexpected_error" };

const ERROR_MESSAGES: Readonly<Record<ContactApiErrorCode, string>> = {
  invalid_request: "Please check the form for errors and try again.",
  origin_rejected: "This request could not be verified. Please reload the page and try again.",
  payload_too_large: "Your message is too long. Please shorten it and try again.",
  unsupported_content_type: "This request could not be processed. Please reload the page and try again.",
  unsupported_content_encoding: "This request could not be processed. Please reload the page and try again.",
  verification_failed: "We couldn't verify you're not a robot. Please try the checkbox again.",
  method_not_allowed: "This request could not be processed. Please reload the page and try again.",
  not_found: "This request could not be processed. Please reload the page and try again.",
  temporarily_unavailable: "Message delivery is temporarily unavailable.",
};

const emailSchema = z.string().max(254).pipe(z.email());

/** `null` unless `value` is a syntactically valid, bounded plain email address. */
function sanitizedFallbackEmail(value: string | undefined): string | null {
  if (value === undefined) {
    return null;
  }
  const result = emailSchema.safeParse(value);
  return result.success ? result.data : null;
}

/** Every error code the Worker returns under a fixed `4xx` status (never `503`). */
const FIXED_STATUS_ERROR_CODES = [
  "invalid_request",
  "origin_rejected",
  "payload_too_large",
  "unsupported_content_type",
  "unsupported_content_encoding",
  "verification_failed",
  "method_not_allowed",
  "not_found",
] as const;

/**
 * The exact, strict response-body shape -- a `z.union` of the three
 * mutually exclusive alternatives the Worker ever produces. `fallbackEmail`
 * is only a valid field alongside `error: "temporarily_unavailable"`; on
 * every other alternative it is an unrecognized extra field and the whole
 * object is rejected, exactly like any other unknown field would be.
 */
const contactApiResponseSchema = z.union([
  z.strictObject({ ok: z.literal(true) }),
  z.strictObject({ ok: z.literal(false), error: z.enum(FIXED_STATUS_ERROR_CODES) }),
  z.strictObject({
    ok: z.literal(false),
    error: z.literal("temporarily_unavailable"),
    fallbackEmail: z.string().optional(),
  }),
]);

/**
 * The Worker's own fixed status/error-code pairings
 * (`worker/contact.ts`) -- the single source of truth this function
 * checks a response against. A status not listed here, or a body whose
 * `error` does not appear in the listed set for that exact status, is
 * always `"unexpected_error"`.
 */
const ERROR_CODES_BY_STATUS: Readonly<Record<number, readonly ContactApiErrorCode[]>> = {
  400: ["invalid_request", "verification_failed"],
  403: ["origin_rejected"],
  404: ["not_found"],
  405: ["method_not_allowed"],
  413: ["payload_too_large"],
  415: ["unsupported_content_type", "unsupported_content_encoding"],
};

export interface SubmitContactFormOptions {
  /** Injectable for tests; defaults to the global `fetch`. */
  readonly fetchImpl?: typeof fetch;
}

export async function submitContactForm(
  input: ContactFormInput,
  options: SubmitContactFormOptions = {},
): Promise<ContactSubmissionResult> {
  const doFetch = options.fetchImpl ?? fetch;

  let response: Response;
  try {
    response = await doFetch("/api/contact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  } catch {
    return { kind: "unexpected_error" };
  }

  let rawBody: unknown;
  try {
    rawBody = await response.json();
  } catch {
    return { kind: "unexpected_error" };
  }

  const parsedBody = contactApiResponseSchema.safeParse(rawBody);
  if (!parsedBody.success) {
    return { kind: "unexpected_error" };
  }
  const body = parsedBody.data;
  const status = response.status;

  if (status === 200) {
    return body.ok ? { kind: "success" } : { kind: "unexpected_error" };
  }

  if (status === 503) {
    return !body.ok && body.error === "temporarily_unavailable"
      ? { kind: "temporarily_unavailable", fallbackEmail: sanitizedFallbackEmail(body.fallbackEmail) }
      : { kind: "unexpected_error" };
  }

  const permittedCodes = ERROR_CODES_BY_STATUS[status];
  if (!permittedCodes || body.ok || !permittedCodes.includes(body.error)) {
    return { kind: "unexpected_error" };
  }

  return { kind: "validation_error", message: ERROR_MESSAGES[body.error] };
}
