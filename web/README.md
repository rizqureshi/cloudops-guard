# CloudOps Guard web

This is the public website for CloudOps Guard: a v0.3.0, browser-only interactive
demo and local `report.json` explorer, alongside the existing Kubernetes and GitLab
CLI audits. See
[`../docs/milestones/v0.3.0-interactive-web-demo.md`](../docs/milestones/v0.3.0-interactive-web-demo.md)
for the full design and scope reference, and [`../CLAUDE.md`](../CLAUDE.md) for
durable, cross-project rules.

## Current phase: 3K &mdash; Authorized Deployment and Release Preparation

The static web foundation (Phase 3B), the browser-side report-contract
layer (Phase 3C), the Kubernetes and GitLab interactive demonstrations at
`/demo/kubernetes` and `/demo/gitlab` (Phases 3D and 3E), comparison plus
the executive summary (Phase 3F), the local report explorer at
`/explorer` (Phase 3G), the check catalogue plus product/educational
pages (Phase 3H), the contact/feedback boundary at `/request-demo` and
`/feedback` (Phase 3I), and accessibility/security/release-readiness
(Phase 3J) are unchanged in their route/content fundamentals; Phases
3B&ndash;3J are all closed (Phase 3J on commit `7352a9a`, with both `CI`
and `Web CI` green — see
[`../docs/reviews/v0.3.0-phase-3j-release-readiness.md`](../docs/reviews/v0.3.0-phase-3j-release-readiness.md)
for its full evidence, including the project owner's 2026-08-24
Chrome-151.0.7922.173 manual keyboard/VoiceOver/200%-zoom review that
closed its accessibility/manual-review gate). **Phase 3K is now being
prepared**: Cloudflare deployment configuration for two independent
units (a static-assets unit with no `main`, serving `dist/` via Workers
Static Assets as a custom domain; a contact-API unit routing only the
exact `<hostname>/api/contact` path to the existing, unmodified
`worker/contact.ts`), a dependency-free config renderer
(`deploy/render-wrangler-configs.mjs`), a manual-dispatch-only
`.github/workflows/deploy-web.yml`, and
[`../docs/deployment/web-production.md`](../docs/deployment/web-production.md)
documenting the full future deployment procedure. **No commit has been
created for this work yet, and no Cloudflare Worker, route, domain,
preview, tag, release, or publication exists** — Phase 3K prepares
deployment-ready configuration only; it does not deploy, publish, tag,
or release anything, and no step in it runs Wrangler, contacts a
Cloudflare API, or triggers `deploy-web.yml`. Phase 3K is not closed
until this uncommitted package passes independent review and a
subsequently approved commit passes `CI`/`Web CI` (see the milestone
document for the exact closure gate).
Phase 3F added comparison and an executive summary to both demo routes:

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
- A new `@playwright/test` dev dependency (Chromium only at the time --
  `@axe-core/playwright` and the full Chromium/Firefox/WebKit matrix were
  added in Phase 3J) drives
  [`tests/e2e/local-report-explorer.spec.ts`](tests/e2e/local-report-explorer.spec.ts)
  against the real production build (`npm run build` then Playwright's
  own `astro preview` webServer), proving zero network requests/failures
  during import and interaction, no `localStorage`/`sessionStorage`/
  IndexedDB/cookie/service-worker artifacts, an empty state after reload,
  and CSP-compatible hydration. Run it with `npm run test:e2e` (requires
  `npx playwright install chromium` once, and `npm run build` first).

Phase 3H adds the check catalogue and the remaining product/educational
pages:

- A project-owned catalogue of all 17 currently implemented checks
  ([`src/data/check-catalogue.json`](src/data/check-catalogue.json)),
  loaded and Zod-validated at module-evaluation time (build/test time) --
  a duplicate ID, invalid platform/severity, or missing required text
  fails loudly rather than shipping. Verified against the real Python
  check functions by a Python contract test
  ([`../tests/test_web_check_catalogue_contract.py`](../tests/test_web_check_catalogue_contract.py)):
  it calls `evaluate_container`, `evaluate_container_restarts`,
  `evaluate_protected_branch_checks` (twice, since `GL-BR-001` and
  `GL-BR-002`/`GL-BR-003` require two mutually exclusive
  branch-protection states), `evaluate_project_setting_checks`,
  `evaluate_job_timeout_check`, and `evaluate_ci_image_check` directly and
  compares each check's ID/title/severity against the catalogue -- never
  reimplementing a check's condition or restating its expected values in
  a second hard-coded table.
- A searchable catalogue island
  ([`src/features/check-catalogue/`](src/features/check-catalogue/)) at
  `/checks`: search by check ID/title, platform/category/severity
  filters, a clear-filters action, a live "Showing X of 17 checks" count,
  and an empty-results message. Reuses the existing `deriveCategory`
  utility from `../report-workspace/category.ts` rather than a second
  category mapping.
- Seventeen static per-check detail pages at
  [`src/pages/checks/[id].astro`](src/pages/checks/[id].astro), generated
  via `getStaticPaths` from the catalogue data -- no React island on any
  of them.
- `/roadmap`, `/learn` (an index linking its two subpages),
  `/learn/read-only-audits`, `/learn/local-report-privacy` (describing
  the real `File.text()` -> `JSON.parse()` -> parser -> React-memory flow
  the explorer actually uses -- explicitly not the older `FileReader`
  API), and `/privacy` -- all static pages with zero islands.
- `/` was reworked to follow the milestone document's §D information
  order, ending on an honest, non-interactive "requesting a pilot"
  statement (no form, no disabled fake control, no placeholder route)
  since `/request-demo` remains unimplemented. Its former illustrative
  severity-badge preview -- which showed a "Critical" badge even though
  no currently implemented check reaches Critical severity -- was
  replaced with a real "anatomy of a finding" example built directly from
  the `K8S-IMG-001` catalogue entry, never a fabricated result.
- Header navigation gained Checks/Learn/Roadmap; footer navigation gained
  Checks/Learn/Roadmap/Privacy. Neither links to `/request-demo` or
  `/feedback`, which remain unimplemented.

The production build now contains exactly 27 routes (the 4 prior routes,
`/checks`, the 17 detail pages, `/roadmap`, `/learn` plus its 2 subpages,
and `/privacy`). `/checks` hydrates exactly one island
(`CheckCatalogue`); every other Phase 3H page has zero islands; the
existing demo/explorer island counts and their restrictive CSP are
unchanged.

Phase 3I adds the contact and feedback boundary: `/request-demo`,
`/feedback`, and an isolated Worker endpoint behind them.

- A shared, reusable form island
  ([`src/features/contact-form/`](src/features/contact-form/)), used on
  both pages with a different `formType` (`"pilot_request"` /
  `"feedback"`). A neutral, strict Zod contract
  ([`contract.ts`](src/features/contact-form/contract.ts)) allows exactly
  `formType`/`name`/`workEmail`/`company`/`message`/`consent`/
  `turnstileToken` -- unknown fields, over-length values, non-literal
  `consent`, and inappropriate control characters are all rejected
  outright, never truncated -- and is imported by both the browser form
  and the Worker, with zero dependency on any report-related feature (see
  the automated isolation test below).
- Explicit-rendering Cloudflare Turnstile integration
  ([`turnstile.ts`](src/features/contact-form/turnstile.ts),
  [`useTurnstile.ts`](src/features/contact-form/useTurnstile.ts)): the
  official `challenges.cloudflare.com/turnstile/v0/api.js` script is
  loaded at most once per page (`render=explicit`, an `onload`
  query-parameter callback, no duplicate insertion on remount), reads
  `PUBLIC_TURNSTILE_SITE_KEY` (the build fails with a sanitized error if
  it is absent -- see [`.env.example`](.env.example)), and never exposes
  a secret key to client code. Every attempted submission consumes and
  resets the current token, so a retry always requires a fresh challenge.
- A single isolated Worker endpoint,
  [`worker/contact.ts`](worker/contact.ts) (`POST /api/contact`, source
  only in this phase -- not deployed, no Wrangler configuration), which
  enforces, in this exact order: exact path and method (query-string
  variations and non-`POST` methods rejected) -> exact-match `Origin`
  (parsed and compared to `request.url`'s own origin, never
  suffix/substring/wildcard-matched, no CORS reflection) -> an exact
  `application/json` Content-Type with no parameters -> a rejected
  `Content-Encoding` -> an 8&nbsp;KiB body limit enforced twice
  ([`worker/readBoundedBody.ts`](worker/readBoundedBody.ts): a declared
  oversized `Content-Length` is rejected before any read, and a bounded
  incremental read separately stops the instant actual bytes exceed the
  limit, covering a chunked or dishonest/absent `Content-Length` alike --
  `request.text()`/`request.json()` are never called) -> JSON parsing and
  an object-shape check -> the shared contract -> mandatory server-side
  Turnstile verification
  ([`worker/turnstile.ts`](worker/turnstile.ts): one POST to the real
  Siteverify endpoint, hostname- and `formType`-action-matched, a bounded
  timeout, no visitor IP ever sent or retained, no caching, no retry on
  an ambiguous response -- every failure mode collapses to one sanitized
  `verification_failed` response) -> email delivery
  ([`worker/email.ts`](worker/email.ts): exactly one plain-text email via
  the structured `EMAIL.send({ to, from, subject, text })` binding, with
  `to`/`from`/subject always fixed from Worker configuration or a fixed
  per-`formType` subject table -- never from submitted input, so no
  visitor-controlled header, recipient, CC/BCC, or attachment is
  possible, and no acknowledgement email is ever sent to the visitor's
  own address). A binding failure returns a sanitized
  `503 temporarily_unavailable` response carrying the configured
  destination address as a fallback; the client re-validates that value
  as a plain email address before constructing its own `mailto:` link
  (a fixed subject only -- never the visitor's name, message, or
  Turnstile token). Every response is a fixed, sanitized JSON body
  (`Content-Type`, `Cache-Control: no-store`,
  `X-Content-Type-Options: nosniff`; never an echoed value, a Zod issue,
  a Turnstile response, an email-binding error, or a stack trace); the
  Worker contains no `console.log`/`console.error` anywhere.
- A dedicated contact-route CSP
  ([`src/lib/contactRouteCsp.ts`](src/lib/contactRouteCsp.ts)) permits
  `'self'` and `https://challenges.cloudflare.com` only -- the sole
  external-script exception anywhere on this site, and kept entirely
  separate from [`reportRouteCsp.ts`](src/lib/reportRouteCsp.ts). Astro's
  `insertScriptResource` silently drops its own default `'self'`
  script-src source the instant *any* custom resource is inserted at
  all (confirmed directly against Astro's `renderCspContent` source),
  so both contact pages explicitly re-insert `'self'` alongside the
  Turnstile origin -- discovered as a genuine hydration-breaking bug
  during this phase's own manual review against a real production
  build, not by inspection alone.
- An automated architectural-isolation test
  ([`tests/unit/contact-form/isolation.test.ts`](tests/unit/contact-form/isolation.test.ts))
  builds a real import graph from the actual source files on disk (never
  a hand-maintained list) and proves, in both directions, that the
  contact/Worker feature and every report-related feature
  (`report-import`, `report-workspace`, `local-report-explorer`,
  `comparison`, `executive-summary`, `demo-controller`,
  `check-catalogue`) are mutually unreachable, and that no report-related
  source contains `/api/contact` or references `submitContactForm`.

`/request-demo` and `/feedback` each hydrate exactly one island
(`ContactForm`); every other route's island count and CSP are unchanged.
No new dependency was added -- the Worker uses only standard
Fetch-API-shaped types and local structural interfaces for its two custom
bindings.

As of the end of Phase 3I, the following remained intentionally absent;
full automated accessibility (`axe`) scanning and the Firefox/WebKit legs
of the Playwright matrix were subsequently added in Phase 3J (see below).
The following still remain **intentionally absent**, and arrive in Phase 3K
(see the milestone document, §R):

- Real Cloudflare account/domain/binding provisioning, Wrangler
  configuration (`wrangler.jsonc`, adapter, etc.), or any deployment
  workflow.

## Phase 3J: accessibility, security and release readiness

Phase 3J added automated `@axe-core/playwright` accessibility scanning
across every one of the 29 production routes plus 12 representative
interactive states, expanded the Playwright matrix from Chromium-only to
Chromium/Firefox/WebKit (all three run by default via `npm run test:e2e`),
and added an automated product-quality spec covering HTTP status,
heading/landmark structure, title/description uniqueness, internal-link
validity, console/page errors, island counts, viewport overflow, the skip
link, keyboard operability, visible focus, reduced-motion behaviour, and
colour-independent severity text
([`tests/e2e/accessibility.spec.ts`](tests/e2e/accessibility.spec.ts),
[`tests/e2e/product-quality.spec.ts`](tests/e2e/product-quality.spec.ts),
[`tests/e2e/route-inventory.spec.ts`](tests/e2e/route-inventory.spec.ts),
sharing one route inventory at
[`tests/e2e/support/routes.ts`](tests/e2e/support/routes.ts)). This
testing found and fixed three genuine production-code defects -- an
ARIA-prohibited-attribute accessibility bug, a Firefox-only CSP console
error from Zod's internal JIT-availability probe (fixed with
[`src/lib/zodJitless.ts`](src/lib/zodJitless.ts), never by weakening any
CSP), and a WebKit-only skip-link focus failure -- each with a non-vacuous
regression test. A follow-up correction pass fixed a test-isolation gap
that let one aggregate test reach the real Turnstile service (fixed with
a file-level `beforeEach`) and added `npm audit --audit-level=high` as an
enforced `Web CI` step. `@axe-core/playwright` is the only new
dependency. **Automated** scripted, pointer-free keyboard interaction and
a narrower-scope manual visual screenshot inspection (exact coverage
stated precisely, not overstated) are recorded and pass. The three gates
that genuinely require a person -- a manual keyboard review, a
screen-reader spot-check (VoiceOver), and a literal 200% browser-zoom
review -- were personally completed by the project owner on 2026-08-24
using Google Chrome Version 151.0.7922.173, each reported as **Pass**
with no issues found, recorded in
[`../docs/reviews/v0.3.0-phase-3j-release-readiness.md`](../docs/reviews/v0.3.0-phase-3j-release-readiness.md),
which also carries full §Q evidence, CSP/privacy/dependency-audit/
performance findings, and exact test counts. **Phase 3J is closed**, on
commit `7352a9aa17af9ba55f07cde1700ee1b72d5b65d0` (`feat(web): complete
accessibility and release readiness`), for which independent review of
the final package was completed and both `CI` (run `32797606973`) and
`Web CI` (run `32797606927`) succeeded.

## Phase 3K: authorized deployment and release preparation

Phase 3K prepares Cloudflare deployment configuration for two
independent units and documents a future, separately authorized
deployment procedure -- it does not deploy anything. A dependency-free
Node ESM renderer
([`deploy/render-wrangler-configs.mjs`](deploy/render-wrangler-configs.mjs))
generates two Wrangler configuration files from strictly validated
`DEPLOY_*` environment variables only: a **static-assets unit** (no
`main`, serves `dist/` through Workers Static Assets with
`not_found_handling: "404-page"`, attached only to the configured
hostname as a custom domain) and a **contact-API unit** (the existing,
unmodified [`worker/contact.ts`](worker/contact.ts), routed only to the
exact `<hostname>/api/contact` path, never a wildcard). Both units set
`workers_dev: false` and `preview_urls: false`. Validation fails closed
on missing/empty/malformed/placeholder/control-character/whitespace-
surrounded/invalid-DNS/port/URL/path/wildcard/query/fragment values and
on a hostname outside its zone (an exact dot-qualified suffix check --
`hostname === zone` or `hostname.endsWith("." + zone)` -- deliberately
never a naive `endsWith(zone)`, which would wrongly accept
`notexample.com` for zone `example.com`). Output files are created
exclusively (never overwriting an existing path or following a symlink),
mode `0600`; a failed second file's creation removes the first file that
same call created, so no partial pair is left behind. No Cloudflare API
token, account ID, or the Turnstile secret's *value* is ever read,
required, or written by this script -- the contact config only declares
that `TURNSTILE_SECRET_KEY` must be provisioned, by name, out of band.

[`../.github/workflows/deploy-web.yml`](../.github/workflows/deploy-web.yml)
is manual-`workflow_dispatch`-only, `contents: read`, and requires an
exact typed confirmation phrase plus a full 40-hex-character commit SHA,
verified in a Cloudflare-credential-free `validate` job (dispatch ref is
`main`, SHA format, checked-out `HEAD` matches it, and it is reachable
from `origin/main`) before a `production`-GitHub-environment-gated
`deploy` job can run at all; Wrangler is pinned to exactly
`wrangler@4.102.0`; the generated configuration is removed in an
`always()` step; no secret or generated configuration is ever echoed or
uploaded as an artifact.
[`../docs/deployment/web-production.md`](../docs/deployment/web-production.md)
documents the full topology/privacy rationale, every environment
variable and secret by name and classification, Cloudflare/Turnstile/
Email-Routing prerequisites, the `production` environment's required
protections, a pre-deployment checklist, a future post-deployment
verification checklist, and the non-atomic two-unit deployment order with
explicit failure-handling and recovery guidance -- including a deliberate
statement that rollback has not yet been tested.

**No commit has been created for this work yet, and no Cloudflare
Worker, route, domain, preview, tag, release, or publication exists.**
Phase 3K is not closed until this uncommitted package passes independent
review and a subsequently approved commit passes `CI` and `Web CI`.

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

`npm run build`/`npm run dev`/`npm run test:e2e` require
`PUBLIC_TURNSTILE_SITE_KEY` to be set (`/request-demo` and `/feedback`
fail the build otherwise) -- copy [`.env.example`](.env.example) to
`.env` first. For local development and CI, use Cloudflare's official,
publicly documented always-passing test site key,
`1x00000000000000000000AA` (a published testing identifier, not a
secret) -- never a real site key or the real secret key in this
repository.

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

# End-to-end tests (Playwright: Chromium, Firefox, and WebKit, all by
# default). Requires a production build first (npm run build) and, once,
# npx playwright install --with-deps chromium firefox webkit. Add
# --project=chromium (or firefox/webkit) to run a single engine.
# Turnstile and /api/contact are intercepted/mocked in these tests --
# no real Turnstile verification or email is ever triggered.
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
  the built output); `/checks` hydrates exactly one island
  (`CheckCatalogue`) via `client:load`; `/request-demo` and `/feedback`
  each hydrate exactly one island (`ContactForm`) via `client:load`; no
  other route, including every `/checks/[id]` detail page and every
  Phase 3H/3I product/educational page, hydrates anything. `DemoController`
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
