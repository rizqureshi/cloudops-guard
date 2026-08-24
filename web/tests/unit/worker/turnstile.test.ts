import { describe, expect, it, vi } from "vitest";

import { verifyTurnstileToken } from "../../../worker/turnstile";

const BASE_PARAMS = {
  token: "a-token",
  secretKey: "a-secret",
  expectedHostname: "cloudopsguard.example",
  expectedAction: "pilot_request",
};

function fakeFetch(response: Partial<Response> & { json?: () => Promise<unknown> }): typeof fetch {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => ({}), ...response }) as unknown as typeof fetch;
}

describe("verifyTurnstileToken: never calls the real Siteverify service", () => {
  it("uses only the injected fetchImpl, never the global fetch", async () => {
    const globalFetchSpy = vi.spyOn(globalThis, "fetch");
    const fetchImpl = fakeFetch({ json: async () => ({ success: true, hostname: BASE_PARAMS.expectedHostname, action: BASE_PARAMS.expectedAction }) });

    await verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl });

    expect(globalFetchSpy).not.toHaveBeenCalled();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    globalFetchSpy.mockRestore();
  });

  it("posts to the real Siteverify URL with the secret and token, never the visitor IP", async () => {
    const fetchImpl = fakeFetch({ json: async () => ({ success: true, hostname: BASE_PARAMS.expectedHostname, action: BASE_PARAMS.expectedAction }) });

    await verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl });

    const [url, init] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://challenges.cloudflare.com/turnstile/v0/siteverify");
    expect(init.method).toBe("POST");
    const body = init.body as string;
    expect(body).toContain("secret=a-secret");
    expect(body).toContain("response=a-token");
    expect(body).not.toContain("remoteip");
  });
});

describe("verifyTurnstileToken: success", () => {
  it("returns true when success is true and hostname/action match", async () => {
    const fetchImpl = fakeFetch({ json: async () => ({ success: true, hostname: BASE_PARAMS.expectedHostname, action: BASE_PARAMS.expectedAction }) });
    await expect(verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl })).resolves.toBe(true);
  });
});

describe("verifyTurnstileToken: failure modes all collapse to false", () => {
  it("returns false on network failure", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error("network down")) as unknown as typeof fetch;
    await expect(verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl })).resolves.toBe(false);
  });

  it("returns false on a non-2xx response", async () => {
    const fetchImpl = fakeFetch({ ok: false });
    await expect(verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl })).resolves.toBe(false);
  });

  it("returns false on malformed JSON", async () => {
    const fetchImpl = fakeFetch({
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    });
    await expect(verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl })).resolves.toBe(false);
  });

  it("returns false when success is not true", async () => {
    const fetchImpl = fakeFetch({ json: async () => ({ success: false }) });
    await expect(verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl })).resolves.toBe(false);
  });

  it("returns false on a hostname mismatch", async () => {
    const fetchImpl = fakeFetch({
      json: async () => ({ success: true, hostname: "attacker.example", action: BASE_PARAMS.expectedAction }),
    });
    await expect(verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl })).resolves.toBe(false);
  });

  it("returns false on an action mismatch", async () => {
    const fetchImpl = fakeFetch({
      json: async () => ({ success: true, hostname: BASE_PARAMS.expectedHostname, action: "feedback" }),
    });
    await expect(verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl })).resolves.toBe(false);
  });

  it("returns false when the response is not a JSON object", async () => {
    const fetchImpl = fakeFetch({ json: async () => "not an object" });
    await expect(verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl })).resolves.toBe(false);
  });

  it("returns false and aborts the request on timeout", async () => {
    const fetchImpl = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      });
    }) as unknown as typeof fetch;

    const result = await verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl, timeoutMs: 5 });
    expect(result).toBe(false);
  });
});

describe("verifyTurnstileToken: single attempt only", () => {
  it("calls fetch exactly once, even on failure -- no retry", async () => {
    const fetchImpl = fakeFetch({ json: async () => ({ success: false }) });
    await verifyTurnstileToken({ ...BASE_PARAMS, fetchImpl });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
