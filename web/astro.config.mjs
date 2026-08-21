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
export default defineConfig({
  output: "static",
  integrations: [react()],
});
