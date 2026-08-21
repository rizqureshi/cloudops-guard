# CloudOps Guard web

This is the public website for CloudOps Guard: a v0.3.0, browser-only interactive
demo and local `report.json` explorer, alongside the existing Kubernetes and GitLab
CLI audits. See
[`../docs/milestones/v0.3.0-interactive-web-demo.md`](../docs/milestones/v0.3.0-interactive-web-demo.md)
for the full design and scope reference, and [`../CLAUDE.md`](../CLAUDE.md) for
durable, cross-project rules.

## Current phase: 3B &mdash; Web Foundation and Design System

This directory currently contains **only the static web foundation**: the
Astro/React/TypeScript project skeleton, the project-owned CSS design-token system,
a shared layout with header/footer, and one real page (`/`). It is intentionally
**static-only foundation work**, not a feature-complete site. The following are
**intentionally absent** from this phase, and arrive in later phases (see the
milestone document, §R):

- Report parsing, `NormalizedWebReport`, or any report-contract code.
- Synthetic Kubernetes/GitLab demonstration data or demo pages.
- The local report explorer or any report-import UI.
- Comparison logic or the executive-summary view.
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

# Production static build (outputs to dist/).
npm run build

# Preview the production build locally.
npm run preview
```

## Design notes

- Astro, configured for **static output only** &mdash; no SSR adapter.
- The official `@astrojs/react` integration is installed and configured for later
  React islands (the interactive report workspace, comparison view, etc.), but this
  phase's one page is fully static with zero client-side hydration.
- TypeScript runs under Astro's `strictest` preset.
- All styling is project-owned CSS (custom properties in
  `src/styles/global.css`) &mdash; no UI framework, no CSS framework, no external font
  or icon service.
- No analytics, telemetry, session replay, advertising, or third-party runtime
  script is present anywhere in this phase.
