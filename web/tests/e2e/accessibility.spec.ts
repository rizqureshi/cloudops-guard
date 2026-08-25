import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import type { AxeResults, NodeResult, Result } from "axe-core";
import { fileURLToPath } from "node:url";

import { CONTACT_ROUTES, PUBLIC_ROUTES } from "./support/routes";
import { waitForIslandHydration } from "./support/hydration";
import { completeTurnstileChallenge, interceptContactApi, interceptTurnstileScript } from "./support/turnstile";

const WEB_ROOT = fileURLToPath(new URL("../..", import.meta.url));

/**
 * Phase 3J automated accessibility coverage: `@axe-core/playwright` run
 * against the real production build (never `astro dev`), for every one of
 * the 29 routes in `./support/routes.ts` -- the same shared inventory the
 * route-coverage proof (`route-inventory.spec.ts`) checks against the
 * actual `dist/` output, so this file cannot silently drift from "every
 * built route" -- plus a set of representative interactive states where
 * substantial content changes after the initial load (comparison results,
 * the executive summary, filtered/empty catalogue states, explorer error
 * states, contact form validation/fallback states).
 *
 * No axe rule is disabled, no page region is excluded, and colour-contrast
 * checking is never turned off. No CSP accommodation was needed for axe's
 * own script injection to work -- confirmed empirically (axe injects and
 * runs cleanly under this site's real, unmodified CSP on every route type
 * tried, including a hydrated island route) -- so this file uses the
 * default Playwright context/CSP the same as every other spec; production
 * CSP is therefore never weakened anywhere in this suite. `contact-form.
 * spec.ts` and `local-report-explorer.spec.ts` (Phase 3I/3G, unchanged)
 * remain the source of truth proving the *served* CSP header/meta content
 * itself.
 *
 * `analyze()` is never used to *prove* WCAG 2.2 AA conformance by itself
 * -- automated tools such as axe-core catch roughly 20-50% of WCAG success
 * criteria failures by design (issues like meaningful reading order, the
 * accuracy of alt text, or genuine keyboard operability need a human or a
 * dedicated interaction test). This file is one input among several
 * (see the manual keyboard/screen-reader/reflow review and the
 * product-quality spec) recorded in
 * `docs/reviews/v0.3.0-phase-3j-release-readiness.md`.
 */

const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

interface SanitizedNode {
  readonly target: string[];
}

interface SanitizedResult {
  readonly id: string;
  readonly impact: string | null | undefined;
  readonly help: string;
  readonly helpUrl: string;
  readonly nodeCount: number;
  // Only CSS-selector targets are kept -- never `html`/`failureSummary`,
  // which can embed the actual rendered text of a report finding or a
  // contact-form field value.
  readonly nodes: SanitizedNode[];
}

function sanitize(results: readonly Result[]): SanitizedResult[] {
  return results.map((result) => ({
    id: result.id,
    impact: result.impact,
    help: result.help,
    helpUrl: result.helpUrl,
    nodeCount: result.nodes.length,
    nodes: result.nodes.map((node: NodeResult) => ({ target: node.target.map(String) })),
  }));
}

function formatFailureMessage(label: string, violations: readonly Result[]): string {
  const sanitized = sanitize(violations);
  return `${label}: ${sanitized.length} critical/serious axe violation(s):\n${JSON.stringify(sanitized, null, 2)}`;
}

/** Runs axe, asserts zero critical/serious violations, and logs any `incomplete` results for manual review rather than silently discarding them. */
async function assertNoSeriousViolations(page: Page, label: string): Promise<AxeResults> {
  const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  const seriousOrCritical = results.violations.filter((v) => v.impact === "critical" || v.impact === "serious");
  expect(seriousOrCritical, formatFailureMessage(label, seriousOrCritical)).toEqual([]);

  if (results.incomplete.length > 0) {
    // Deliberately not a test failure: `incomplete` means axe could not
    // determine pass/fail automatically (e.g. some colour-contrast checks
    // against a background image or gradient) -- it requires a human
    // judgment call, recorded here for the manual review rather than
    // silently ignored or treated as a false failure.
    console.log(
      `[axe incomplete] ${label}: ${JSON.stringify(sanitize(results.incomplete), null, 2)}`,
    );
  }
  return results;
}

test.describe("accessibility: axe scan across every production route", () => {
  for (const route of PUBLIC_ROUTES) {
    test(`axe: ${route} has no critical or serious violations`, async ({ page }) => {
      if (CONTACT_ROUTES.includes(route)) {
        await interceptTurnstileScript(page);
      }
      await page.goto(route);
      await waitForIslandHydration(page);
      await assertNoSeriousViolations(page, route);
    });
  }
});

test.describe("accessibility: representative interactive states", () => {
  test("check catalogue: a filtered (non-empty) result state", async ({ page }) => {
    await page.goto("/checks");
    await waitForIslandHydration(page);
    await page.getByLabel("Search checks").fill("branch");
    await expect(page.getByText(/^Showing \d+ of \d+ checks\.$/)).toBeVisible();
    await assertNoSeriousViolations(page, "/checks (filtered)");
  });

  test("check catalogue: the empty (no matches) state", async ({ page }) => {
    await page.goto("/checks");
    await waitForIslandHydration(page);
    await page.getByLabel("Search checks").fill("no-check-will-ever-match-this-string");
    await expect(page.getByText(/No checks match your current search and filters\./)).toBeVisible();
    await assertNoSeriousViolations(page, "/checks (empty)");
  });

  test("kubernetes demo: comparison result state", async ({ page }) => {
    await page.goto("/demo/kubernetes");
    await waitForIslandHydration(page);
    await page.getByLabel("Compare earlier scan to later scan").check();
    await expect(page.getByText(/^New \d+$/)).toBeVisible();
    await assertNoSeriousViolations(page, "/demo/kubernetes (comparison)");
  });

  test("gitlab demo: comparison result state", async ({ page }) => {
    await page.goto("/demo/gitlab");
    await waitForIslandHydration(page);
    await page.getByLabel(/^Compare earlier scan to later scan$/).check();
    await expect(page.getByText(/^New \d+$/)).toBeVisible();
    await assertNoSeriousViolations(page, "/demo/gitlab (comparison)");
  });

  test("kubernetes demo: executive summary view", async ({ page }) => {
    await page.goto("/demo/kubernetes");
    await waitForIslandHydration(page);
    await page.getByRole("button", { name: "Executive summary" }).click();
    await expect(page.getByText("Prioritized recommendations")).toBeVisible();
    await assertNoSeriousViolations(page, "/demo/kubernetes (executive summary)");
  });

  test("explorer: a loaded single report", async ({ page }) => {
    await page.goto("/explorer");
    await waitForIslandHydration(page);
    await page
      .getByLabel("Earlier or primary report")
      .setInputFiles(`${WEB_ROOT}/src/data/synthetic-kubernetes-report.json`);
    await expect(page.getByText("Report loaded.").first()).toBeVisible();
    await assertNoSeriousViolations(page, "/explorer (single report loaded)");
  });

  test("explorer: a loaded comparison pair", async ({ page }) => {
    await page.goto("/explorer");
    await waitForIslandHydration(page);
    await page
      .getByLabel("Earlier or primary report")
      .setInputFiles(`${WEB_ROOT}/src/data/synthetic-kubernetes-report.json`);
    await expect(page.getByText("Report loaded.").first()).toBeVisible();
    await page
      .getByLabel("Later report for comparison (optional)")
      .setInputFiles(`${WEB_ROOT}/src/data/synthetic-kubernetes-report-later.json`);
    await expect(page.getByText("Report loaded.").nth(1)).toBeVisible();
    await page.getByLabel("Compare earlier to later").check();
    await expect(page.getByText(/^New \d+$/)).toBeVisible();
    await assertNoSeriousViolations(page, "/explorer (comparison pair)");
  });

  test("explorer: the sanitized wrong-extension error state", async ({ page }) => {
    await page.goto("/explorer");
    await waitForIslandHydration(page);
    // A real, ordinary file with the wrong extension -- the same client-side
    // rejection path a user selecting the wrong file would hit. No content
    // of the file is ever read for a `.json`-extension check, so its body
    // is irrelevant.
    await page
      .getByLabel("Earlier or primary report")
      .setInputFiles({ name: "not-a-report.txt", mimeType: "text/plain", buffer: Buffer.from("not json") });
    await expect(page.getByText(/Only a CloudOps Guard report\.json file/)).toBeVisible();
    await assertNoSeriousViolations(page, "/explorer (sanitized error state)");
  });

  for (const [routeName, path] of [
    ["request-demo", "/request-demo"],
    ["feedback", "/feedback"],
  ] as const) {
    test(`${routeName}: client-side validation-error state`, async ({ page }) => {
      await interceptTurnstileScript(page);
      await page.goto(path);
      await waitForIslandHydration(page);
      await completeTurnstileChallenge(page);
      await page.getByLabel("Name").fill("Ada Lovelace");
      await page.getByLabel("Work email").fill("not-an-email");
      await page.getByLabel("Message").fill("Hello");
      await page.getByLabel(/I consent/).check();
      await page.getByRole("button", { name: /Request a pilot|Send feedback/ }).click();
      await expect(page.getByText(/valid work email/)).toBeVisible();
      await assertNoSeriousViolations(page, `${path} (validation error)`);
    });

    test(`${routeName}: temporary-unavailable mailto-fallback state`, async ({ page }) => {
      await interceptTurnstileScript(page);
      const captured: Array<{ body: Record<string, unknown> }> = [];
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
      await page.getByLabel("Message").fill("Please get in touch.");
      await page.getByLabel(/I consent/).check();
      await page.getByRole("button", { name: /Request a pilot|Send feedback/ }).click();
      await expect(page.getByRole("link", { name: "contact@cloudopsguard.example" })).toBeVisible();
      await assertNoSeriousViolations(page, `${path} (temporary fallback)`);
    });
  }
});
