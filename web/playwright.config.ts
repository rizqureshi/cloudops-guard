import { defineConfig, devices } from "@playwright/test";

/**
 * Phase 3J expands Phase 3G's Chromium-only configuration to three desktop
 * projects -- Chromium, Firefox, and WebKit -- and adds `@axe-core/
 * playwright`-driven accessibility coverage (see `tests/e2e/
 * accessibility.spec.ts`). Every existing functional spec now runs against
 * all three engines by default via `npm run test:e2e`; a single project can
 * still be targeted with `--project=<name>` for isolating a browser-specific
 * failure.
 *
 * Runs against the **production build**, served by `astro preview` (a
 * real static file server), never `astro dev` -- Astro's native CSP
 * support (see `astro.config.mjs`/`src/lib/reportRouteCsp.ts`) is only
 * fully representative of production output, not the dev server. A fixed
 * `127.0.0.1` host and port keep the target deterministic; Playwright
 * itself starts and stops exactly one `webServer` process per test run
 * (`reuseExistingServer` is `false` in CI, so a stray process is never
 * silently reused there), so no untracked preview process is left behind.
 *
 * `npm run build` must be run before `npm run test:e2e` -- this config
 * does not build the site itself.
 */
const PORT = 4173;
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // Omitted (rather than set to `undefined`) outside CI, so Playwright's
  // own default (based on available CPU cores) applies -- the project's
  // `exactOptionalPropertyTypes` TypeScript setting rejects an explicit
  // `undefined` value for an optional property.
  ...(process.env.CI ? { workers: 1 } : {}),
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"] },
    },
    {
      name: "webkit",
      use: { ...devices["Desktop Safari"] },
    },
  ],
  webServer: {
    command: `npm run preview -- --port ${PORT} --host 127.0.0.1`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    // `astro preview` auto-detects an agentic coding-tool environment (see
    // Astro's `isRunByAgent`/`am-i-vibing`) and silently daemonizes itself
    // in the background when one is detected -- the parent process then
    // exits immediately after confirming the daemon started, which
    // Playwright reports as "Process from config.webServer exited early"
    // even though the server is genuinely up. Setting `ASTRO_PREVIEW_
    // BACKGROUND` (Astro's own env var for "this is already the intended
    // long-lived server process, do not re-detect/re-background") forces
    // normal foreground/blocking behavior instead, which is what
    // Playwright's own process-lifecycle management expects.
    env: { ASTRO_PREVIEW_BACKGROUND: "1" },
  },
});
