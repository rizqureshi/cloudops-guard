import react from "@astrojs/react";
import { defineConfig } from "astro/config";

// CloudOps Guard web foundation (v0.3.0 Phase 3B).
//
// Static output only -- no SSR adapter, no server islands, no Cloudflare
// configuration. The React integration is enabled so later phases can add
// interactive islands (the report workspace, comparison view, etc.), but
// Phase 3B itself ships a fully static, zero-hydration foundation page. No
// production `site` URL is set yet -- that is deferred until Phase 3K, when
// the real domain is approved.
//
// `security.csp: true` (Phase 3G) enables Astro's native, stable
// Content-Security-Policy support: Astro computes hash-based `script-src`/
// `style-src` directives for its own generated island-hydration bootstrap
// scripts/styles and emits a `<meta http-equiv="Content-Security-Policy">`
// tag on every prerendered page -- no `'unsafe-inline'`, no hand-written
// policy. This alone does not lock any route down; the three
// report-derived routes (`/demo/kubernetes`, `/demo/gitlab`, `/explorer`)
// each add further restrictive directives on top of this via Astro's
// per-page `Astro.csp.insertDirective(...)` API -- see
// `src/lib/reportRouteCsp.ts`.
export default defineConfig({
  output: "static",
  integrations: [react()],
  security: {
    csp: true,
  },
});
