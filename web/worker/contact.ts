/**
 * The isolated `POST /api/contact` Worker endpoint (Phase 3I) -- the only
 * route this Worker serves. See CLAUDE.md and the isolation test
 * (`web/tests/unit/contact-form/isolation.test.ts`) for the structural
 * guarantee that nothing here reaches `report-import`, `report-workspace`,
 * `local-report-explorer`, `comparison`, `executive-summary`,
 * `demo-controller`, or any synthetic/check-catalogue data, and that no
 * report-related module reaches this file either.
 *
 * Request-processing order (each step short-circuits the rest on failure):
 * path -> method -> same-origin -> Content-Type -> Content-Encoding ->
 * bounded body read -> JSON parse -> object-shape check -> contract
 * validation -> Turnstile verification -> email delivery.
 *
 * Every response is a fixed, sanitized JSON body -- never an echoed
 * submitted value, a Zod issue, a native error message, a Turnstile
 * response, an email-binding error, a secret, or a stack trace. This
 * module contains no `console.log`/`console.error` and never logs a
 * request body or field value.
 */

import { parseContactFormInput } from "../src/features/contact-form/contract";
import type { ContactApiErrorCode } from "../src/features/contact-form/responses";
import type { ContactWorkerEnv } from "./env";
import { sendContactEmail } from "./email";
import { MAX_CONTACT_BODY_BYTES, readBoundedBody } from "./readBoundedBody";
import { verifyTurnstileToken } from "./turnstile";

const CONTACT_PATH = "/api/contact";
const EXACT_JSON_CONTENT_TYPE = "application/json";

const BASE_RESPONSE_HEADERS: Readonly<Record<string, string>> = {
  "Content-Type": "application/json",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
};

function jsonResponse(status: number, body: unknown, extraHeaders?: Readonly<Record<string, string>>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...BASE_RESPONSE_HEADERS, ...extraHeaders },
  });
}

function errorResponse(status: number, error: ContactApiErrorCode, extraHeaders?: Readonly<Record<string, string>>): Response {
  return jsonResponse(status, { ok: false, error }, extraHeaders);
}

/**
 * Compares the *raw* `Origin` header string exactly against the canonical
 * public origin of `request.url` (`new URL(request.url).origin`, e.g.
 * `"https://cloudopsguard.example"`) -- no suffix matching, no substring
 * checks, no wildcard, no reflecting the value back into a CORS header,
 * and no override.
 *
 * Deliberately never parses the Origin header through `new URL(origin)`
 * first: doing so previously let an Origin containing a path, credentials,
 * query, or fragment (e.g. `https://cloudopsguard.example/not-an-origin`)
 * normalize down to just its `.origin` component and pass, since
 * `new URL(...).origin` silently discards everything but scheme/host/port.
 * A real browser's own `Origin` header is always already the bare
 * `scheme://host[:port]` form with no path, credentials, query, fragment,
 * or trailing slash -- so any request whose raw header is not *exactly*
 * that string is rejected outright, never normalized into an accepted
 * value. A missing, malformed, or literal `"null"` Origin (e.g. a
 * sandboxed context) can never equal that canonical string and is
 * rejected the same way as any other mismatch.
 */
function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) {
    return false;
  }
  const requestUrl = new URL(request.url);
  return origin === requestUrl.origin;
}

function hasExactJsonContentType(request: Request): boolean {
  return request.headers.get("content-type") === EXACT_JSON_CONTENT_TYPE;
}

function hasUnsupportedContentEncoding(request: Request): boolean {
  return request.headers.get("content-encoding") !== null;
}

function decodeUtf8Strict(bytes: Uint8Array): string | null {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

export async function handleContactRequest(request: Request, env: ContactWorkerEnv): Promise<Response> {
  const url = new URL(request.url);

  if (url.pathname !== CONTACT_PATH || url.search !== "") {
    return errorResponse(404, "not_found");
  }

  if (request.method !== "POST") {
    return errorResponse(405, "method_not_allowed", { Allow: "POST" });
  }

  if (!isSameOrigin(request)) {
    return errorResponse(403, "origin_rejected");
  }

  if (!hasExactJsonContentType(request)) {
    return errorResponse(415, "unsupported_content_type");
  }

  if (hasUnsupportedContentEncoding(request)) {
    return errorResponse(415, "unsupported_content_encoding");
  }

  const bodyResult = await readBoundedBody(request, MAX_CONTACT_BODY_BYTES);
  if (bodyResult.kind === "too_large") {
    return errorResponse(413, "payload_too_large");
  }
  if (bodyResult.kind === "invalid") {
    return errorResponse(400, "invalid_request");
  }

  const text = decodeUtf8Strict(bodyResult.bytes);
  if (text === null) {
    return errorResponse(400, "invalid_request");
  }

  let parsedJson: unknown;
  try {
    parsedJson = JSON.parse(text);
  } catch {
    return errorResponse(400, "invalid_request");
  }

  if (typeof parsedJson !== "object" || parsedJson === null || Array.isArray(parsedJson)) {
    return errorResponse(400, "invalid_request");
  }

  const validated = parseContactFormInput(parsedJson);
  if (!validated.success) {
    return errorResponse(400, "invalid_request");
  }
  const input = validated.data;

  const turnstileOk = await verifyTurnstileToken({
    token: input.turnstileToken,
    secretKey: env.TURNSTILE_SECRET_KEY,
    expectedHostname: env.TURNSTILE_EXPECTED_HOSTNAME,
    expectedAction: input.formType,
  });
  if (!turnstileOk) {
    return errorResponse(400, "verification_failed");
  }

  const emailSent = await sendContactEmail({
    email: env.EMAIL,
    toEmail: env.CONTACT_TO_EMAIL,
    fromEmail: env.CONTACT_FROM_EMAIL,
    formType: input.formType,
    name: input.name,
    workEmail: input.workEmail,
    company: input.company,
    message: input.message,
  });
  if (!emailSent) {
    return jsonResponse(503, { ok: false, error: "temporarily_unavailable", fallbackEmail: env.CONTACT_TO_EMAIL });
  }

  return jsonResponse(200, { ok: true });
}

const worker = {
  fetch: handleContactRequest,
};

export default worker;
