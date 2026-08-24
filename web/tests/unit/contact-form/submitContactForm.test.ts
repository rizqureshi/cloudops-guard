import { describe, expect, it, vi } from "vitest";

import type { ContactFormInput } from "../../../src/features/contact-form/contract";
import { submitContactForm } from "../../../src/features/contact-form/submitContactForm";

const INPUT: ContactFormInput = {
  formType: "pilot_request",
  name: "Ada Lovelace",
  workEmail: "ada@example.com",
  company: "Analytical Engines Ltd",
  message: "We'd like to run a pilot audit.",
  consent: true,
  turnstileToken: "a-token",
};

function fetchReturning(status: number, body: unknown): typeof fetch {
  return vi.fn().mockResolvedValue({
    status,
    json: async () => body,
  }) as unknown as typeof fetch;
}

describe("submitContactForm: request shape", () => {
  it("POSTs to /api/contact with exactly the allowlisted fields and application/json", async () => {
    const fetchImpl = fetchReturning(200, { ok: true });
    await submitContactForm(INPUT, { fetchImpl });

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/contact");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual(INPUT);
  });
});

describe("submitContactForm: success requires status 200 AND the success body", () => {
  it("returns kind 'success' for 200 + { ok: true }", async () => {
    const fetchImpl = fetchReturning(200, { ok: true });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "success" });
  });

  it("regression: 500 + { ok: true } is never success (status is part of the contract)", async () => {
    // Independently reproduced: previously only the body's `ok` boolean was
    // checked, so an unrelated 500 response carrying a look-alike success
    // body was wrongly accepted as success.
    const fetchImpl = fetchReturning(500, { ok: true });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("regression: 503 + { ok: true } is never success", async () => {
    const fetchImpl = fetchReturning(503, { ok: true });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("a 200 carrying an error body is unexpected, not success and not a validation error", async () => {
    const fetchImpl = fetchReturning(200, { ok: false, error: "invalid_request" });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });
});

describe("submitContactForm: every legitimate Worker status/body pairing retains its intended behavior", () => {
  it.each([
    [400, "invalid_request"],
    [400, "verification_failed"],
    [403, "origin_rejected"],
    [404, "not_found"],
    [405, "method_not_allowed"],
    [413, "payload_too_large"],
    [415, "unsupported_content_type"],
    [415, "unsupported_content_encoding"],
  ] as const)("status %i with error %s maps to a validation_error with a fixed message", async (status, error) => {
    const fetchImpl = fetchReturning(status, { ok: false, error });
    const result = await submitContactForm(INPUT, { fetchImpl });
    expect(result.kind).toBe("validation_error");
  });

  it("maps verification_failed's message specifically", async () => {
    const fetchImpl = fetchReturning(400, { ok: false, error: "verification_failed" });
    const result = await submitContactForm(INPUT, { fetchImpl });
    expect(result.kind).toBe("validation_error");
    if (result.kind === "validation_error") {
      expect(result.message).toMatch(/verify/i);
    }
  });
});

describe("submitContactForm: status/error-code mismatches are unexpected, not validation errors", () => {
  it("regression: 200 + temporarily_unavailable is unexpected (that pairing is only valid at 503)", async () => {
    const fetchImpl = fetchReturning(200, { ok: false, error: "temporarily_unavailable" });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("an error code paired with the wrong fixed status is unexpected", async () => {
    // origin_rejected only ever accompanies 403 from the real Worker.
    const fetchImpl = fetchReturning(400, { ok: false, error: "origin_rejected" });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("regression: an unknown/unrecognized error code is unexpected, never a generic validation error", async () => {
    const fetchImpl = fetchReturning(400, { ok: false, error: "some_future_code" });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("an entirely unknown status code is unexpected regardless of body shape", async () => {
    const fetchImpl = fetchReturning(418, { ok: false, error: "invalid_request" });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });
});

describe("submitContactForm: temporarily_unavailable / mailto fallback", () => {
  it("returns the fallback email when it is a syntactically valid address", async () => {
    const fetchImpl = fetchReturning(503, { ok: false, error: "temporarily_unavailable", fallbackEmail: "contact@cloudopsguard.example" });
    const result = await submitContactForm(INPUT, { fetchImpl });
    expect(result).toEqual({ kind: "temporarily_unavailable", fallbackEmail: "contact@cloudopsguard.example" });
  });

  it("discards an invalid fallback email rather than trusting it", async () => {
    const fetchImpl = fetchReturning(503, { ok: false, error: "temporarily_unavailable", fallbackEmail: "not-an-email" });
    const result = await submitContactForm(INPUT, { fetchImpl });
    expect(result).toEqual({ kind: "temporarily_unavailable", fallbackEmail: null });
  });

  it("treats a missing fallback email as null, not a crash", async () => {
    const fetchImpl = fetchReturning(503, { ok: false, error: "temporarily_unavailable" });
    const result = await submitContactForm(INPUT, { fetchImpl });
    expect(result).toEqual({ kind: "temporarily_unavailable", fallbackEmail: null });
  });

  it("regression: fallbackEmail on a non-temporarily_unavailable body is an unrecognized extra field, rejected wholesale", async () => {
    const fetchImpl = fetchReturning(400, { ok: false, error: "invalid_request", fallbackEmail: "contact@cloudopsguard.example" });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });
});

describe("submitContactForm: malformed/extra-field response bodies are rejected", () => {
  it("returns unexpected_error on a network failure", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error("SENSITIVE_NETWORK_DETAIL")) as unknown as typeof fetch;
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("returns unexpected_error on a non-JSON response body", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      status: 200,
      json: async () => {
        throw new SyntaxError("bad json");
      },
    }) as unknown as typeof fetch;
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("returns unexpected_error on a response shaped nothing like the contract", async () => {
    const fetchImpl = fetchReturning(200, { totally: "unexpected" });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("rejects a success body carrying an extra field", async () => {
    const fetchImpl = fetchReturning(200, { ok: true, extra: "field" });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("rejects an error body carrying an extra field", async () => {
    const fetchImpl = fetchReturning(400, { ok: false, error: "invalid_request", extra: "field" });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("rejects a body where ok is not a boolean", async () => {
    const fetchImpl = fetchReturning(200, { ok: "true" });
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("rejects a null response body", async () => {
    const fetchImpl = fetchReturning(200, null);
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });

  it("rejects an array response body", async () => {
    const fetchImpl = fetchReturning(200, [{ ok: true }]);
    await expect(submitContactForm(INPUT, { fetchImpl })).resolves.toEqual({ kind: "unexpected_error" });
  });
});
