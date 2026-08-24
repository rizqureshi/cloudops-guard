import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * End-to-end coverage for `/request-demo` and `/feedback` (Phase 3I),
 * against the real production build. Neither the real Cloudflare Turnstile
 * service nor a real `/api/contact` Worker exists in this environment (the
 * Worker is not deployed until Phase 3K) -- both are intercepted with
 * `page.route()`, which fulfills the request locally before it ever
 * reaches the network, so no real Turnstile verification or email is ever
 * sent from this test.
 */

const TURNSTILE_SCRIPT_URL_PATTERN = "https://challenges.cloudflare.com/turnstile/v0/api.js**";
const CONTACT_API_PATTERN = "**/api/contact";

async function waitForIslandHydration(page: Page): Promise<void> {
  await page.waitForFunction(() => {
    const island = document.querySelector("astro-island");
    return island !== null && !island.hasAttribute("ssr");
  });
}

/**
 * Fulfills the Turnstile script request with a small fake implementation
 * that exposes `window.turnstile` and calls the page's own `onload` query
 * parameter callback, exactly like the real script does -- but never
 * contacts `challenges.cloudflare.com`. `render()` stores the options it
 * receives on `window.__e2eTurnstile` so the test can trigger `callback`/
 * `expired-callback`/`error-callback` itself, and counts `reset()` calls.
 */
async function interceptTurnstileScript(page: Page): Promise<void> {
  await page.route(TURNSTILE_SCRIPT_URL_PATTERN, async (route: Route) => {
    const url = new URL(route.request().url());
    const onloadName = url.searchParams.get("onload") ?? "";
    const body = `
      window.__e2eTurnstile = { resetCount: 0, removeCount: 0, lastOptions: null, lastWidgetId: null };
      window.turnstile = {
        render: function (container, options) {
          var id = "e2e-widget-" + Math.random().toString(36).slice(2);
          window.__e2eTurnstile.lastOptions = options;
          window.__e2eTurnstile.lastWidgetId = id;
          return id;
        },
        reset: function () { window.__e2eTurnstile.resetCount += 1; },
        remove: function () { window.__e2eTurnstile.removeCount += 1; },
      };
      if (${JSON.stringify(onloadName)} && typeof window[${JSON.stringify(onloadName)}] === "function") {
        window[${JSON.stringify(onloadName)}]();
      }
    `;
    await route.fulfill({ status: 200, contentType: "application/javascript", body });
  });
}

async function waitForTurnstileWidget(page: Page): Promise<void> {
  await page.waitForFunction(() => {
    const state = (window as unknown as { __e2eTurnstile?: { lastWidgetId: string | null } }).__e2eTurnstile;
    return !!state?.lastWidgetId;
  });
}

async function completeTurnstileChallenge(page: Page, token = "e2e-fake-token"): Promise<void> {
  await waitForTurnstileWidget(page);
  await page.evaluate((tokenValue) => {
    const state = (window as unknown as { __e2eTurnstile: { lastOptions: { callback: (t: string) => void } } })
      .__e2eTurnstile;
    state.lastOptions.callback(tokenValue);
  }, token);
}

async function expireTurnstileChallenge(page: Page): Promise<void> {
  await waitForTurnstileWidget(page);
  await page.evaluate(() => {
    const state = (
      window as unknown as { __e2eTurnstile: { lastOptions: { "expired-callback": () => void } } }
    ).__e2eTurnstile;
    state.lastOptions["expired-callback"]();
  });
}

interface CapturedContactRequest {
  readonly body: Record<string, unknown>;
}

/** Intercepts POST /api/contact and fulfills it with a fixed response, recording every request body seen. */
async function interceptContactApi(
  page: Page,
  respond: (body: Record<string, unknown>) => { status: number; json: Record<string, unknown> },
  captured: CapturedContactRequest[],
): Promise<void> {
  await page.route(CONTACT_API_PATTERN, async (route: Route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    captured.push({ body });
    const { status, json } = respond(body);
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(json) });
  });
}

for (const [routeName, path, submitLabel, successPhrase] of [
  ["request-demo", "/request-demo", "Request a pilot", "pilot request has been sent"],
  ["feedback", "/feedback", "Send feedback", "feedback has been sent"],
] as const) {
  test.describe(`${routeName}: privacy-preserving interaction`, () => {
    test(`${routeName} - pointer interaction: successful submission sends only permitted fields, no report content`, async ({
      page,
    }) => {
      await interceptTurnstileScript(page);
      const captured: CapturedContactRequest[] = [];
      await interceptContactApi(page, () => ({ status: 200, json: { ok: true } }), captured);

      const consoleErrors: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error") consoleErrors.push(message.text());
      });

      await page.goto(path);
      await waitForIslandHydration(page);
      await completeTurnstileChallenge(page);

      await page.getByLabel("Name").fill("Ada Lovelace");
      await page.getByLabel("Work email").fill("ada@example.com");
      await page.getByLabel(/Company/).fill("Analytical Engines Ltd");
      await page.getByLabel("Message").fill("We would like to explore CloudOps Guard.");
      await page.getByLabel(/I consent/).check();

      await page.getByRole("button", { name: submitLabel }).click();

      await expect(page.getByText(new RegExp(successPhrase))).toBeVisible();

      expect(captured).toHaveLength(1);
      const sentBody = captured[0]!.body;
      expect(Object.keys(sentBody).sort()).toEqual(
        ["company", "consent", "formType", "message", "name", "turnstileToken", "workEmail"].sort(),
      );
      expect(sentBody.name).toBe("Ada Lovelace");
      expect(sentBody.workEmail).toBe("ada@example.com");
      expect(sentBody.consent).toBe(true);
      // No report-derived key or content of any kind (checkId, severity,
      // evidence, findings, report, etc.) appears anywhere in the request.
      expect(JSON.stringify(sentBody)).not.toMatch(/checkId|severity|finding|report\.json|kubeconfig/i);

      expect(consoleErrors).toEqual([]);
    });

    test(`${routeName} - keyboard interaction: full form can be completed and submitted without a pointer`, async ({
      page,
    }) => {
      await interceptTurnstileScript(page);
      const captured: CapturedContactRequest[] = [];
      await interceptContactApi(page, () => ({ status: 200, json: { ok: true } }), captured);

      await page.goto(path);
      await waitForIslandHydration(page);
      await completeTurnstileChallenge(page);

      await page.getByLabel("Name").focus();
      await page.keyboard.type("Grace Hopper");
      await page.keyboard.press("Tab");
      await page.keyboard.type("grace@example.com");
      await page.keyboard.press("Tab");
      await page.keyboard.type("Analytical Engines Ltd");
      await page.keyboard.press("Tab");
      await page.keyboard.type("Keyboard-only submission test.");
      // Tab past the warning note (not focusable) directly to the consent checkbox.
      await page.getByLabel(/I consent/).focus();
      await page.keyboard.press(" ");
      await expect(page.getByLabel(/I consent/)).toBeChecked();

      await page.getByRole("button", { name: submitLabel }).focus();
      await page.keyboard.press("Enter");

      await expect(page.getByText(new RegExp(successPhrase))).toBeVisible();
      expect(captured).toHaveLength(1);
      expect(captured[0]!.body.name).toBe("Grace Hopper");
    });

    test(`${routeName} - client-side validation failure never reaches /api/contact`, async ({ page }) => {
      await interceptTurnstileScript(page);
      const captured: CapturedContactRequest[] = [];
      await interceptContactApi(page, () => ({ status: 200, json: { ok: true } }), captured);

      await page.goto(path);
      await waitForIslandHydration(page);
      await completeTurnstileChallenge(page);

      await page.getByLabel("Name").fill("Ada Lovelace");
      await page.getByLabel("Work email").fill("not-an-email");
      await page.getByLabel("Message").fill("Hello");
      await page.getByLabel(/I consent/).check();

      await page.getByRole("button", { name: submitLabel }).click();

      await expect(page.getByText(/valid work email/)).toBeVisible();
      expect(captured).toHaveLength(0);
    });

    test(`${routeName} - Turnstile expiry requests a fresh challenge and blocks submission until a new token arrives`, async ({
      page,
    }) => {
      await interceptTurnstileScript(page);
      const captured: CapturedContactRequest[] = [];
      await interceptContactApi(page, () => ({ status: 200, json: { ok: true } }), captured);

      await page.goto(path);
      await waitForIslandHydration(page);
      await completeTurnstileChallenge(page);
      await expireTurnstileChallenge(page);

      await expect(page.getByText(/Verification expired/)).toBeVisible();

      // The fake widget's own reset count increased -- a fresh challenge
      // was genuinely requested via the existing widget, not left stalled.
      const resetCountAfterExpiry = await page.evaluate(
        () => (window as unknown as { __e2eTurnstile: { resetCount: number } }).__e2eTurnstile.resetCount,
      );
      expect(resetCountAfterExpiry).toBeGreaterThanOrEqual(1);

      // No new token exists yet -- attempting to submit must be blocked
      // client-side, never reach /api/contact.
      await page.getByLabel("Name").fill("Ada Lovelace");
      await page.getByLabel("Work email").fill("ada@example.com");
      await page.getByLabel("Message").fill("Hello.");
      await page.getByLabel(/I consent/).check();
      await page.getByRole("button", { name: submitLabel }).click();
      await expect(page.getByText(/complete the verification/)).toBeVisible();
      expect(captured).toHaveLength(0);

      // Once Turnstile's own callback supplies a genuinely new token,
      // submission succeeds normally.
      await completeTurnstileChallenge(page, "e2e-fresh-token-after-expiry");
      await page.getByRole("button", { name: submitLabel }).click();
      await expect(page.getByText(new RegExp(successPhrase))).toBeVisible();
      expect(captured).toHaveLength(1);
      expect(captured[0]!.body.turnstileToken).toBe("e2e-fresh-token-after-expiry");
    });

    test(`${routeName} - temporary failure shows a sanitized mailto fallback, never report content`, async ({
      page,
    }) => {
      await interceptTurnstileScript(page);
      const captured: CapturedContactRequest[] = [];
      await interceptContactApi(
        page,
        () => ({
          status: 503,
          json: { ok: false, error: "temporarily_unavailable", fallbackEmail: "contact@cloudopsguard.example" },
        }),
        captured,
      );

      await page.goto(path);
      await waitForIslandHydration(page);
      await completeTurnstileChallenge(page);

      await page.getByLabel("Name").fill("Ada Lovelace");
      await page.getByLabel("Work email").fill("ada@example.com");
      await page.getByLabel("Message").fill("Please contact me about a pilot.");
      await page.getByLabel(/I consent/).check();
      await page.getByRole("button", { name: submitLabel }).click();

      const mailLink = page.getByRole("link", { name: "contact@cloudopsguard.example" });
      await expect(mailLink).toBeVisible();
      const href = (await mailLink.getAttribute("href")) ?? "";
      expect(href.startsWith("mailto:contact@cloudopsguard.example?subject=")).toBe(true);
      expect(href).not.toContain("Ada");
      expect(href.toLowerCase()).not.toContain("pilot.");
    });

    test(`${routeName} - initiates only the required Turnstile traffic and same-origin /api/contact submission`, async ({
      page,
    }) => {
      await interceptTurnstileScript(page);
      const captured: CapturedContactRequest[] = [];
      await interceptContactApi(page, () => ({ status: 200, json: { ok: true } }), captured);

      const externalRequests: string[] = [];
      page.on("request", (request) => {
        const url = new URL(request.url());
        if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost" && url.hostname !== "challenges.cloudflare.com") {
          externalRequests.push(request.url());
        }
      });

      await page.goto(path);
      await waitForIslandHydration(page);
      await completeTurnstileChallenge(page);
      await page.getByLabel("Name").fill("Ada Lovelace");
      await page.getByLabel("Work email").fill("ada@example.com");
      await page.getByLabel("Message").fill("Hello.");
      await page.getByLabel(/I consent/).check();
      await page.getByRole("button", { name: submitLabel }).click();
      await expect(page.getByText(new RegExp(successPhrase))).toBeVisible();

      expect(externalRequests).toEqual([]);
    });
  });
}

test.describe("contact routes: CSP", () => {
  test("the report-derived routes remain unaffected: connect-src 'none' and no Turnstile references", async ({
    page,
  }) => {
    for (const path of ["/demo/kubernetes", "/demo/gitlab", "/explorer"]) {
      await page.goto(path);
      const cspMeta = await page.locator('meta[http-equiv="Content-Security-Policy" i]').getAttribute("content");
      expect(cspMeta).toContain("connect-src 'none'");
      expect(cspMeta).not.toContain("challenges.cloudflare.com");
      const html = await page.content();
      expect(html).not.toContain("challenges.cloudflare.com");
      expect(html).not.toContain("/api/contact");
    }
  });

  test("/request-demo and /feedback both hydrate exactly one island under their CSP", async ({ page }) => {
    await interceptTurnstileScript(page);
    for (const path of ["/request-demo", "/feedback"]) {
      await page.goto(path);
      await waitForIslandHydration(page);
      const islandCount = await page.locator("astro-island").count();
      expect(islandCount).toBe(1);
      const cspMeta = await page.locator('meta[http-equiv="Content-Security-Policy" i]').getAttribute("content");
      expect(cspMeta).toContain("challenges.cloudflare.com");
      expect(cspMeta).not.toContain("unsafe-inline");
      expect(cspMeta).not.toContain("unsafe-eval");
    }
  });
});
