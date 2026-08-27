# Durable project rules for CloudOps Guard

These rules govern how work on this repository should be approached. They apply
regardless of which milestone is currently in progress.

## Scope discipline

- Work incrementally, one milestone at a time. v0.1.0, the Kubernetes audit MVP, is
  released (see README.md). v0.2.0, the read-only, single-project GitLab CI/CD Audit
  MVP, is also released — see `docs/milestones/v0.2.0-gitlab-audit.md` for its
  objective, command interface, checks, invariants and non-goals. GitLab
  implementation and controlled acceptance testing for the documented v0.2.0 scope
  are complete: the HTTP client foundation, a
  normalized instance/project/protected-branch collector, the
  protected-default-branch checks (`GL-BR-001` through `GL-BR-003`), the
  project-setting checks (`GL-MR-001`, `GL-SEC-001` through `GL-SEC-003`,
  `GL-COST-001`, `GL-COST-002`), the job timeout check (`GL-REL-001`), the
  separate CI Lint collection/normalization together with `GL-CI-001`, the
  combined GitLab evaluator (`evaluate_gitlab` in
  `src/cloudops_guard/engine/evaluator.py`, which builds `GitLabAuditReport`),
  GitLab JSON/HTML report-file rendering (`generate_gitlab_reports` in
  `src/cloudops_guard/reports/generator.py`, with its own
  `gitlab_report.html.j2` template), and end-to-end CLI integration
  (`cloudops-guard audit gitlab --gitlab-url ... --project ...
  --job-timeout-threshold-seconds ... --output ...`, Phase 2E-A) exist.
  Kubernetes report generation (`generate_reports`, `report.html.j2`) and the
  Kubernetes CLI command remain a separate, unchanged contract. Controlled
  acceptance testing has passed on both GitLab.com's current hosted version
  and self-managed GitLab CE 18.4.6, at Owner and at Maintainer — on
  GitLab.com via a Maintainer-role project service account, and on
  self-managed via an ordinary, non-service-account internal user account
  (see `docs/milestones/v0.2.0-gitlab-audit.md`, "Controlled GitLab.com
  acceptance record — 2026-08-18" and "Controlled self-managed GitLab CE
  18.4.6 acceptance record — 2026-08-18"); the documented minimum required
  role for the implemented GitLab audit is now Maintainer with `read_api`,
  not Owner. A human-operated Maintainer account on GitLab.com, other
  self-managed GitLab releases/editions, project/group access tokens, OAuth
  tokens, fine-grained PATs, administrator tokens remain outstanding for
  future evidence-broadening, and do not block this milestone's documented
  scope. v0.2.0 was tagged as the annotated tag `v0.2.0`, peeling to release
  commit `ed358dc4006697632aaf87bafb654b44a18daa8c`, for which remote CI
  (GitHub Actions) passed. It was published as a GitHub Release —
  "CloudOps Guard v0.2.0 — GitLab Audit MVP"
  (<https://github.com/rizqureshi/cloudops-guard/releases/tag/v0.2.0>),
  published 2026-08-19T21:36:19Z — non-draft, non-prerelease, and identified
  by GitHub as the latest release. Implementation, controlled acceptance,
  release preparation, release CI, annotated tagging, and GitHub Release
  publication are all complete for the documented v0.2.0 scope.
- **The current approved milestone is v0.3.0: Interactive Web Demo and Local
  Report Explorer** — see `docs/milestones/v0.3.0-interactive-web-demo.md` for
  its full objective, approved technology stack, routes, report-contract
  handling, comparison semantics, synthetic-data requirements, privacy
  boundary, contact/feedback boundary, deployment plan, accessibility target,
  non-goals, acceptance criteria, and phased plan (Phases 3A–3K). Its
  architecture and scope are approved; **Phase 3A (the milestone document and
  the accompanying `CLAUDE.md` update) is complete.** **Phase 3B has
  introduced the Astro/React/TypeScript web foundation** under a new `web/`
  directory (project skeleton, project-owned CSS design-token system, shared
  header/footer layout, one static page at `/`) and a validation-only
  `web-ci.yml` (type check, lint, unit tests, build — never deploys). **The
  web foundation is static**, with zero client-side hydration on its one
  page. **Phase 3C introduced the browser-side report-contract layer**
  under `web/src/features/report-import/`: strict Zod schemas mirroring the
  released Kubernetes and GitLab `report.json` contracts,
  `parseKubernetesReport`/`parseGitLabReport`/`parseReport`, a normalized
  `NormalizedWebReport` representation, deterministic severity-summary
  recomputation, and sanitized validation errors, covered by Vitest unit
  tests that `web-ci.yml` runs. **Phase 3C is closed**, on commit
  `15be4a873a4e4d149022d1f07f23f43d541b2b84` (`feat(web): add report schema
  adapters`), for which both `CI` (run `32535066964`) and `Web CI` (run
  `32535066959`) succeeded. **Phase 3D implemented the Kubernetes
  single-report interactive demonstration** at `/demo/kubernetes`: a
  deterministic synthetic Kubernetes report covering all six implemented
  checks (`web/src/data/synthetic-kubernetes-report.json`), parsed at build
  time through the existing `parseKubernetesReport`, and a reusable React
  report-workspace island (`web/src/features/report-workspace/`) providing
  search, severity/category/resource-kind filtering, deterministic sorting,
  and keyboard-accessible finding details — hydrated as the page's only
  interactive island (`client:load`), with the rest of the page remaining
  static. **Phase 3D is closed**, on commit
  `d94a86517b064ac816cdeaabe87eda675188326e`
  (`feat(web): add Kubernetes interactive demo`), for which both `CI` (run
  `32540231252`) and `Web CI` (run `32540231247`) succeeded. **Phase 3E
  implemented the GitLab interactive demonstration** at `/demo/gitlab`: two
  deterministic synthetic GitLab reports for the same fictional project
  (`web/src/data/synthetic-gitlab-report-unprotected-branch.json` and
  `-protected-branch.json`), together covering all eleven implemented
  GitLab checks, parsed at build time through the existing
  `parseGitLabReport`. **Phase 3E is closed**, on commit
  `f50863479dc55c1d9ac535fde87a82501a957e78`
  (`feat(web): add GitLab interactive demo`), for which both `CI` (run
  `32600251672`) and `Web CI` (run `32600251664`) succeeded. **Phase 3F has
  implemented comparison and the executive summary**: a browser-only
  comparison feature (`web/src/features/comparison/`) that fingerprints
  findings (never on severity/title/evidence/impact/recommendation/
  auto-remediation/timestamp) and multiset-matches an older and a newer
  report into new/persistent/resolved results, validating platform match,
  a strictly later timestamp, and target-identity compatibility first; a
  deterministic, non-AI-generated executive-summary feature
  (`web/src/features/executive-summary/`) built as a pure function of
  normalized/comparison data; and a shared `DemoController`
  (`web/src/features/demo-controller/`) offering earlier-scan/later-scan/
  compare-earlier-to-later modes plus a findings/executive-summary view
  toggle on both `/demo/kubernetes` and `/demo/gitlab` — each still
  hydrating exactly one island. The Kubernetes demo gained a second,
  strictly later synthetic report
  (`web/src/data/synthetic-kubernetes-report-later.json`); the GitLab
  demo's existing two reports were adapted (a persistent `GL-MR-001`
  finding added to the protected-branch state, and a second `GL-CI-001`
  entry added there to demonstrate the documented image-reference-change
  limitation) while preserving `GL-BR-001`'s isolation from
  `GL-BR-002`/`GL-BR-003`. The former per-platform `gitlab-demo` feature
  folder was removed in favor of the shared controller; `ReportWorkspace`
  was further generalized to render either a single report or a
  `ComparisonResult`. **Phase 3F is closed**, on commit
  `44baebd1224713d11ab9bb10f48bf46f0e1b7637` (`feat(web): add comparison
  and executive summary`), for which both `CI` (run `32608091012`) and
  `Web CI` (run `32608091018`) succeeded; Phase 3F's final committed web
  Vitest test count was **319** (corrected from an earlier-recorded 318,
  reflecting a `React` duplicate-key regression test added for
  `ExecutiveSummary`'s recommendation list). **Phase 3G has implemented
  `/explorer`, the local report explorer**: two labeled file inputs
  (earlier/primary report, optional later report for comparison), each
  imported through a new `src/features/local-report-explorer/` pipeline
  (`importLocalReportFile.ts`: case-insensitive `.json`-extension check
  before any read, `assertReportFileSize` before `File.text()`,
  `JSON.parse`, then the existing `parseReport`) with sanitized errors and
  a race-safe `useReportSlot` hook (per-slot generation counters so the
  latest file selection always wins and `clear()` invalidates any
  in-flight read). Comparison now goes through a `compareReports`
  dispatcher shared between `DemoController` and the explorer (moved into
  `src/features/comparison/compare.ts`, no duplicated platform-dispatch
  logic); `ReportWorkspace` and `ExecutiveSummary` gained a `source:
  "synthetic" | "local"` discriminant so the demo routes keep showing
  "Synthetic demonstration" and the explorer shows "Local report", never a
  report-derived label. Astro 7's native `security.csp` support is now
  enabled, with a shared restrictive directive set
  (`src/lib/reportRouteCsp.ts`: `connect-src 'none'` among eleven `'none'`
  directives, plus Astro's own hash-based `script-src`/`style-src`, no
  `unsafe-inline`/`unsafe-eval`) applied on `/explorer`,
  `/demo/kubernetes`, and `/demo/gitlab`. A new `@playwright/test` dev
  dependency (Chromium only; `@axe-core/playwright` and the full
  cross-browser matrix remain Phase 3J) drives
  `web/tests/e2e/local-report-explorer.spec.ts` against the real
  production build, proving zero network requests/failures and zero
  browser-storage/cookie/service-worker artifacts across a full
  import/search/filter/sort/comparison/clear interaction; `web-ci.yml` now
  installs Chromium and runs this suite after the production build, still
  validation-only (it never deploys or publishes). A focused correction
  pass, before commit, fixed a real error-message disclosure bug in
  `useReportSlot.ts` (it trusted `error.message` for any `instanceof
  Error`, not just the two sanitized error classes, so an unexpected
  rejection could leak a filename/path/report value) by checking
  `instanceof LocalImportError || instanceof ReportValidationError`
  explicitly. **Phase 3G is closed**, on commit
  `1a1a9bb5cde8de18b689d7636666dac0a34fecd9` (`feat(web): add local report
  explorer`), for which both `CI` (run `32682965675`) and `Web CI` (run
  `32682965663`, including the Playwright Chromium install and its 5-test
  run) succeeded; Phase 3G's final committed web test count was **383**
  Vitest tests (up from Phase 3F's 319) plus **5** Chromium Playwright
  end-to-end tests. **Phase 3H has implemented the product pages and the
  17-check catalogue**: a project-owned
  `web/src/data/check-catalogue.json`, Zod-validated (fails loudly on a
  duplicate ID, invalid platform/severity, or missing text), verified
  against the real Python check functions by a new
  `tests/test_web_check_catalogue_contract.py` (calls
  `evaluate_container`, `evaluate_container_restarts`,
  `evaluate_protected_branch_checks` twice for the two mutually exclusive
  `GL-BR-001` vs. `GL-BR-002`/`GL-BR-003` states,
  `evaluate_project_setting_checks`, `evaluate_job_timeout_check`, and
  `evaluate_ci_image_check` directly — never reimplementing a check's
  condition or restating its expected title/severity). A searchable
  `CheckCatalogue` island at `/checks` (search, platform/category/severity
  filters, clear, live count, empty state; reuses the existing
  `deriveCategory` utility rather than a second category mapping); 17
  static `/checks/[id]` detail pages via `getStaticPaths`, no island;
  `/roadmap`, `/learn` plus its two educational subpages
  (`/learn/read-only-audits`, `/learn/local-report-privacy`, correctly
  describing `File.text()`, never `FileReader`), and `/privacy` — all
  static, zero islands. `/` was reworked to this document's §D order,
  ending on an honest non-interactive "requesting a pilot" statement (no
  form, no fake control) since `/request-demo` remains unimplemented, and
  its former illustrative Critical-severity-badge preview (misleading,
  since no implemented check reaches Critical) was replaced with a real
  "anatomy of a finding" example built from the actual `K8S-IMG-001`
  catalogue entry. Header/footer navigation gained Checks/Learn/Roadmap(/
  Privacy), never `/request-demo` or `/feedback`. Production build
  contains exactly the expected 27 routes; `/checks` has exactly one
  island, every other new page has zero, and existing demo/explorer
  island counts and CSP are unchanged. New tests: 29 pure catalogue-data/
  filtering Vitest tests, 12 `CheckCatalogue` component tests, and 3
  Python contract tests — 424 total Vitest tests (up from 383) and 1026
  total pytest tests (up from 1023). A manual desktop/mobile Playwright
  screenshot review caught and fixed one real bug before commit: two
  `/privacy` paragraphs had inline links immediately preceded/followed by
  a line break with no literal space, which Astro's compiler collapses to
  zero width (unlike a browser's own whitespace collapsing) — fixed by
  keeping the affected text and links on the same source line. **Phase 3H
  is closed**, on commit `959e3f1620d6fcc007d4e58695018f48a2612506`
  (`feat(web): add product pages and check catalogue`), for which both
  `CI` (run `32746176915`) and `Web CI` (run `32746176930`) succeeded;
  Phase 3H's final committed counts were 424 Vitest tests, 5 Chromium
  Playwright tests, and 1026 pytest tests. **Phase 3I has implemented the
  contact and feedback boundary**: `/request-demo` and `/feedback`, each
  hydrating exactly one `ContactForm` island, sharing a neutral, strict
  Zod contract (`web/src/features/contact-form/contract.ts`: exactly
  `formType`/`name`/`workEmail`/`company`/`message`/`consent`/
  `turnstileToken`, unknown fields and inappropriate control characters
  rejected, nothing ever truncated). A single isolated Worker endpoint,
  `POST /api/contact` (`web/worker/contact.ts`, source only — not yet
  deployed, no Wrangler config), enforces in order: exact path/method →
  exact-match `Origin` (never suffix/wildcard/CORS-reflected) → exact
  `application/json` Content-Type → rejected `Content-Encoding` → an
  8 KiB body limit enforced twice (`readBoundedBody.ts`: a declared
  oversized `Content-Length` rejected before any read, plus a bounded
  incremental read that stops the instant actual bytes exceed the limit —
  `request.text()`/`request.json()` are never called) → the shared
  contract → mandatory server-side Turnstile verification
  (`turnstile.ts`: one POST to the real Siteverify endpoint, hostname-
  and `formType`-action-matched, bounded timeout, no visitor IP, no
  retry, every failure mode sanitized to one `verification_failed`
  response) → exactly one plain-text email via the structured
  `EMAIL.send({ to, from, subject, text })` binding (`email.ts`:
  recipient/sender/subject always fixed from Worker configuration, never
  from submitted input — no visitor-controlled header/recipient/
  attachment is possible, and no acknowledgement email is ever sent to
  the visitor). A binding failure returns a sanitized
  `503 temporarily_unavailable` response carrying the configured
  destination address, which the client re-validates as a plain email
  before building its own `mailto:` link (fixed subject only — never the
  visitor's name, message, or token). A dedicated contact-route CSP
  (`web/src/lib/contactRouteCsp.ts`) permits `'self'` and
  `https://challenges.cloudflare.com` only — the sole external-script
  exception anywhere on this site; a real bug was found and fixed here
  during this phase's own manual review (Astro's `insertScriptResource`
  silently drops its default `'self'` script-src source the instant any
  custom resource is inserted, which broke both pages' own hydration
  script under a real production build, caught via an actual
  CSP-violation console error, not by inspection alone — fixed by
  explicitly re-inserting `'self'`). An automated architectural-isolation
  test builds a real import graph from the actual source files on disk
  (never a hand-maintained list) proving the contact/Worker feature and
  every report-related feature are mutually unreachable in both
  directions — independently confirmed non-vacuous by injecting a
  violating import and watching the test fail before reverting it. No new
  dependency was added. Test coverage: 179 new Vitest tests for 603 total
  (up from 424), plus 14 new Chromium Playwright tests for 19 total (up
  from 5) — the original 5 report-explorer tests continue to pass
  unchanged. A subsequent, focused CI-and-documentation-only correction
  pass fixed `Web CI`'s missing `PUBLIC_TURNSTILE_SITE_KEY` build failure
  (added step-scoped, using Cloudflare's public test key, to the
  "Production build" step only) and corrected these Vitest figures from a
  stale 149-new/573-total to the accurate 179-new/603-total shown above,
  bringing Phase 3I's final tracked file count to 38 (`.github/workflows/
  web-ci.yml` joined the previously reviewed 37). **Phase 3I is closed**,
  on commit `e7428431890b90114b2b7ef22fe548445cb3dd9a` (`feat(web): add
  contact and feedback boundary`), for which both `CI` (run `32764250097`)
  and `Web CI` (run `32764250008`) succeeded — confirmed from the actual
  logs: 1026 pytest, 603 Vitest across 38 files, 19 Chromium Playwright,
  and a clean production build using the step-scoped public Turnstile test
  key. **Phase 3J has implemented accessibility, security, and
  release-readiness work**: automated `@axe-core/playwright` scanning
  (`web/tests/e2e/accessibility.spec.ts`) across all 29 production routes
  plus 12 representative interactive states (comparison results, the
  executive summary, filtered/empty catalogue states, explorer error
  states, contact validation/fallback states) with zero critical/serious
  violations and zero unresolved `incomplete` results; the Playwright
  matrix expanded from Chromium-only to Chromium/Firefox/WebKit (all three
  desktop projects run by default via `npm run test:e2e`), with `Web CI`'s
  browser-install step updated to match; a shared route inventory
  (`web/tests/e2e/support/routes.ts`, deriving its 17 check-detail routes
  from the real, Zod-validated check-catalogue data) backing a new
  build-output coverage-proof test
  (`web/tests/e2e/route-inventory.spec.ts`) and a new automated
  product-quality spec (`web/tests/e2e/product-quality.spec.ts`, 42 tests)
  covering HTTP status, heading/landmark structure, title/description
  uniqueness, internal-link validity, console/page errors, island counts,
  viewport overflow, the skip link, keyboard operability, visible focus,
  reduced-motion behaviour, and colour-independent severity text. This
  testing surfaced and fixed three genuine, previously-undetected defects
  in production code, each with a non-vacuous regression test: an
  `aria-prohibited-attr` violation (`aria-label` on a bare, role-less
  `<div>` in `ReportWorkspace.tsx`/`ExecutiveSummary.tsx`, fixed with
  `role="group"`); a Firefox-only CSP console error from Zod's internal
  `new Function`-based JIT-availability probe (fixed by a new
  `web/src/lib/zodJitless.ts` side-effect module calling Zod's own
  `config({ jitless: true })` — Zod's upstream code explicitly
  special-cases this "strict CSP" scenario — never by weakening any CSP);
  and a WebKit-only skip-link failure (WebKit does not honor the
  "sequential focus navigation starting point" heuristic Chromium/Firefox
  use, so the skip link had no effect at all for WebKit keyboard users;
  fixed with `tabindex="-1"` on `<main id="main-content">` in
  `BaseLayout.astro`, the standard technique for this exact pattern,
  confirmed to move focus correctly in all three engines — a narrower,
  WebKit-only residual nuance on the *following* Tab press is recorded
  honestly in `docs/reviews/v0.3.0-phase-3j-release-readiness.md` rather
  than hidden or force-fixed). **Automated** scripted pointer-free
  keyboard interaction (`page.keyboard.press()`, never a synthesized
  click) across all three engines is recorded and passes — this is
  automated test coverage, not a review performed by a person, and the
  review document names it accordingly rather than calling it "manual."
  A narrower-scope manual visual screenshot inspection (Chromium at
  320px for four pages; WebKit at 320px/1440px for one page — the exact
  scope is stated precisely in the review document, not overstated as
  full cross-browser coverage) also passes. **On 2026-08-24, the project
  owner personally completed all three of the genuine, person-performed
  manual-review gates**, using Google Chrome Version 151.0.7922.173: a
  genuine manual keyboard review (a person physically using only a
  keyboard) — Pass, no issues found; a genuine screen-reader spot-check
  (VoiceOver) — Pass, no issues found; and a literal 200%
  browser-zoom/text-resize review (a real zoom control, distinct from the
  320px-viewport proxy used for automated coverage) — Pass, no issues
  found; recorded in `docs/reviews/v0.3.0-phase-3j-release-readiness.md`
  as the owner's actual reported result, not expanded into fabricated
  per-item findings, and never claiming Safari/Firefox/NVDA/Windows/mobile
  assistive technology was reviewed (only Chrome was used). The overall
  accessibility/manual-review §Q gate is therefore now **Pass**, not
  "Partial" and no longer Blocked — this closes that one gate but does
  not, by itself, close Phase 3J (see below). `npm audit --audit-level=high` and
  `npm audit --omit=dev --audit-level=high` both report zero
  vulnerabilities; CSP, privacy/isolation, and dependency/secret reviews
  were performed directly against the real built `dist/` output (see the
  review document for exact evidence) with no finding requiring a fix
  beyond the three above. Local production-build performance was measured
  (total `dist` 700 KB; first-party JS 325 KB across 10 files, first-party
  CSS 13 KB across 2 files) and confirmed **not** a regression from
  Phase 3I (a byte-for-byte comparison against a `git stash`-restored
  Phase 3I build found only a 166-byte total increase from this phase's
  fixes) — no Lighthouse or other performance dependency was added, and no
  score beyond these local measurements is claimed. Web test coverage
  after Phase 3J: 604 Vitest tests (up from 603) across the same 38 files,
  and a Playwright suite that grew from 19 logical tests on Chromium only
  to 103 logical tests run on **all three** browsers (103/103 on each of
  Chromium, Firefox, and WebKit; 309/309 combined). Only one new
  dependency was added, exactly as scoped: `@axe-core/playwright`. A
  subsequent, focused correction pass fixed a genuine test-isolation gap
  (`product-quality.spec.ts`'s cross-route title/description aggregate
  test navigated to `/request-demo`/`/feedback` without the shared
  Turnstile fake installed, letting real requests reach
  `challenges.cloudflare.com`; fixed with a file-level `beforeEach` that
  installs the fake for every test in that file, confirmed non-vacuous:
  2 real external requests occurred before the fix, 0 after), corrected
  this document's own accessibility-status terminology (scripted
  Playwright keyboard interaction is automated evidence, never "manual
  keyboard review"; the visual-inspection scope is now stated exactly
  rather than overstated as full cross-browser coverage; since this
  milestone's approved statuses are Pass/Fail/Blocked/Not yet applicable
  and "Partial" is not one of them, at that stage the overall
  accessibility gate was correctly recorded as **Blocked**, because the
  required owner-operated evidence had not yet been supplied — see below
  for the subsequent 2026-08-24 owner review that changed this gate to
  **Pass**), corrected the owner-run screen-reader checklist to never
  ask for a real contact-form submission (`/api/contact` is not
  deployed or locally routed in Phase 3J), and added a
  `npm audit --audit-level=high` step to `Web CI` (after `npm ci`),
  enforcing the dependency-audit gate on every future web change rather
  than leaving it as local-only evidence. A subsequent, documentation-only
  pass then recorded the project owner's completed manual accessibility
  evidence (see above) across all four status-bearing documents, changing
  no production code, test, workflow, dependency, fixture, or route.
  **Phase 3J is closed**, on commit
  `7352a9aa17af9ba55f07cde1700ee1b72d5b65d0` (`feat(web): complete
  accessibility and release readiness`), for which independent review of
  the final package was completed and both `CI` (run `32797606973`) and
  `Web CI` (run `32797606927`) succeeded — confirmed from the actual
  logs: 1026 pytest, 604 Vitest across 38 files, a `Dependency audit`
  step (`found 0 vulnerabilities`), a 29-page production build, and
  103/103/103 Playwright per browser (309/309 combined), with zero
  occurrences of `challenges.cloudflare.com` anywhere in the `Web CI`
  log. **Phase 3K — Authorized Deployment and Release Preparation — is
  now being prepared**: a dependency-free Node ESM renderer
  (`web/deploy/render-wrangler-configs.mjs`) generates Wrangler
  configuration for two independent Cloudflare deployment units (a
  static-assets unit with no `main`, serving `web/dist` via Workers
  Static Assets as a custom domain; a contact-API unit routing only the
  exact `<hostname>/api/contact` path to the existing, unmodified
  `web/worker/contact.ts`), both with `workers_dev: false` and
  `preview_urls: false`, from strictly fail-closed-validated `DEPLOY_*`
  environment variables only — no Cloudflare token, account ID, or the
  Turnstile secret's value is ever read, required, or written.
  `.github/workflows/deploy-web.yml` (manual `workflow_dispatch` only,
  `contents: read`, exact confirmation-phrase and 40-hex-character-SHA
  validation in a credential-free job gating a `production`-environment
  deployment job, Wrangler pinned to `wrangler@4.102.0`) and
  `docs/deployment/web-production.md` document a future, separately
  authorized deployment procedure. **No commit has been created for this
  work yet; no deployment, Cloudflare resource, tag, release, or
  publication exists.** Phase 3K is not closed until this uncommitted
  package passes independent review and a subsequently approved commit
  passes `CI` and `Web CI`.
  **Nothing has been deployed, released, or published for v0.3.0.** v0.1.0
  and v0.2.0 remain unchanged, released product capabilities; do not start
  AKS/EKS-specific code, cloud cost intelligence, a database, SaaS
  multi-tenancy, authentication, billing or LLM integration until a
  milestone explicitly calls for it — v0.3.0 does not call for any of those.
- Do not introduce a database, web framework, cloud SDK (beyond the official
  Kubernetes client) or AI/LLM API until the relevant milestone requires it.
  (The v0.3.0 website's Astro/React/TypeScript stack is scoped to a separate
  `web/` directory once implementation begins — see the milestone document —
  and does not license adding a Python web framework, database, or AI/LLM API
  to the `cloudops_guard` package itself.)
- Explain important architectural changes before making them — don't silently restructure
  the collector/checks/engine/reports separation.

## Web application invariants (v0.3.0+)

These apply from Phase 3B onward, now that v0.3.0 implementation has begun;
see `docs/milestones/v0.3.0-interactive-web-demo.md` for full rationale.

- Report files a user selects are processed **locally in the browser only**
  and are **never uploaded** to any server.
- The website must never accept customer credentials, a kubeconfig file, or a
  GitLab token as input.
- Imported reports must never be persisted in `localStorage`, `sessionStorage`,
  `IndexedDB`, cookies, or a service-worker cache — closing or reloading the
  tab clears them.
- No analytics, session replay, or third-party scripts on demo/explorer
  routes.
- The contact/feedback endpoint(s) must remain architecturally isolated from
  report data — no code path may send an imported report or derived finding
  content to them.
- The existing Python `AuditReport`/`GitLabAuditReport` report contracts must
  not change to accommodate the web UI; all report normalization for the
  website happens in TypeScript, in the browser, against the JSON these
  models already produce.
- Production deployment is manual and requires explicit user authorization —
  never automatic on push or merge.

## Read-only invariant

- CloudOps Guard is a read-only auditing tool. It must never modify, create, patch or
  delete any resource in an audited system.
- Never retrieve or log Kubernetes Secret contents.
- Never retrieve or log ConfigMap contents.
- Never collect container environment variable values or application logs.
- Never print kubeconfig credentials, tokens or certificate material — including in
  exception messages.

## GitLab read-only and privacy invariants (v0.2.0+)

These apply once GitLab audit implementation begins; see
`docs/milestones/v0.2.0-gitlab-audit.md` §D for full rationale.

- Use read-only GitLab API operations only.
- Never call project, group, or instance CI/CD variables endpoints.
- Never collect or report job traces, logs, artifacts, credentials, or tokens.
- Never persist raw or merged CI YAML in reports.
- Never reproduce CI scripts or variable values in findings or error messages.
- If CI configuration must be processed to evaluate a check, process it only in
  memory and retain only the normalized, non-sensitive fields that check needs.
- Never log authentication headers.
- Sanitize remote API errors and untrusted response content before they reach a
  report or the terminal.
- A failure to access required information must not silently produce a partial clean
  report — fail the audit rather than under-report.
- The GitLab access token is read only from the `CLOUDOPS_GUARD_GITLAB_TOKEN`
  environment variable; it must never be accepted as a CLI option or read from a
  configuration file.
- An approved read-only endpoint may return unrelated sensitive fields that GitLab
  provides automatically. Such fields may exist only transiently during response
  parsing and must be discarded immediately at the normalization boundary. They must
  never be retained, logged, persisted, reported, cached, or included in errors.

## Testing

- Add tests for every check (existing and new). Tests must not require a live cluster —
  use the injectable Kubernetes client and representative `kubernetes.client` model
  objects (see `tests/fixtures/builders.py`), not hand-rolled dicts standing in for API
  responses.
- Tests should exercise real project code, not reimplement its logic to check against
  itself.

## Dependencies

- Avoid unnecessary dependencies. The dependency set (kubernetes, typer, pydantic,
  jinja2, pyyaml, pytest, ruff) is deliberately modest — justify any addition.

## Git

- Do not make commits or push changes unless explicitly requested by the user.
