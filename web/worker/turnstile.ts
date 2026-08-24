/**
 * Server-side Cloudflare Turnstile verification (Phase 3I). Every accepted
 * request is verified exactly once against Cloudflare's Siteverify
 * endpoint -- no caching, no retry on an ambiguous response, and no visitor
 * IP is sent or retained (`remoteip` is optional and deliberately omitted).
 */

const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
const DEFAULT_TIMEOUT_MS = 5000;

export interface VerifyTurnstileTokenParams {
  readonly token: string;
  readonly secretKey: string;
  readonly expectedHostname: string;
  readonly expectedAction: string;
  /** Injectable for tests; defaults to the global `fetch`. Real tests never call the real Siteverify service. */
  readonly fetchImpl?: typeof fetch;
  readonly timeoutMs?: number;
}

interface SiteverifyResponseShape {
  readonly success?: unknown;
  readonly hostname?: unknown;
  readonly action?: unknown;
}

function isSiteverifyResponseShape(value: unknown): value is SiteverifyResponseShape {
  return typeof value === "object" && value !== null;
}

/**
 * Network failure, a timeout, a non-2xx status, malformed JSON,
 * `success !== true`, a hostname mismatch, or an action mismatch are all
 * treated as failure -- uniformly `false`, with no distinguishing detail
 * ever surfaced to the caller (see `contact.ts`, which maps every failure
 * to the same sanitized `verification_failed` response).
 */
export async function verifyTurnstileToken(params: VerifyTurnstileTokenParams): Promise<boolean> {
  const doFetch = params.fetchImpl ?? fetch;
  const timeoutMs = params.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const body = new URLSearchParams({ secret: params.secretKey, response: params.token });
    const response = await doFetch(SITEVERIFY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
      signal: controller.signal,
    });

    if (!response.ok) {
      return false;
    }

    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      return false;
    }

    if (!isSiteverifyResponseShape(payload)) {
      return false;
    }
    if (payload.success !== true) {
      return false;
    }
    if (typeof payload.hostname !== "string" || payload.hostname !== params.expectedHostname) {
      return false;
    }
    if (typeof payload.action !== "string" || payload.action !== params.expectedAction) {
      return false;
    }
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timeoutId);
  }
}
