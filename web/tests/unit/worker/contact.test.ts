import { afterEach, describe, expect, it, vi } from "vitest";

import { handleContactRequest } from "../../../worker/contact";
import type { ContactWorkerEnv } from "../../../worker/env";
import { MAX_CONTACT_BODY_BYTES } from "../../../worker/readBoundedBody";

const ORIGIN = "https://cloudopsguard.example";
const CONTACT_URL = `${ORIGIN}/api/contact`;

const VALID_PAYLOAD = {
  formType: "pilot_request" as const,
  name: "Ada Lovelace",
  workEmail: "ada@example.com",
  company: "Analytical Engines Ltd",
  message: "We'd like to run a pilot audit.",
  consent: true,
  turnstileToken: "a-real-looking-token",
};

function makeEnv(overrides: Partial<Omit<ContactWorkerEnv, "EMAIL">> & { send?: ReturnType<typeof vi.fn> } = {}): {
  env: ContactWorkerEnv;
  send: ReturnType<typeof vi.fn>;
} {
  const { send: sendOverride, ...envOverrides } = overrides;
  const send = sendOverride ?? vi.fn().mockResolvedValue(undefined);
  const env: ContactWorkerEnv = {
    TURNSTILE_SECRET_KEY: "test-secret",
    TURNSTILE_EXPECTED_HOSTNAME: "cloudopsguard.example",
    EMAIL: { send: send as unknown as ContactWorkerEnv["EMAIL"]["send"] },
    CONTACT_TO_EMAIL: "contact@cloudopsguard.example",
    CONTACT_FROM_EMAIL: "no-reply@cloudopsguard.example",
    ...envOverrides,
  };
  return { env, send };
}

/** Stubs global fetch (only the Turnstile Siteverify call reaches it) to succeed with the given payload's action. */
function stubSuccessfulTurnstile(action: string = VALID_PAYLOAD.formType): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ success: true, hostname: "cloudopsguard.example", action }),
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function stubFailedTurnstile(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ success: false }) });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function jsonRequest(
  body: unknown,
  init: { readonly method?: string; readonly origin?: string | null; readonly path?: string; readonly extraHeaders?: Record<string, string> } = {},
): Request {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...init.extraHeaders };
  if (init.origin !== undefined && init.origin !== null) {
    headers.Origin = init.origin;
  }
  const url = `${ORIGIN}${init.path ?? "/api/contact"}`;
  return new Request(url, {
    method: init.method ?? "POST",
    headers,
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

async function bodyOf(response: Response): Promise<Record<string, unknown>> {
  return (await response.json()) as Record<string, unknown>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("handleContactRequest: path and method", () => {
  it("rejects an unknown path", async () => {
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { path: "/api/other" }), env);
    expect(response.status).toBe(404);
  });

  it("rejects a query-string variation on the exact path", async () => {
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { path: "/api/contact?x=1" }), env);
    expect(response.status).toBe(404);
  });

  it("rejects a non-POST method with an Allow: POST header", async () => {
    const { env } = makeEnv();
    const request = new Request(CONTACT_URL, { method: "GET", headers: { Origin: ORIGIN } });
    const response = await handleContactRequest(request, env);
    expect(response.status).toBe(405);
    expect(response.headers.get("Allow")).toBe("POST");
  });

  it("rejects PUT and DELETE the same way", async () => {
    const { env } = makeEnv();
    for (const method of ["PUT", "DELETE", "PATCH"]) {
      const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { method }), env);
      expect(response.status).toBe(405);
    }
  });
});

describe("handleContactRequest: Origin validation", () => {
  it.each([
    ["a missing Origin header", null],
    ["a malformed Origin header", "not a url"],
    ["the literal string 'null' as Origin", "null"],
    ["a cross-origin Origin", "https://attacker.example"],
    ["a same-hostname-different-scheme Origin", "http://cloudopsguard.example"],
  ] as const)("rejects %s, and never reaches Turnstile or email delivery", async (_label, origin) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { env, send } = makeEnv();

    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin }), env);

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });

  it("accepts an exactly matching Origin", async () => {
    stubSuccessfulTurnstile();
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(response.status).toBe(200);
  });

  it("never reflects the Origin into a CORS header", async () => {
    stubSuccessfulTurnstile();
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(response.headers.get("Access-Control-Allow-Origin")).toBeNull();
  });

  describe("regression: an Origin normalizing down to the right host must still be rejected verbatim", () => {
    // Independently reproduced: `new URL(origin).origin` silently discards
    // a path/credentials/query/fragment/trailing-slash, so an Origin like
    // `https://cloudopsguard.example/not-an-origin` used to normalize down
    // to an accepted value even though no real browser ever sends an
    // Origin header containing a path. Each case below proves both the
    // rejection itself, and that rejection happens before Turnstile or
    // email delivery are ever reached.
    it.each([
      ["a path", `${ORIGIN}/not-an-origin`],
      ["credentials", "https://user:pass@cloudopsguard.example"],
      ["a trailing slash", `${ORIGIN}/`],
      ["a query string", `${ORIGIN}?x=1`],
      ["a fragment", `${ORIGIN}#frag`],
    ])("rejects an Origin containing %s", async (_label, origin) => {
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);
      const { env, send } = makeEnv();

      const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin }), env);

      expect(response.status).toBe(403);
      expect((await bodyOf(response)).error).toBe("origin_rejected");
      expect(fetchMock).not.toHaveBeenCalled();
      expect(send).not.toHaveBeenCalled();
    });
  });
});

describe("handleContactRequest: Content-Type and Content-Encoding", () => {
  it("rejects a missing Content-Type", async () => {
    const { env } = makeEnv();
    const request = new Request(CONTACT_URL, {
      method: "POST",
      headers: { Origin: ORIGIN },
      body: JSON.stringify(VALID_PAYLOAD),
    });
    const response = await handleContactRequest(request, env);
    expect(response.status).toBe(415);
  });

  it("rejects a parameterized Content-Type (charset)", async () => {
    const { env } = makeEnv();
    const response = await handleContactRequest(
      jsonRequest(VALID_PAYLOAD, { origin: ORIGIN, extraHeaders: { "Content-Type": "application/json; charset=utf-8" } }),
      env,
    );
    expect(response.status).toBe(415);
  });

  it("rejects an unrelated Content-Type", async () => {
    const { env } = makeEnv();
    const response = await handleContactRequest(
      jsonRequest(VALID_PAYLOAD, { origin: ORIGIN, extraHeaders: { "Content-Type": "text/plain" } }),
      env,
    );
    expect(response.status).toBe(415);
  });

  it("rejects a request declaring an unsupported Content-Encoding", async () => {
    const { env } = makeEnv();
    const response = await handleContactRequest(
      jsonRequest(VALID_PAYLOAD, { origin: ORIGIN, extraHeaders: { "Content-Encoding": "gzip" } }),
      env,
    );
    expect(response.status).toBe(415);
  });
});

describe("handleContactRequest: body size limit", () => {
  it("rejects an honestly declared oversized Content-Length before reading", async () => {
    const { env } = makeEnv();
    const request = new Request(CONTACT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: ORIGIN, "Content-Length": String(MAX_CONTACT_BODY_BYTES + 1) },
      body: JSON.stringify(VALID_PAYLOAD),
    });
    const response = await handleContactRequest(request, env);
    expect(response.status).toBe(413);
  });

  it("rejects an oversized streamed body with no honest Content-Length", async () => {
    const { env } = makeEnv();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(JSON.stringify({ ...VALID_PAYLOAD, message: "a".repeat(MAX_CONTACT_BODY_BYTES) })));
        controller.close();
      },
    });
    const request = new Request(CONTACT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: ORIGIN },
      body: stream,
      // @ts-expect-error -- duplex is required by the Fetch spec for a streaming body.
      duplex: "half",
    });
    const response = await handleContactRequest(request, env);
    expect(response.status).toBe(413);
  });
});

describe("handleContactRequest: JSON parsing and shape", () => {
  it("rejects malformed JSON", async () => {
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest("{not valid json", { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
  });

  it("rejects a JSON array", async () => {
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest([1, 2, 3], { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
  });

  it("rejects a JSON primitive", async () => {
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest("just a string", { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
  });

  it("rejects missing required fields", async () => {
    const { env } = makeEnv();
    const { message: _message, ...incomplete } = VALID_PAYLOAD;
    const response = await handleContactRequest(jsonRequest(incomplete, { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
  });

  it("rejects an unknown field", async () => {
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest({ ...VALID_PAYLOAD, extra: "x" }, { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
  });

  it("rejects consent that is not literal true", async () => {
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest({ ...VALID_PAYLOAD, consent: "true" }, { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
  });

  it("regression: rejects a whitespace-only message via the shared contract", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { env, send } = makeEnv();
    const response = await handleContactRequest(
      jsonRequest({ ...VALID_PAYLOAD, message: "   \n\t  " }, { origin: ORIGIN }),
      env,
    );
    expect(response.status).toBe(400);
    expect((await bodyOf(response)).error).toBe("invalid_request");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });

  it("accepts a multiline message with meaningful content", async () => {
    stubSuccessfulTurnstile();
    const { env } = makeEnv();
    const response = await handleContactRequest(
      jsonRequest({ ...VALID_PAYLOAD, message: "  Real content here.\n\nMore content.  " }, { origin: ORIGIN }),
      env,
    );
    expect(response.status).toBe(200);
  });
});

describe("handleContactRequest: Turnstile verification", () => {
  it("rejects a missing turnstileToken before ever calling Siteverify", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { env } = makeEnv();
    const { turnstileToken: _turnstileToken, ...withoutToken } = VALID_PAYLOAD;
    const response = await handleContactRequest(jsonRequest(withoutToken, { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects an invalid/failed Turnstile verification", async () => {
    stubFailedTurnstile();
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
    expect((await bodyOf(response)).error).toBe("verification_failed");
  });

  it("sanitizes a Turnstile network/timeout failure to the same verification_failed response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("SENSITIVE_NETWORK_DETAIL")),
    );
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
    const body = await bodyOf(response);
    expect(body.error).toBe("verification_failed");
    expect(JSON.stringify(body)).not.toContain("SENSITIVE_NETWORK_DETAIL");
  });

  it("sanitizes a malformed Siteverify response the same way", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => {
          throw new SyntaxError("bad json");
        },
      }),
    );
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
  });

  it("rejects a hostname mismatch from Siteverify", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ success: true, hostname: "attacker.example", action: VALID_PAYLOAD.formType }) }),
    );
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
  });

  it("rejects an action mismatch (submitted formType vs. Turnstile action) from Siteverify", async () => {
    stubSuccessfulTurnstile("feedback"); // payload's formType is "pilot_request"
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(response.status).toBe(400);
  });
});

describe("handleContactRequest: successful delivery", () => {
  it("delivers exactly one plain-text email for a valid pilot_request submission", async () => {
    stubSuccessfulTurnstile("pilot_request");
    const { env, send } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(response.status).toBe(200);
    expect(await bodyOf(response)).toEqual({ ok: true });
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("delivers exactly one plain-text email for a valid feedback submission", async () => {
    stubSuccessfulTurnstile("feedback");
    const { env, send } = makeEnv();
    const response = await handleContactRequest(
      jsonRequest({ ...VALID_PAYLOAD, formType: "feedback" }, { origin: ORIGIN }),
      env,
    );
    expect(response.status).toBe(200);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]![0].subject).toBe("CloudOps Guard feedback");
  });

  it("sends only to the fixed configured destination", async () => {
    stubSuccessfulTurnstile();
    const { env, send } = makeEnv();
    await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(send.mock.calls[0]![0].to).toBe("contact@cloudopsguard.example");
  });

  it("never lets submitted input reach the email's to/from/subject", async () => {
    stubSuccessfulTurnstile();
    const { env, send } = makeEnv();
    await handleContactRequest(
      jsonRequest({ ...VALID_PAYLOAD, name: "attacker-controlled-header-value" }, { origin: ORIGIN }),
      env,
    );
    const message = send.mock.calls[0]![0];
    expect(message.to).toBe("contact@cloudopsguard.example");
    expect(message.from).toBe("no-reply@cloudopsguard.example");
    expect(message.subject).toBe("CloudOps Guard pilot request");
  });

  it("response headers include Content-Type, Cache-Control, and X-Content-Type-Options", async () => {
    stubSuccessfulTurnstile();
    const { env } = makeEnv();
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(response.headers.get("Content-Type")).toBe("application/json");
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
  });
});

describe("handleContactRequest: email-binding failure", () => {
  it("returns a sanitized temporarily_unavailable response with the configured fallback address", async () => {
    stubSuccessfulTurnstile();
    const send = vi.fn().mockRejectedValue(new Error("SENSITIVE_BINDING_ERROR"));
    const { env } = makeEnv({ send });
    const response = await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);
    expect(response.status).toBe(503);
    const body = await bodyOf(response);
    expect(body.ok).toBe(false);
    expect(body.error).toBe("temporarily_unavailable");
    expect(body.fallbackEmail).toBe("contact@cloudopsguard.example");
    expect(JSON.stringify(body)).not.toContain("SENSITIVE_BINDING_ERROR");
  });
});

describe("handleContactRequest: never reproduces sensitive markers", () => {
  it("no response body ever echoes a submitted value", async () => {
    stubSuccessfulTurnstile();
    const { env } = makeEnv();
    const marker = "UNIQUE_SUBMITTED_MARKER_STRING";
    const response = await handleContactRequest(jsonRequest({ ...VALID_PAYLOAD, message: marker }, { origin: ORIGIN }), env);
    const raw = await response.clone().text();
    expect(raw).not.toContain(marker);
  });

  it("a rejection response never echoes the submitted token", async () => {
    stubFailedTurnstile();
    const { env } = makeEnv();
    const response = await handleContactRequest(
      jsonRequest({ ...VALID_PAYLOAD, turnstileToken: "UNIQUE_TOKEN_MARKER" }, { origin: ORIGIN }),
      env,
    );
    const raw = await response.text();
    expect(raw).not.toContain("UNIQUE_TOKEN_MARKER");
  });
});

describe("handleContactRequest: no console logging", () => {
  it("never calls console.log or console.error across the full success path", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    stubSuccessfulTurnstile();
    const { env } = makeEnv();

    await handleContactRequest(jsonRequest(VALID_PAYLOAD, { origin: ORIGIN }), env);

    expect(logSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
    logSpy.mockRestore();
    errorSpy.mockRestore();
  });

  it("never calls console.log or console.error across a full rejection path", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { env } = makeEnv();

    await handleContactRequest(jsonRequest("{bad json", { origin: ORIGIN }), env);

    expect(logSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
    logSpy.mockRestore();
    errorSpy.mockRestore();
  });
});
