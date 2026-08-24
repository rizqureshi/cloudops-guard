import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const WEB_ROOT = fileURLToPath(new URL("../..", import.meta.url));
const REPO_ROOT = fileURLToPath(new URL("../../..", import.meta.url));

const EARLIER_KUBERNETES_REPORT = `${WEB_ROOT}/src/data/synthetic-kubernetes-report.json`;
const LATER_KUBERNETES_REPORT = `${WEB_ROOT}/src/data/synthetic-kubernetes-report-later.json`;
const GOLDEN_KUBERNETES_REPORT = `${REPO_ROOT}/tests/fixtures/golden_kubernetes_report.json`;
const GOLDEN_GITLAB_REPORT = `${REPO_ROOT}/tests/fixtures/golden_gitlab_report.json`;

/**
 * Astro's `<astro-island>` carries an `ssr` attribute until hydration
 * finishes (see the astro-island runtime, which calls
 * `this.removeAttribute("ssr")` at the end of `hydrate()`). Waiting for
 * that attribute to disappear is the authoritative hydration signal --
 * the underlying DOM already exists in the initial static HTML (Astro
 * server-renders it), so waiting for elements to merely be "visible"
 * would not prove React event handlers are attached yet.
 */
async function waitForIslandHydration(page: Page): Promise<void> {
  await page.waitForFunction(() => {
    const island = document.querySelector("astro-island");
    return island !== null && !island.hasAttribute("ssr");
  });
}

test.describe("Local report explorer: privacy-preserving interaction", () => {
  test("full findings/comparison/executive-summary flow initiates no network request, storage, or console error", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") {
        consoleErrors.push(message.text());
      }
    });
    page.on("pageerror", (error) => {
      consoleErrors.push(String(error));
    });

    await page.goto("/explorer");
    await waitForIslandHydration(page);

    // CSP must not have blocked hydration: the explorer's real controls
    // are present and interactive.
    await expect(page.getByLabel("Earlier or primary report")).toBeVisible();
    await expect(page.getByText("No report imported yet.")).toBeVisible();

    // Everything from this point on is "report import or interaction" --
    // no request, successful or blocked, may occur from here on. Recording
    // starts only after the initial static page load/hydration so the
    // page's own legitimate same-origin asset loading is not counted
    // against this assertion.
    const requestUrls: string[] = [];
    const failedRequestUrls: string[] = [];
    page.on("request", (request) => requestUrls.push(request.url()));
    page.on("requestfailed", (request) => {
      // An attempted-but-CSP-blocked request still fires "requestfailed"
      // in Chromium -- that counts as a defect just as much as a request
      // that reached the network, so it is recorded the same way, never
      // filtered out because CSP happened to stop it.
      failedRequestUrls.push(`${request.url()} (${request.failure()?.errorText ?? "unknown"})`);
    });

    // 3. Select a representative valid report.
    await page.getByLabel("Earlier or primary report").setInputFiles(EARLIER_KUBERNETES_REPORT);
    await expect(page.getByText("Report loaded.").first()).toBeVisible();
    await expect(page.getByText("Local report").first()).toBeVisible();
    await expect(page.getByLabel("Search findings")).toBeVisible();

    // 4. Search.
    await page.getByLabel("Search findings").fill("checkout-api");
    await expect(page.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeVisible();
    await page.getByLabel("Search findings").fill("");

    // 5. Severity/category/resource-kind filters.
    await page.getByLabel("Severity", { exact: true }).selectOption("medium");
    await expect(page.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeVisible();
    await page.getByLabel("Severity", { exact: true }).selectOption("all");

    await page.getByLabel("Category", { exact: true }).selectOption({ index: 1 });
    await expect(page.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeVisible();
    await page.getByLabel("Category", { exact: true }).selectOption("all");

    await page.getByLabel("Resource kind", { exact: true }).selectOption({ index: 1 });
    await expect(page.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeVisible();
    await page.getByLabel("Resource kind", { exact: true }).selectOption("all");

    // 6. Change sorting.
    await page.getByLabel("Sort by").selectOption("checkId");
    await page.getByLabel("Sort by").selectOption("severity");

    // 7. Open a finding's native disclosure.
    const firstDetails = page.locator(".finding-row__details").first();
    await firstDetails.locator("summary").click();
    await expect(firstDetails).toHaveAttribute("open", "");

    // 8. Switch to the executive summary and back.
    await page.getByRole("button", { name: "Executive summary" }).click();
    await expect(page.getByText("Prioritized recommendations")).toBeVisible();
    await expect(page.getByText("Local report").first()).toBeVisible();
    await page.getByRole("button", { name: "Findings" }).click();
    await expect(page.getByLabel("Search findings")).toBeVisible();

    // 9. Select a compatible second report.
    await page.getByLabel("Later report for comparison (optional)").setInputFiles(LATER_KUBERNETES_REPORT);
    await expect(page.getByText("Report loaded.").nth(1)).toBeVisible();
    await expect(page.getByLabel("Compare earlier to later")).toBeEnabled();

    // 10. Enter comparison mode.
    await page.getByLabel("Compare earlier to later").check();
    await expect(page.getByText(/^New \d+$/)).toBeVisible();
    await expect(page.getByText(/^Persistent \d+$/)).toBeVisible();
    await expect(page.getByText(/^Resolved \d+$/)).toBeVisible();

    // 11. Comparison-status filtering/sorting.
    await page.getByLabel("Comparison status", { exact: true }).selectOption("new");
    await expect(page.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeVisible();
    await page.getByLabel("Comparison status", { exact: true }).selectOption("all");
    await page.getByLabel("Sort by").selectOption("comparisonStatus");

    // 12. Clear imported reports.
    await page.getByRole("button", { name: "Clear all" }).click();
    await expect(page.getByText("No report imported yet.")).toBeVisible();
    await expect(page.getByLabel("Earlier or primary report")).toHaveValue("");
    await expect(page.getByLabel("Later report for comparison (optional)")).toHaveValue("");

    expect(requestUrls, `Unexpected request(s): ${JSON.stringify(requestUrls)}`).toEqual([]);
    expect(
      failedRequestUrls,
      `Unexpected blocked/failed request(s): ${JSON.stringify(failedRequestUrls)}`,
    ).toEqual([]);
    expect(consoleErrors, `Unexpected console error(s): ${JSON.stringify(consoleErrors)}`).toEqual([]);
  });

  test("does not persist a report in localStorage, sessionStorage, IndexedDB, or a cookie, and registers no service worker", async ({
    page,
    context,
  }) => {
    await page.goto("/explorer");
    await waitForIslandHydration(page);

    await page.getByLabel("Earlier or primary report").setInputFiles(EARLIER_KUBERNETES_REPORT);
    await expect(page.getByText("Report loaded.").first()).toBeVisible();

    const localStorageEntries = await page.evaluate(() => ({ ...window.localStorage }));
    const sessionStorageEntries = await page.evaluate(() => ({ ...window.sessionStorage }));
    expect(localStorageEntries).toEqual({});
    expect(sessionStorageEntries).toEqual({});

    const databaseNames = await page.evaluate(async () => {
      if (!("databases" in indexedDB)) {
        return [];
      }
      const databases = await indexedDB.databases();
      return databases.map((database) => database.name);
    });
    expect(databaseNames).toEqual([]);

    const cookies = await context.cookies();
    expect(cookies).toEqual([]);

    const serviceWorkerRegistrations = await page.evaluate(async () => {
      const registrations = await navigator.serviceWorker.getRegistrations();
      return registrations.length;
    });
    expect(serviceWorkerRegistrations).toBe(0);
  });

  test("reloading the page returns the explorer to its empty state", async ({ page }) => {
    await page.goto("/explorer");
    await waitForIslandHydration(page);

    await page.getByLabel("Earlier or primary report").setInputFiles(EARLIER_KUBERNETES_REPORT);
    await expect(page.getByText("Report loaded.").first()).toBeVisible();

    await page.reload();
    await waitForIslandHydration(page);

    await expect(page.getByText("No report imported yet.")).toBeVisible();
    await expect(page.getByLabel("Earlier or primary report")).toHaveValue("");
  });

  test("the report-route CSP is active and does not prevent hydration or interaction", async ({ page }) => {
    const response = await page.goto("/explorer");
    expect(response).not.toBeNull();

    const cspMetaContent = await page.locator('meta[http-equiv="Content-Security-Policy" i]').getAttribute("content");
    expect(cspMetaContent).toBeTruthy();
    expect(cspMetaContent).toContain("connect-src 'none'");
    expect(cspMetaContent?.toLowerCase()).not.toContain("unsafe-inline");
    expect(cspMetaContent?.toLowerCase()).not.toContain("unsafe-eval");

    await waitForIslandHydration(page);
    // Interaction still works under the policy above.
    await page.getByLabel("Earlier or primary report").setInputFiles(EARLIER_KUBERNETES_REPORT);
    await expect(page.getByText("Report loaded.").first()).toBeVisible();
  });

  test("both authoritative golden fixtures can be selected and rendered individually", async ({ page }) => {
    await page.goto("/explorer");
    await waitForIslandHydration(page);

    await page.getByLabel("Earlier or primary report").setInputFiles(GOLDEN_KUBERNETES_REPORT);
    await expect(page.getByText("Report loaded.").first()).toBeVisible();
    await expect(page.locator(".report-workspace__identity")).toContainText("Kubernetes");
    await expect(page.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeVisible();

    await page.getByRole("button", { name: "Clear all" }).click();
    await expect(page.getByText("No report imported yet.")).toBeVisible();

    await page.getByLabel("Earlier or primary report").setInputFiles(GOLDEN_GITLAB_REPORT);
    await expect(page.getByText("Report loaded.").first()).toBeVisible();
    await expect(page.locator(".report-workspace__identity")).toContainText("GitLab");
    await expect(page.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeVisible();
  });
});
