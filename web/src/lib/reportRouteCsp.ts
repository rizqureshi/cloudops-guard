/**
 * Shared, restrictive Content-Security-Policy directives for every route
 * that renders report-derived data: `/demo/kubernetes`, `/demo/gitlab`,
 * and `/explorer` (Phase 3G). Kept in one place so the three routes can
 * never drift from each other.
 *
 * These are applied per-page via Astro's native CSP runtime API
 * (`Astro.csp.insertDirective(...)`, enabled globally by `security.csp` in
 * `astro.config.mjs`) -- never a hand-written `<meta>` tag, and never
 * `'unsafe-inline'`/`'unsafe-eval'`. Astro itself computes and adds the
 * `script-src`/`style-src` hash directives needed for its own generated
 * island-hydration bootstrap scripts/styles; the directives below add the
 * additional lockdown on top of that.
 *
 * `connect-src 'none'` is the one that matters most here: it is the
 * browser-enforced backstop proving these pages cannot make an outbound
 * request even if application code somehow tried to. `img-src 'self'` is
 * the only directive that is not `'none'` -- these pages load no images,
 * so it is unused in practice, but a same-origin allowance is harmless and
 * more conventional than `'none'` for image sources.
 *
 * Not included: `frame-ancestors`. Browsers do not enforce that directive
 * when delivered via a `<meta>` element (only via the `Content-Security-
 * Policy` HTTP response header), so adding it to this meta-delivered
 * policy would be a no-op that could misleadingly look like protection.
 * Header-level hardening (`frame-ancestors` included) belongs to a later
 * hosting/deployment phase.
 */
export const REPORT_ROUTE_CSP_DIRECTIVES = [
  "default-src 'none'",
  "connect-src 'none'",
  "img-src 'self'",
  "font-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
  "media-src 'none'",
  "manifest-src 'none'",
] as const;
