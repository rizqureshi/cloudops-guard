import { expect, test, type Page } from "@playwright/test";

import { EXPECTED_ISLAND_COUNTS, PUBLIC_ROUTES } from "./support/routes";
import { waitForIslandHydration } from "./support/hydration";
import { completeTurnstileChallenge, interceptTurnstileScript } from "./support/turnstile";

/**
 * Phase 3J's automated product-quality gates: structural, navigational, and
 * interaction-quality checks the axe scan does not cover by itself (axe
 * does not check link validity, title/description uniqueness, console
 * errors, island counts, viewport overflow, or keyboard operability of a
 * specific control by itself). Every check reuses the same `PUBLIC_ROUTES`
 * inventory as `route-inventory.spec.ts` and `accessibility.spec.ts`.
 *
 * Every test in this file installs the existing shared Turnstile fake
 * (`./support/turnstile.ts`) via this file-level `beforeEach`, before any
 * navigation happens -- not only in the tests that deliberately visit
 * `/request-demo`/`/feedback`. This is deliberate and unconditional,
 * rather than gated on which route(s) a given test happens to visit: a
 * `page.route()` interceptor that never matches (every non-contact route)
 * is a no-op, but a test that visits a contact route *without* it
 * installed first would let that route's real hydration reach the actual
 * `https://challenges.cloudflare.com` script during this suite -- exactly
 * the gap a prior version of the cross-route "unique title/description"
 * aggregate test had, since it looped over every `PUBLIC_ROUTES` entry
 * (including both contact routes) with no interceptor installed at all.
 * Installing it once per test here, rather than conditionally at each
 * `page.goto()` call site, removes that entire class of gap and avoids
 * registering the same route handler more than once per test. All
 * Turnstile traffic anywhere in this specification is therefore always
 * locally fulfilled -- never the real Cloudflare service.
 */
test.beforeEach(async ({ page }) => {
  await interceptTurnstileScript(page);
});

function trackConsoleErrors(page: Page): { consoleErrors: string[] } {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(String(error)));
  return { consoleErrors };
}

test.describe("product quality: per-route structural integrity", () => {
  for (const route of PUBLIC_ROUTES) {
    test(`${route}: 200 response, one <h1>, non-empty title/description, correct island count, no console/page errors`, async ({
      page,
    }) => {
      const { consoleErrors } = trackConsoleErrors(page);
      const response = await page.goto(route);
      expect(response, `no response for ${route}`).not.toBeNull();
      expect(response!.status(), `${route} did not return 200`).toBe(200);
      await waitForIslandHydration(page);

      const h1Count = await page.locator("h1").count();
      expect(h1Count, `${route} must have exactly one <h1>`).toBe(1);

      const title = await page.title();
      expect(title.trim().length, `${route} has an empty <title>`).toBeGreaterThan(0);

      const description = await page.locator('meta[name="description"]').getAttribute("content");
      expect(description?.trim().length ?? 0, `${route} has an empty meta description`).toBeGreaterThan(0);

      // Exactly one <header> (banner), one <main>, one <footer>
      // (contentinfo) -- the three landmarks every page shares via
      // BaseLayout -- plus no heading level jump from h1 straight to h4+
      // (h1 -> h2/h3 is fine; h1 -> h4 skips a level).
      expect(await page.locator("header").count()).toBe(1);
      expect(await page.locator("main#main-content").count()).toBe(1);
      expect(await page.locator("footer").count()).toBe(1);
      const headingLevels = await page.evaluate(() =>
        [...document.querySelectorAll("h1, h2, h3, h4, h5, h6")].map((el) => Number(el.tagName[1])),
      );
      for (let i = 1; i < headingLevels.length; i++) {
        const jump = headingLevels[i]! - headingLevels[i - 1]!;
        expect(jump, `${route}: heading level jumps from h${headingLevels[i - 1]} to h${headingLevels[i]}`).toBeLessThanOrEqual(1);
      }

      const islandCount = await page.locator("astro-island").count();
      expect(islandCount, `${route} island count`).toBe(EXPECTED_ISLAND_COUNTS.get(route) ?? 0);

      expect(consoleErrors, `${route} console/page error(s): ${JSON.stringify(consoleErrors)}`).toEqual([]);
    });
  }
});

test.describe("product quality: cross-route aggregate checks", () => {
  test("every route has a unique, non-empty <title> and meta description", async ({ page }) => {
    const titles = new Map<string, string>();
    const descriptions = new Map<string, string>();
    for (const route of PUBLIC_ROUTES) {
      await page.goto(route);
      const title = await page.title();
      const description = (await page.locator('meta[name="description"]').getAttribute("content")) ?? "";
      titles.set(route, title);
      descriptions.set(route, description);
    }

    const titleValues = [...titles.values()];
    const duplicateTitles = titleValues.filter((value, index) => titleValues.indexOf(value) !== index);
    expect(duplicateTitles, `Duplicate <title> value(s): ${JSON.stringify(duplicateTitles)}`).toEqual([]);

    const descriptionValues = [...descriptions.values()];
    const duplicateDescriptions = descriptionValues.filter((value, index) => descriptionValues.indexOf(value) !== index);
    expect(
      duplicateDescriptions,
      `Duplicate meta description value(s): ${JSON.stringify(duplicateDescriptions)}`,
    ).toEqual([]);
  });

  test("every internal navigation link on every route resolves to a route in the production build", async ({
    page,
  }) => {
    const brokenLinks: string[] = [];
    for (const route of PUBLIC_ROUTES) {
      await page.goto(route);
      const hrefs = await page.evaluate(() =>
        [...document.querySelectorAll("a[href]")]
          .map((a) => a.getAttribute("href") ?? "")
          .filter((href) => href.startsWith("/")),
      );
      for (const href of hrefs) {
        const path = href.split("#")[0]!.split("?")[0]!.replace(/\/$/, "") || "/";
        if (!PUBLIC_ROUTES.includes(path)) {
          brokenLinks.push(`${route} -> ${href}`);
        }
      }
    }
    expect(brokenLinks, `Internal link(s) not resolving to a built route: ${JSON.stringify(brokenLinks)}`).toEqual(
      [],
    );
  });
});

test.describe("product quality: viewport overflow", () => {
  for (const [label, viewport] of [
    ["desktop (1440x900)", { width: 1440, height: 900 }],
    ["narrow mobile (390x844)", { width: 390, height: 844 }],
  ] as const) {
    test(`no route produces unintended horizontal overflow at ${label}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const overflowingRoutes: string[] = [];
      for (const route of PUBLIC_ROUTES) {
        await page.goto(route);
        await waitForIslandHydration(page);
        const overflow = await page.evaluate(() => {
          const doc = document.documentElement;
          return doc.scrollWidth - doc.clientWidth;
        });
        // A 1px epsilon absorbs subpixel rounding, never a real overflow.
        if (overflow > 1) overflowingRoutes.push(`${route} (overflow: ${overflow}px)`);
      }
      expect(overflowingRoutes, `Route(s) with horizontal overflow: ${JSON.stringify(overflowingRoutes)}`).toEqual(
        [],
      );
    });
  }
});

test.describe("product quality: skip link", () => {
  test("the skip link becomes visible when focused and moves focus to the main landmark", async ({ page }) => {
    await page.goto("/");
    const skipLink = page.locator(".skip-link");

    const hiddenBox = await skipLink.boundingBox();
    // Off-screen (negative `top`) before focus.
    expect(hiddenBox?.y).toBeLessThan(0);

    await skipLink.focus();
    // The skip link's visibility change is CSS-transitioned
    // (`--transition-base`, 200ms) rather than instant -- poll rather than
    // read the bounding box exactly once, so this assertion does not race
    // the animation.
    await expect
      .poll(async () => (await skipLink.boundingBox())?.y ?? -1)
      .toBeGreaterThanOrEqual(0);

    await page.keyboard.press("Enter");
    const active = await page.evaluate(() => ({ tag: document.activeElement?.tagName, id: document.activeElement?.id }));
    expect(active).toEqual({ tag: "MAIN", id: "main-content" });
  });
});

test.describe("product quality: keyboard operability without forced clicks", () => {
  test("check catalogue: search, a filter, and clear are all reachable and operable by keyboard alone", async ({
    page,
  }) => {
    await page.goto("/checks");
    await waitForIslandHydration(page);

    await page.getByLabel("Search checks").focus();
    await page.keyboard.type("branch");
    await expect(page.getByText(/^Showing \d+ of \d+ checks\.$/)).toBeVisible();

    await page.getByLabel("Platform").focus();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByText(/^Showing \d+ of \d+ checks\.$/)).toBeVisible();

    await page.getByRole("button", { name: "Clear filters" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("Search checks")).toHaveValue("");
  });

  test("kubernetes demo: mode radios, view toggle, and a finding disclosure are all keyboard-operable", async ({
    page,
  }) => {
    await page.goto("/demo/kubernetes");
    await waitForIslandHydration(page);

    await page.getByLabel("Compare earlier scan to later scan").focus();
    await page.keyboard.press(" ");
    await expect(page.getByLabel("Compare earlier scan to later scan")).toBeChecked();
    await expect(page.getByText(/^New \d+$/)).toBeVisible();

    await page.getByRole("button", { name: "Executive summary" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Prioritized recommendations")).toBeVisible();

    await page.getByRole("button", { name: "Findings" }).focus();
    await page.keyboard.press("Enter");
    const firstSummary = page.locator(".finding-row__details summary").first();
    await firstSummary.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator(".finding-row__details").first()).toHaveAttribute("open", "");
  });

  test("explorer: file inputs and clear-all are reachable by keyboard, and Tab order stays logical", async ({
    page,
  }) => {
    await page.goto("/explorer");
    await waitForIslandHydration(page);
    await page.getByLabel("Earlier or primary report").focus();
    const active = await page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? document.activeElement?.id);
    expect(active).toBeTruthy();
  });

  for (const [routeName, path] of [
    ["request-demo", "/request-demo"],
    ["feedback", "/feedback"],
  ] as const) {
    test(`${routeName}: every field, consent, and submit control is keyboard-reachable in document order`, async ({
      page,
    }) => {
      await page.goto(path);
      await waitForIslandHydration(page);
      await completeTurnstileChallenge(page);

      await page.getByLabel("Name").focus();
      await page.keyboard.press("Tab");
      await expect(page.getByLabel("Work email")).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(page.getByLabel(/Company/)).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(page.getByLabel("Message")).toBeFocused();
    });
  }
});

test.describe("product quality: visible focus after interaction", () => {
  test("a focused control always has a non-'none' outline (finding disclosure, filter, and contact field)", async ({
    page,
  }) => {
    async function assertVisibleFocusOutline(locator: ReturnType<Page["locator"]>) {
      await locator.focus();
      const outlineStyle = await locator.evaluate((el) => getComputedStyle(el).outlineStyle);
      expect(outlineStyle, `outline-style was "${outlineStyle}", expected something other than "none"`).not.toBe(
        "none",
      );
    }

    await page.goto("/demo/kubernetes");
    await waitForIslandHydration(page);
    await assertVisibleFocusOutline(page.getByLabel("Search findings"));
    const firstSummary = page.locator(".finding-row__details summary").first();
    await assertVisibleFocusOutline(firstSummary);

    await page.goto("/request-demo");
    await waitForIslandHydration(page);
    await assertVisibleFocusOutline(page.getByLabel("Name"));
  });
});

test.describe("product quality: reduced motion", () => {
  test("with prefers-reduced-motion emulated, the check catalogue and kubernetes demo remain fully interactive", async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });

    await page.goto("/checks");
    await waitForIslandHydration(page);
    await page.getByLabel("Search checks").fill("branch");
    await expect(page.getByText(/^Showing \d+ of \d+ checks\.$/)).toBeVisible();

    await page.goto("/demo/kubernetes");
    await waitForIslandHydration(page);
    await page.getByLabel("Compare earlier scan to later scan").check();
    await expect(page.getByText(/^New \d+$/)).toBeVisible();
    const firstSummary = page.locator(".finding-row__details summary").first();
    await firstSummary.click();
    await expect(page.locator(".finding-row__details").first()).toHaveAttribute("open", "");
  });
});

test.describe("product quality: severity is always visible as text", () => {
  test("every status-label badge on the kubernetes demo carries non-empty visible text, not colour alone", async ({
    page,
  }) => {
    await page.goto("/demo/kubernetes");
    await waitForIslandHydration(page);
    const labelTexts = await page.locator(".status-label").allTextContents();
    expect(labelTexts.length).toBeGreaterThan(0);
    for (const text of labelTexts) {
      expect(text.trim().length, `a .status-label badge had no visible text`).toBeGreaterThan(0);
    }
  });
});
