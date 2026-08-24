/**
 * Shared Content-Security-Policy directives for the two contact routes,
 * `/request-demo` and `/feedback` (Phase 3I) -- kept entirely separate
 * from `reportRouteCsp.ts` (never imported by it, never imported into it)
 * so tightening or loosening one can never silently affect the other.
 *
 * These routes need strictly more than the report routes' `connect-src
 * 'none'`/`frame-src 'none'` lockdown, because they embed Cloudflare's
 * official Turnstile widget: its script must load from
 * `challenges.cloudflare.com`, it renders inside an iframe served from
 * that same origin, and it performs its own background requests there.
 * `challenges.cloudflare.com` is the *only* non-`'self'` origin permitted
 * anywhere in this directive set, and Turnstile is the *only* third-party
 * script permitted anywhere on this site.
 *
 * `connect-src` also allows `'self'`, for this page's own same-origin
 * `fetch("/api/contact", ...)` call -- the one deliberate exception to the
 * report routes' `connect-src 'none'` rule, and scoped to these two pages
 * only. `form-action 'self'` is included even though the form submits via
 * JavaScript `fetch` rather than a native form POST, as defense in depth.
 *
 * Applied per-page via `Astro.csp.insertDirective(...)` for directives
 * with no dedicated resource-merging helper, and
 * `Astro.csp.insertScriptResource(...)` for `'self'` and the Turnstile
 * script origin. **Both calls are required**: Astro's own
 * `renderCspContent` (`astro/dist/runtime/server/render/csp.js`) only
 * falls back to `'self'` when *zero* custom script resources have been
 * inserted (`script.default.resources.length > 0 ? ... : "'self'"`) --
 * inserting the Turnstile origin alone silently drops `'self'` from the
 * final `script-src`, which breaks this page's own hydration script
 * (discovered by a genuine hydration failure against the real production
 * build, not by inspection alone: Chromium's own CSP-violation console
 * error named the exact missing `'self'` token). Astro's per-resource
 * hashes for its own generated bootstrap scripts are still added
 * unconditionally on top, regardless of how many custom resources exist.
 * Never `'unsafe-inline'`/`'unsafe-eval'`.
 */
export const CONTACT_ROUTE_CSP_DIRECTIVES = [
  "default-src 'none'",
  "connect-src 'self' https://challenges.cloudflare.com",
  "img-src 'self'",
  "font-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-src https://challenges.cloudflare.com",
  "worker-src 'none'",
  "media-src 'none'",
  "manifest-src 'none'",
] as const;

/**
 * The exact script resources these routes' own `<script>` tag needs, in
 * the order they must both be inserted via `Astro.csp.insertScriptResource`
 * -- `'self'` first (Astro drops its own default the moment any custom
 * script resource is inserted at all) and the Turnstile origin second.
 */
export const CONTACT_ROUTE_SCRIPT_RESOURCES = ["'self'", "https://challenges.cloudflare.com"] as const;
