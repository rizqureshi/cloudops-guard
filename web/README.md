# CloudOps Guard web

This is the public website for CloudOps Guard: a v0.3.0, browser-only interactive
demo and local `report.json` explorer, alongside the existing Kubernetes and GitLab
CLI audits. See
[`../docs/milestones/v0.3.0-interactive-web-demo.md`](../docs/milestones/v0.3.0-interactive-web-demo.md)
for the full design and scope reference, and [`../CLAUDE.md`](../CLAUDE.md) for
durable, cross-project rules.

## Current phase: 3G &mdash; Local Report Explorer Privacy Boundary

The static web foundation (Phase 3B), the browser-side report-contract
layer (Phase 3C), the Kubernetes and GitLab interactive demonstrations at
`/demo/kubernetes` and `/demo/gitlab` (Phases 3D and 3E), and comparison
plus the executive summary (Phase 3F) are unchanged in their route/content
fundamentals; Phases 3B&ndash;3F are closed. **Phase 3G has implemented
`/explorer`, a local `report.json` explorer, but is not yet closed** (see
the milestone document for the exact closure gate). Phase 3F added
comparison and an executive summary to both demo routes:

- A new, browser-only comparison feature
  ([`src/features/comparison/`](src/features/comparison/)), kept separate
  from the normalized report representation and from the released Python
  report contracts. Findings are fingerprinted on identity fields only
  (checkId/clusterContext/namespace/resourceKind/resourceName/
  containerName for Kubernetes; checkId/projectPath/resourceKind/
  resourceName/jobName for GitLab -- never severity, wording, or
  timestamp), using a collision-safe `JSON.stringify`-on-a-tuple
  fingerprint rather than a delimiter-joined string. An older and a newer
  report are validated (same platform; newer's timestamp strictly later,
  compared as instants; compatible target) and then multiset-matched into
  new/persistent/resolved results -- an O(n) grouping, never O(n²), so it
  stays suitable as reports approach the existing 10,000-finding limit.
  `GL-CI-001` uses the image reference as `resourceName`, so a changed
  image reference for the same job appears as one resolved result plus one
  new result, not a persistent result whose evidence changed -- an
  approved, documented limitation, demonstrated directly in the synthetic
  GitLab dataset.
- A new, deterministic executive-summary feature
  ([`src/features/executive-summary/`](src/features/executive-summary/)):
  target identity, totals, affected categories (deterministically ordered
  by highest severity present, then descending count, then category name),
  and up to five prioritized, deduplicated, category-diverse
  recommendations -- computed as a pure function of report/comparison
  data, never an LLM call, and explicitly disclaiming any health/safety/
  compliance/completeness claim. In comparison mode, resolved findings are
  excluded from affected categories and recommendations (though their
  total is still shown), and severity totals always reflect the newer
  report only.
- A shared scan-state controller
  ([`src/features/demo-controller/DemoController.tsx`](src/features/demo-controller/DemoController.tsx)),
  replacing Phase 3E's GitLab-only scenario selector (the `gitlab-demo`
  feature folder was removed), now used on both demo routes: earlier
  scan / later scan / compare-earlier-to-later modes, plus a
  findings/executive-summary view toggle. Switching mode resets search,
  filters, sort order, the view, and any expanded finding details.
- `ReportWorkspace` gained a comparison mode (`mode: "comparison"`,
  alongside the existing `mode: "single"`): a comparison-status filter and
  sort option, a New/Persistent/Resolved totals bar, and a status badge on
  each finding row -- all absent in single mode.
- A second synthetic Kubernetes report
  ([`src/data/synthetic-kubernetes-report-later.json`](src/data/synthetic-kubernetes-report-later.json))
  and adaptations to the existing two synthetic GitLab reports, so both
  demos have a real earlier/later pair to compare.

`/demo/kubernetes` and `/demo/gitlab` each still hydrate exactly one
island (`DemoController`, `client:load`); `/` is unchanged.

Phase 3G adds a fourth route, `/explorer`, that opens one or two local
`report.json` files entirely in the browser:

- A new import pipeline
  ([`src/features/local-report-explorer/importLocalReportFile.ts`](src/features/local-report-explorer/importLocalReportFile.ts)):
  a case-insensitive `.json`-extension check runs before any read;
  `assertReportFileSize` runs before `File.text()`; the parsed JSON is
  then passed through the same `parseReport` used by the demo routes.
  Sanitized errors (`LocalReportExplorer`'s own
  [`errors.ts`](src/features/local-report-explorer/errors.ts)) never
  reproduce a filename, a native `JSON.parse` message, a Zod issue, or an
  arbitrary caught-exception string.
- A race-safe `useReportSlot` hook
  ([`src/features/local-report-explorer/useReportSlot.ts`](src/features/local-report-explorer/useReportSlot.ts)):
  a per-slot generation counter, so the latest file selection always
  wins regardless of resolution order, and `clear()` invalidates any
  read still in flight.
- Two labeled file inputs ("Earlier or primary report", "Later report for
  comparison (optional)") in
  [`LocalReportExplorer.tsx`](src/features/local-report-explorer/LocalReportExplorer.tsx),
  each `accept=".json,application/json"` with no `multiple` or directory
  selection, per-slot clear controls plus a clear-all control, and a
  findings/executive-summary view toggle -- the same real
  `ReportWorkspace`/`ExecutiveSummary` components the demo routes use,
  never a reimplementation.
- Comparison is handled by a `compareReports` dispatcher moved into
  [`src/features/comparison/compare.ts`](src/features/comparison/compare.ts)
  and shared by both `DemoController` and the explorer -- no duplicated
  platform-dispatch logic.
- `ReportWorkspace` and `ExecutiveSummary` both take a `source: "synthetic"
  | "local"` discriminant; the demo routes always pass `"synthetic"`
  ("Synthetic demonstration") and the explorer always passes `"local"`
  ("Local report") -- never a report-derived string.
- Astro 7's native `security.csp` support is enabled
  ([`astro.config.mjs`](astro.config.mjs)), with a shared restrictive
  directive set
  ([`src/lib/reportRouteCsp.ts`](src/lib/reportRouteCsp.ts): `default-src
  'none'; connect-src 'none'; img-src 'self'; font-src 'none'; object-src
  'none'; base-uri 'none'; form-action 'none'; frame-src 'none';
  worker-src 'none'; media-src 'none'; manifest-src 'none'`, plus Astro's
  own hash-based `script-src`/`style-src`) applied on `/explorer`,
  `/demo/kubernetes`, and `/demo/gitlab`.
- A new `@playwright/test` dev dependency (Chromium only for now --
  `@axe-core/playwright` and the full cross-browser matrix are Phase 3J)
  drives
  [`tests/e2e/local-report-explorer.spec.ts`](tests/e2e/local-report-explorer.spec.ts)
  against the real production build (`npm run build` then Playwright's
  own `astro preview` webServer), proving zero network requests/failures
  during import and interaction, no `localStorage`/`sessionStorage`/
  IndexedDB/cookie/service-worker artifacts, an empty state after reload,
  and CSP-compatible hydration. Run it with `npm run test:e2e` (requires
  `npx playwright install chromium` once, and `npm run build` first).

The following remain **intentionally absent**, and arrive in later phases
(see the milestone document, §R):

- The check catalogue and other product pages (`/checks`, `/roadmap`, `/learn`, etc.).
- The contact/feedback endpoint(s) or Worker source.
- Any Cloudflare configuration (`wrangler.jsonc`, adapter, etc.) or deployment
  workflow.
- Full automated accessibility (`axe`) scanning and the Firefox/WebKit
  legs of the Playwright matrix (Phase 3J).

**No production deployment is authorized in this phase or by anything in this
directory.** Deployment requires a separate, explicit, later authorization (see the
milestone document, §N and Phase 3K).

## Requirements

- **Node.js 24** (LTS) &mdash; see [`.nvmrc`](.nvmrc). Using a different major version
  is not supported for this project.
- **npm** (this project's package manager; no other JavaScript package manager is
  used here).

## Commands

Run all commands from this directory (`web/`).

```bash
# Install exactly what package-lock.json specifies.
npm ci

# Local development server.
npm run dev

# Type/content-diagnostic check (astro check).
npm run check

# Lint (ESLint flat config; covers .js/.ts/.tsx/.astro).
npm run lint

# Unit tests (Vitest).
npm run test

# Production static build (outputs to dist/).
npm run build

# Preview the production build locally.
npm run preview

# End-to-end tests (Playwright, Chromium only). Requires a production
# build first (npm run build) and, once, npx playwright install chromium.
npm run test:e2e
```

## Design notes

- Astro, configured for **static output only** &mdash; no SSR adapter.
- The official `@astrojs/react` integration provides React islands. `/`
  remains fully static with zero client-side hydration; `/demo/kubernetes`
  and `/demo/gitlab` each hydrate exactly one island (`DemoController`,
  containing the scan-state controller, `ReportWorkspace`, and
  `ExecutiveSummary` internally) via `client:load`; `/explorer` hydrates
  exactly one island (`LocalReportExplorer`) via `client:load`, with no
  synthetic or default report serialized into its props (`props="{}"` in
  the built output); no other route hydrates anything. `DemoController`
  deliberately never receives a
  function as a prop from its `.astro` page: Astro's island-props
  serialization is JSON-based, so a function value cannot survive
  `client:load` hydration (this was verified directly against the built
  output during Phase 3F). It instead picks the platform-appropriate
  comparator internally, from the reports' own `platform` field.
- TypeScript runs under Astro's `strictest` preset.
- All styling is project-owned CSS (custom properties in
  `src/styles/global.css`, plus small per-feature stylesheets under
  `src/features/report-workspace/`, `src/features/executive-summary/`, and
  `src/features/demo-controller/`) &mdash; no UI framework, no CSS
  framework, no icon package, no chart library, no external font or icon
  service.
- No analytics, telemetry, session replay, advertising, or third-party runtime
  script is present anywhere in this phase. Every island keeps all state in
  React memory only (no `localStorage`/`sessionStorage`/IndexedDB/cookies)
  and never calls `fetch`/`XMLHttpRequest`/`WebSocket`/`sendBeacon`.
- The report-import layer (`src/features/report-import/`) uses
  [Zod](https://zod.dev/) for runtime schema validation and
  [Vitest](https://vitest.dev/) for unit tests; both run against plain
  TypeScript/JSON in a Node test environment, with no DOM emulation and no
  network access. The comparison (`src/features/comparison/`) and
  executive-summary (`src/features/executive-summary/`) calculation logic
  are likewise plain TypeScript, tested the same way.
- Component tests for `ReportWorkspace`, `ExecutiveSummary`, and
  `DemoController` (`tests/component/`) use
  [React Testing Library](https://testing-library.com/react),
  `@testing-library/user-event`, and `jsdom`. The jsdom environment is
  opted into per test file (a `// @vitest-environment jsdom` docblock),
  not project-wide, so the plain-TypeScript unit tests keep running under
  the faster, DOM-free Node environment.
