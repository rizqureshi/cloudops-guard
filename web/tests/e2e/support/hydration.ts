import type { Page } from "@playwright/test";

/**
 * Astro's `<astro-island>` carries an `ssr` attribute until hydration
 * finishes (see the astro-island runtime, which calls
 * `this.removeAttribute("ssr")` at the end of `hydrate()`). Waiting for
 * that attribute to disappear is the authoritative hydration signal -- the
 * underlying DOM already exists in the initial static HTML (Astro
 * server-renders it), so waiting for elements to merely be "visible" would
 * not prove React event handlers are attached yet.
 *
 * Safe to call on a route with zero islands: it resolves immediately when
 * no `<astro-island>` element exists at all.
 */
export async function waitForIslandHydration(page: Page): Promise<void> {
  await page.waitForFunction(() => {
    const islands = document.querySelectorAll("astro-island");
    if (islands.length === 0) return true;
    return [...islands].every((island) => !island.hasAttribute("ssr"));
  });
}
