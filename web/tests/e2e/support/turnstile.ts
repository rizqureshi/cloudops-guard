import type { Page, Route } from "@playwright/test";

/**
 * A local fake for the Turnstile script, shared by every Phase 3J test that
 * needs to reach a hydrated, "verified" state on `/request-demo` or
 * `/feedback` without ever contacting `challenges.cloudflare.com`. Mirrors
 * the fake already used by `tests/e2e/contact-form.spec.ts` (Phase 3I) --
 * duplicated here rather than imported from that file so the two specs'
 * ownership stays independent, matching this project's existing per-spec
 * helper pattern.
 */
export const TURNSTILE_SCRIPT_URL_PATTERN = "https://challenges.cloudflare.com/turnstile/v0/api.js**";

export async function interceptTurnstileScript(page: Page): Promise<void> {
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

export async function completeTurnstileChallenge(page: Page, token = "e2e-fake-token"): Promise<void> {
  await waitForTurnstileWidget(page);
  await page.evaluate((tokenValue) => {
    const state = (window as unknown as { __e2eTurnstile: { lastOptions: { callback: (t: string) => void } } })
      .__e2eTurnstile;
    state.lastOptions.callback(tokenValue);
  }, token);
}

interface CapturedContactRequest {
  readonly body: Record<string, unknown>;
}

/** Intercepts POST /api/contact and fulfills it with a fixed response, recording every request body seen. */
export async function interceptContactApi(
  page: Page,
  respond: (body: Record<string, unknown>) => { status: number; json: Record<string, unknown> },
  captured: CapturedContactRequest[],
): Promise<void> {
  await page.route("**/api/contact", async (route: Route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    captured.push({ body });
    const { status, json } = respond(body);
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(json) });
  });
}
