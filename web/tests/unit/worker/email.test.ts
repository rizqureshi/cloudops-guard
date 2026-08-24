import { describe, expect, it, vi } from "vitest";

import { sendContactEmail } from "../../../worker/email";

const BASE_PARAMS = {
  toEmail: "contact@cloudopsguard.example",
  fromEmail: "no-reply@cloudopsguard.example",
  formType: "pilot_request" as const,
  name: "Ada Lovelace",
  workEmail: "ada@example.com",
  company: "Analytical Engines Ltd",
  message: "We'd like to run a pilot audit.",
};

describe("sendContactEmail: anti-open-relay guarantees", () => {
  it("sends to/from exactly the configured addresses, never a visitor-supplied one", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    await sendContactEmail({ email: { send }, ...BASE_PARAMS });

    expect(send).toHaveBeenCalledTimes(1);
    const message = send.mock.calls[0]![0];
    expect(message.to).toBe(BASE_PARAMS.toEmail);
    expect(message.from).toBe(BASE_PARAMS.fromEmail);
  });

  it("never sends an acknowledgement email to the visitor's own workEmail address", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    await sendContactEmail({ email: { send }, ...BASE_PARAMS });

    const calls = send.mock.calls;
    expect(calls).toHaveLength(1);
    expect(calls[0]![0].to).not.toBe(BASE_PARAMS.workEmail);
  });

  it("uses a fixed subject per form type, never a visitor-supplied one", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    await sendContactEmail({ email: { send }, ...BASE_PARAMS, formType: "pilot_request" });
    await sendContactEmail({ email: { send }, ...BASE_PARAMS, formType: "feedback" });

    expect(send.mock.calls[0]![0].subject).toBe("CloudOps Guard pilot request");
    expect(send.mock.calls[1]![0].subject).toBe("CloudOps Guard feedback");
  });

  it("never provides an HTML body -- only plain text", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    await sendContactEmail({ email: { send }, ...BASE_PARAMS });

    const message = send.mock.calls[0]![0];
    expect(message).not.toHaveProperty("html");
    expect(typeof message.text).toBe("string");
  });

  it("places name/work email/company/message only in the plain-text body, never in headers", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    await sendContactEmail({ email: { send }, ...BASE_PARAMS });

    const message = send.mock.calls[0]![0];
    expect(message.text).toContain(BASE_PARAMS.name);
    expect(message.text).toContain(BASE_PARAMS.workEmail);
    expect(message.text).toContain(BASE_PARAMS.company);
    expect(message.text).toContain(BASE_PARAMS.message);
    // Only the fixed keys a structured EmailMessage supports are ever set --
    // no cc/bcc/headers/attachments key exists on the sent message at all.
    expect(Object.keys(message).sort()).toEqual(["from", "subject", "text", "to"]);
  });

  it("never includes the Turnstile token anywhere in the sent message", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    await sendContactEmail({ email: { send }, ...BASE_PARAMS });

    const message = send.mock.calls[0]![0];
    expect(JSON.stringify(message)).not.toContain("turnstile");
    expect(JSON.stringify(message).toLowerCase()).not.toContain("token");
  });

  it("omits the company line entirely when company is undefined", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    await sendContactEmail({ email: { send }, ...BASE_PARAMS, company: undefined });

    const message = send.mock.calls[0]![0];
    expect(message.text).not.toContain("Company:");
  });
});

describe("sendContactEmail: binding failure", () => {
  it("returns false, never throws, when the binding rejects", async () => {
    const send = vi.fn().mockRejectedValue(new Error("SENSITIVE_BINDING_FAILURE_DETAIL"));
    await expect(sendContactEmail({ email: { send }, ...BASE_PARAMS })).resolves.toBe(false);
  });

  it("returns true on a successful send", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    await expect(sendContactEmail({ email: { send }, ...BASE_PARAMS })).resolves.toBe(true);
  });
});

describe("sendContactEmail: no console logging", () => {
  it("never calls console.log or console.error", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const send = vi.fn().mockRejectedValue(new Error("boom"));

    await sendContactEmail({ email: { send }, ...BASE_PARAMS });

    expect(logSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
    logSpy.mockRestore();
    errorSpy.mockRestore();
  });
});
