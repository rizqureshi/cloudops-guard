# CloudOps Guard web

This is the public website for CloudOps Guard: a v0.3.0, browser-only interactive
demo and local `report.json` explorer, alongside the existing Kubernetes and GitLab
CLI audits. See
[`../docs/milestones/v0.3.0-interactive-web-demo.md`](../docs/milestones/v0.3.0-interactive-web-demo.md)
for the full design and scope reference, and [`../CLAUDE.md`](../CLAUDE.md) for
durable, cross-project rules.

## Current phase: 3F &mdash; Comparison and Executive Summary

The static web foundation (Phase 3B), the browser-side report-contract
layer (Phase 3C), and the Kubernetes and GitLab interactive demonstrations
at `/demo/kubernetes` and `/demo/gitlab` (Phases 3D and 3E, both closed)
are unchanged in their route/content fundamentals. Phase 3F adds
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
island (`DemoController`, `client:load`); `/` is unchanged. The following
remain **intentionally absent**, and arrive in later phases (see the
milestone document, §R):

- The local report explorer, and the report-import UI it needs (file
  selection, drag-and-drop, `File`/`FileReader` usage).
- The check catalogue and other product pages (`/checks`, `/roadmap`, `/learn`, etc.).
- The contact/feedback endpoint(s) or Worker source.
- Any Cloudflare configuration (`wrangler.jsonc`, adapter, etc.) or deployment
  workflow.

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
```

## Design notes

- Astro, configured for **static output only** &mdash; no SSR adapter.
- The official `@astrojs/react` integration provides React islands. `/`
  remains fully static with zero client-side hydration; `/demo/kubernetes`
  and `/demo/gitlab` each hydrate exactly one island (`DemoController`,
  containing the scan-state controller, `ReportWorkspace`, and
  `ExecutiveSummary` internally) via `client:load`; no other route
  hydrates anything. `DemoController` deliberately never receives a
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
