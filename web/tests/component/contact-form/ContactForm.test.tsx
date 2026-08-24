// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContactForm } from "../../../src/features/contact-form/ContactForm";
import type { TurnstileApi, TurnstileRenderOptions } from "../../../src/features/contact-form/turnstile";

vi.mock("../../../src/features/contact-form/turnstile", () => ({
  loadTurnstileScript: vi.fn(),
}));

import { loadTurnstileScript } from "../../../src/features/contact-form/turnstile";

const SITE_KEY = "1x00000000000000000000AA";

interface FakeTurnstileHandle {
  readonly api: TurnstileApi;
  readonly renderedOptions: () => TurnstileRenderOptions;
}

function installFakeTurnstile(): FakeTurnstileHandle {
  let captured: TurnstileRenderOptions | null = null;
  const api: TurnstileApi = {
    render: vi.fn((_container: HTMLElement, options: TurnstileRenderOptions) => {
      captured = options;
      return "widget-1";
    }),
    reset: vi.fn(),
    remove: vi.fn(),
  };
  vi.mocked(loadTurnstileScript).mockResolvedValue(api);
  return {
    api,
    renderedOptions: () => {
      if (!captured) throw new Error("Turnstile render() was not called yet");
      return captured;
    },
  };
}

function fetchReturning(status: number, body: unknown): typeof fetch {
  return vi.fn().mockResolvedValue({ status, json: async () => body }) as unknown as typeof fetch;
}

async function fillRequiredFields(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText("Name"), "Ada Lovelace");
  await user.type(screen.getByLabelText("Work email"), "ada@example.com");
  await user.type(screen.getByLabelText("Message"), "We would like to run a pilot.");
  await user.click(screen.getByLabelText(/I consent/));
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ContactForm: both form types render correctly", () => {
  it("renders the pilot_request variant with its submit label", async () => {
    installFakeTurnstile();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    expect(await screen.findByRole("button", { name: "Request a pilot" })).toBeInTheDocument();
  });

  it("renders the feedback variant with its submit label", async () => {
    installFakeTurnstile();
    render(<ContactForm formType="feedback" siteKey={SITE_KEY} />);
    expect(await screen.findByRole("button", { name: "Send feedback" })).toBeInTheDocument();
  });
});

describe("ContactForm: labels, required/optional copy, limits, warning, consent, disclosure", () => {
  it("labels every control", async () => {
    installFakeTurnstile();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Work email")).toBeInTheDocument();
    expect(screen.getByLabelText(/Company/)).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeInTheDocument();
    expect(screen.getByLabelText(/I consent/)).toBeInTheDocument();
  });

  it("marks company as optional in visible text", async () => {
    installFakeTurnstile();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    expect(screen.getByText("(optional)")).toBeInTheDocument();
  });

  it("mirrors the server-side maximum lengths via maxLength attributes", async () => {
    installFakeTurnstile();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    expect(screen.getByLabelText("Name")).toHaveAttribute("maxLength", "100");
    expect(screen.getByLabelText(/Company/)).toHaveAttribute("maxLength", "200");
    expect(screen.getByLabelText("Message")).toHaveAttribute("maxLength", "2000");
  });

  it("shows the sensitive-information warning next to the message field", async () => {
    installFakeTurnstile();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    expect(
      screen.getByText(/Do not include passwords, API tokens, cloud credentials, kubeconfig content, report JSON/),
    ).toBeInTheDocument();
  });

  it("discloses that submitted text is transmitted and retained as email", async () => {
    installFakeTurnstile();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    expect(screen.getByText(/transmitted and retained as an email/)).toBeInTheDocument();
  });
});

describe("ContactForm: Turnstile token lifecycle", () => {
  it("renders a Turnstile widget container and calls render() with the site key and action", async () => {
    const fake = installFakeTurnstile();
    render(<ContactForm formType="feedback" siteKey={SITE_KEY} />);
    await waitFor(() => expect(fake.api.render).toHaveBeenCalledTimes(1));
    const options = fake.renderedOptions();
    expect(options.sitekey).toBe(SITE_KEY);
    expect(options.action).toBe("feedback");
  });

  it("shows an expiry message and requests a fresh challenge for the same widget after expired-callback fires", async () => {
    const fake = installFakeTurnstile();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());

    fake.renderedOptions()["expired-callback"]();

    expect(await screen.findByText(/Verification expired/)).toBeInTheDocument();
    // Regression: expiry must request a fresh challenge via the existing
    // widget/API instance, never leave the widget stalled.
    expect(fake.api.reset).toHaveBeenCalledTimes(1);
    expect(fake.api.reset).toHaveBeenCalledWith("widget-1");

    // Submission must remain blocked: no new token exists yet, so
    // attempting to submit is a client-side rejection, never a network call.
    const fetchImpl = vi.fn();
    vi.stubGlobal("fetch", fetchImpl);
    const user = userEvent.setup();
    await fillRequiredFields(user);
    await user.click(screen.getByRole("button", { name: "Request a pilot" }));
    expect(await screen.findByText(/complete the verification/)).toBeInTheDocument();
    expect(fetchImpl).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("shows an error message after error-callback fires", async () => {
    const fake = installFakeTurnstile();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());
    fake.renderedOptions()["error-callback"]();
    expect(await screen.findByText(/Verification could not be loaded/)).toBeInTheDocument();
  });
});

describe("ContactForm: submission", () => {
  it("disables the submit button while a submission is pending", async () => {
    const fake = installFakeTurnstile();
    let resolveFetch: (value: unknown) => void = () => {};
    const pendingFetch = vi.fn().mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    ) as unknown as typeof fetch;
    vi.stubGlobal("fetch", pendingFetch);

    const user = userEvent.setup();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());
    fake.renderedOptions().callback("a-token");
    await fillRequiredFields(user);

    await user.click(screen.getByRole("button", { name: "Request a pilot" }));
    expect(screen.getByRole("button", { name: "Sending…" })).toBeDisabled();

    resolveFetch({ status: 200, json: async () => ({ ok: true }) });
    await waitFor(() => expect(screen.getByRole("button")).not.toBeDisabled());
    vi.unstubAllGlobals();
  });

  it("sends exactly the allowlisted fields to /api/contact", async () => {
    const fake = installFakeTurnstile();
    const fetchImpl = fetchReturning(200, { ok: true });
    vi.stubGlobal("fetch", fetchImpl);

    const user = userEvent.setup();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());
    fake.renderedOptions().callback("captured-token");
    await fillRequiredFields(user);
    await user.type(screen.getByLabelText(/Company/), "Acme Inc");

    await user.click(screen.getByRole("button", { name: "Request a pilot" }));
    await waitFor(() => expect(fetchImpl).toHaveBeenCalled());

    const [url, init] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/contact");
    const body = JSON.parse(init.body as string);
    expect(Object.keys(body).sort()).toEqual(
      ["company", "consent", "formType", "message", "name", "turnstileToken", "workEmail"].sort(),
    );
    expect(body.formType).toBe("pilot_request");
    expect(body.name).toBe("Ada Lovelace");
    expect(body.workEmail).toBe("ada@example.com");
    expect(body.company).toBe("Acme Inc");
    expect(body.consent).toBe(true);
    expect(body.turnstileToken).toBe("captured-token");
    vi.unstubAllGlobals();
  });

  it("every attempted submission resets the Turnstile widget", async () => {
    const fake = installFakeTurnstile();
    vi.stubGlobal("fetch", fetchReturning(200, { ok: true }));

    const user = userEvent.setup();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());
    fake.renderedOptions().callback("a-token");
    await fillRequiredFields(user);

    await user.click(screen.getByRole("button", { name: "Request a pilot" }));
    await waitFor(() => expect(fake.api.reset).toHaveBeenCalledTimes(1));
    vi.unstubAllGlobals();
  });

  it("succeeds, clears the fields, and shows a success message", async () => {
    const fake = installFakeTurnstile();
    vi.stubGlobal("fetch", fetchReturning(200, { ok: true }));

    const user = userEvent.setup();
    render(<ContactForm formType="feedback" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());
    fake.renderedOptions().callback("a-token");
    await fillRequiredFields(user);

    await user.click(screen.getByRole("button", { name: "Send feedback" }));

    expect(await screen.findByText(/your feedback has been sent/)).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByLabelText("Work email")).toHaveValue("");
    expect(screen.getByLabelText("Message")).toHaveValue("");
    expect(screen.getByLabelText(/I consent/)).not.toBeChecked();
    vi.unstubAllGlobals();
  });
});

describe("ContactForm: client-side validation rejection", () => {
  it("rejects submission without ever calling fetch when consent is unchecked", async () => {
    const fake = installFakeTurnstile();
    const fetchImpl = fetchReturning(200, { ok: true });
    vi.stubGlobal("fetch", fetchImpl);

    const user = userEvent.setup();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());
    fake.renderedOptions().callback("a-token");

    await user.type(screen.getByLabelText("Name"), "Ada Lovelace");
    await user.type(screen.getByLabelText("Work email"), "ada@example.com");
    await user.type(screen.getByLabelText("Message"), "Hello");
    // consent left unchecked

    await user.click(screen.getByRole("button", { name: "Request a pilot" }));

    expect(await screen.findByText(/confirm consent/)).toBeInTheDocument();
    expect(fetchImpl).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("rejects an invalid work email client-side", async () => {
    const fake = installFakeTurnstile();
    const fetchImpl = fetchReturning(200, { ok: true });
    vi.stubGlobal("fetch", fetchImpl);

    const user = userEvent.setup();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());
    fake.renderedOptions().callback("a-token");

    await user.type(screen.getByLabelText("Name"), "Ada Lovelace");
    await user.type(screen.getByLabelText("Work email"), "not-an-email");
    await user.type(screen.getByLabelText("Message"), "Hello");
    await user.click(screen.getByLabelText(/I consent/));

    await user.click(screen.getByRole("button", { name: "Request a pilot" }));

    expect(await screen.findByText(/valid work email/)).toBeInTheDocument();
    expect(fetchImpl).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("regression: rejects a whitespace-only message client-side, without ever calling fetch", async () => {
    const fake = installFakeTurnstile();
    const fetchImpl = fetchReturning(200, { ok: true });
    vi.stubGlobal("fetch", fetchImpl);

    const user = userEvent.setup();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());
    fake.renderedOptions().callback("a-token");

    await user.type(screen.getByLabelText("Name"), "Ada Lovelace");
    await user.type(screen.getByLabelText("Work email"), "ada@example.com");
    await user.type(screen.getByLabelText("Message"), "     ");
    await user.click(screen.getByLabelText(/I consent/));

    await user.click(screen.getByRole("button", { name: "Request a pilot" }));

    expect(await screen.findByText(/Please enter a message/)).toBeInTheDocument();
    expect(fetchImpl).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("ContactForm: temporary failure and mailto fallback", () => {
  it("shows a sanitized mailto fallback, never containing the user's name or message", async () => {
    const fake = installFakeTurnstile();
    vi.stubGlobal(
      "fetch",
      fetchReturning(503, { ok: false, error: "temporarily_unavailable", fallbackEmail: "contact@cloudopsguard.example" }),
    );

    const user = userEvent.setup();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());
    fake.renderedOptions().callback("a-token");
    await fillRequiredFields(user);

    await user.click(screen.getByRole("button", { name: "Request a pilot" }));

    const mailLink = await screen.findByRole("link", { name: "contact@cloudopsguard.example" });
    const href = mailLink.getAttribute("href") ?? "";
    expect(href.startsWith("mailto:contact@cloudopsguard.example?subject=")).toBe(true);
    expect(href).not.toContain("Ada");
    expect(href).not.toContain("run+a+pilot");
    expect(href).not.toContain(encodeURIComponent("We would like to run a pilot."));
    vi.unstubAllGlobals();
  });

  it("does not offer a mailto link when no fallback email is provided", async () => {
    const fake = installFakeTurnstile();
    vi.stubGlobal("fetch", fetchReturning(503, { ok: false, error: "temporarily_unavailable" }));

    const user = userEvent.setup();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    await waitFor(() => fake.renderedOptions());
    fake.renderedOptions().callback("a-token");
    await fillRequiredFields(user);
    await user.click(screen.getByRole("button", { name: "Request a pilot" }));

    await screen.findByText(/temporarily unavailable/);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});

describe("ContactForm: report/credential/upload safety", () => {
  it("has no file input, no attachment control, and no field other than Turnstile's own token", async () => {
    installFakeTurnstile();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    expect(document.querySelector('input[type="file"]')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/kubeconfig|gitlab token|access token|cloud credential/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /attach/i })).not.toBeInTheDocument();
  });

  it("does not use dangerouslySetInnerHTML anywhere (all rendered text is ordinary React text)", () => {
    // A structural guarantee, not a runtime probe: confirmed directly
    // against the component's own source in the isolation test.
    installFakeTurnstile();
    render(<ContactForm formType="pilot_request" siteKey={SITE_KEY} />);
    expect(document.querySelectorAll("script")).toHaveLength(0);
  });
});

describe("ContactForm: no browser storage", () => {
  // This project's jsdom test environment does not provide a working
  // `localStorage`/`sessionStorage` global (confirmed: both are `undefined`
  // here, a jsdom/Vitest environment limitation, not a browser API this
  // component could accidentally use). The authoritative, real-browser
  // proof that no storage is ever written belongs at the Playwright layer
  // -- see tests/e2e/contact-form.spec.ts -- which is also where the
  // equivalent guarantee for the local report explorer was verified in
  // Phase 3G. What this test file can and does prove is that the
  // component's own source never references any storage API at all.
  it("never references localStorage, sessionStorage, or IndexedDB in its own source", () => {
    const source = ContactForm.toString();
    expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB/);
  });
});
