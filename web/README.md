# CloudOps Guard web

This is the public website for CloudOps Guard: a v0.3.0, browser-only interactive
demo and local `report.json` explorer, alongside the existing Kubernetes and GitLab
CLI audits. See
[`../docs/milestones/v0.3.0-interactive-web-demo.md`](../docs/milestones/v0.3.0-interactive-web-demo.md)
for the full design and scope reference, and [`../CLAUDE.md`](../CLAUDE.md) for
durable, cross-project rules.

## Current phase: 3D &mdash; Kubernetes Interactive Demonstration

The static web foundation (Phase 3B) and the browser-side report-contract
layer under [`src/features/report-import/`](src/features/report-import/)
(Phase 3C) are unchanged. Phase 3D adds the first interactive route,
**`/demo/kubernetes`**: a deterministic synthetic Kubernetes report
([`src/data/synthetic-kubernetes-report.json`](src/data/synthetic-kubernetes-report.json),
covering all six implemented Kubernetes checks) is parsed at build time
through the existing `parseKubernetesReport`, then passed to a reusable
React report-workspace island under
[`src/features/report-workspace/`](src/features/report-workspace/) that
provides search, severity/category/resource-kind filtering, deterministic
sorting, and keyboard-accessible finding details. `/demo/kubernetes`
hydrates only that one island (`client:load`); `/` remains fully static
with zero client-side hydration, as before. The following remain
**intentionally absent**, and arrive in later phases (see the milestone
document, §R):

- The GitLab interactive demonstration.
- The local report explorer, and the report-import UI it needs (file
  selection, drag-and-drop, `File`/`FileReader` usage).
- Comparison logic, fingerprints, or the executive-summary view.
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
  hydrates exactly one island (the report-workspace) via `client:load`, and
  no other route hydrates anything.
- TypeScript runs under Astro's `strictest` preset.
- All styling is project-owned CSS (custom properties in
  `src/styles/global.css`, plus a small workspace-specific stylesheet under
  `src/features/report-workspace/`) &mdash; no UI framework, no CSS framework,
  no icon package, no external font or icon service.
- No analytics, telemetry, session replay, advertising, or third-party runtime
  script is present anywhere in this phase. The report-workspace island keeps
  all state in React memory only (no `localStorage`/`sessionStorage`/
  IndexedDB/cookies) and never calls `fetch`/`XMLHttpRequest`/`WebSocket`.
- The report-import layer (`src/features/report-import/`) uses
  [Zod](https://zod.dev/) for runtime schema validation and
  [Vitest](https://vitest.dev/) for unit tests; both run against plain
  TypeScript/JSON in a Node test environment, with no DOM emulation and no
  network access.
- The report-workspace island's component tests
  (`tests/component/report-workspace/`) use
  [React Testing Library](https://testing-library.com/react),
  `@testing-library/user-event`, and `jsdom`. The jsdom environment is
  opted into per test file (a `// @vitest-environment jsdom` docblock),
  not project-wide, so the plain-TypeScript unit tests keep running under
  the faster, DOM-free Node environment.
