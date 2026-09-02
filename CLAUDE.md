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
  authorized deployment procedure. Phase 3K went through two further,
  independently-reviewed correction passes after initial implementation
  (nine findings, then four more — closing a transactional-write gap in
  the config renderer, making the workflow's temp-directory cleanup
  traversal-safe via `realpath` canonicalization rather than a textual
  prefix match, replacing an ambiguous bootstrap-Wrangler-command
  recommendation with an unambiguous dashboard-driven procedure, and
  correcting report accounting) before being approved. **Phase 3K is
  closed**, on commit `87376002553b24f21a0331c708986222a005a62d`
  (`feat(web): prepare authorized production deployment`) on `main`,
  `HEAD == origin/main`, for which both `CI` (run `33029176307`,
  succeeded in 17s) and `Web CI` (run `33029176229`, succeeded in 6m35s —
  full type-check/lint/unit-test/production-build/three-browser-
  Playwright sequence) succeeded. **No deployment, Cloudflare resource,
  tag, release, or publication exists** — this commit adds deployment
  *configuration* only; `deploy-web.yml` has never been dispatched.
  **v0.3.0 is now feature-complete (Phases 3A through 3K all committed
  and pushed), but nothing has been deployed, released, or published for
  it.** v0.1.0 and v0.2.0 remain unchanged, released product capabilities;
  do not start AKS/EKS-specific code, cloud cost intelligence, a database,
  SaaS multi-tenancy, authentication, billing or LLM integration for the
  `web/` website beyond what v0.4.0 (below) explicitly calls for.
- **The current approved milestone is v0.4.0: Versioned Ingestion API and
  Customer-Controlled Uploader** — see
  `docs/milestones/v0.4.0-ingestion-api.md` for its full objective, scope
  and non-goals, privacy boundary, versioning contract, API contract,
  authentication and tenant-isolation design, threat model, proposed
  architecture (including storage interfaces and a request/data-flow
  diagram), and phased plan (Phases 4B–4G). **Phase 4A — this milestone
  document itself, this `CLAUDE.md` update, the roadmap update, and the
  documentation/contract test suite proving the two are not allowed to
  drift apart — is documentation and contract design only.** No API code,
  storage provisioning, authentication code, uploader code, or deployment
  infrastructure exists yet, and none of it is authorized to exist until a
  later phase (4B onward) is separately approved. The ingestion API this
  document designs is explicitly a **separate service and separate
  security boundary from the `web/` website** — it is not, and must never
  become, a new route on the existing Cloudflare Worker or Astro site, and
  the v0.3.0 website's own "no report-ingestion endpoint" invariant (see
  "Web application invariants" below) remains true of the website itself
  regardless of anything v0.4.0 adds elsewhere. v0.4.0 does not call for
  AKS/EKS-specific code, expanded cloud cost analysis, or a persistent
  multi-tenant dashboard — see the milestone document's non-goals for the
  complete list. **Phase 4B has implemented the storage and token
  interfaces Phase 4A's §H designs, together with deterministic local,
  in-memory reference implementations**, under a new
  `src/cloudops_guard/ingestion/` package: `IngestionRecord`/
  `Tombstone`/`TokenRecord` domain types; the `MetadataStore`,
  `ReportBlobStore`, `TokenStore`, and `AttemptLimiter` interfaces as
  Python ABCs; and `InMemoryMetadataStore`/`InMemoryReportBlobStore`/
  `InMemoryTokenStore`/`InMemoryAttemptLimiter` reference
  implementations — including `InMemoryMetadataStore`'s single-lock
  atomic `create_or_get_received` (never a separate lookup-then-write),
  the fixed non-sliding 24-hour idempotency-key window, lazy
  (non-background-swept) tombstone expiry, and the full
  received→retired→deleted lifecycle; and index-corruption prevention in
  `create_or_get_received` (an `IngestionIdConflict` is raised, before
  any store mutation, if a `new_ingestion_id` collides with a different
  tenant-scoped identity — live, retired, or still-tombstoned). `TokenStore`
  is the complete, approved three-method interface (`lookup`,
  `verify_secret`, `mark_revoked`); `InMemoryTokenStore.verify_secret`
  delegates entirely to an injected `SecretVerifier` `Protocol` callable
  — this class performs no hashing or comparison itself, so real
  Argon2id-backed verification remains Phase 4C work, and a test injects
  only a deterministic fake. `InMemoryMetadataStore.mark_retired`/
  `mark_purged` construct and validate the complete candidate record (and,
  for purge, its `Tombstone`) via the normal, validating constructor
  before mutating any store state — never `model_copy(update=...)`, which
  does not validate. **None of this is production storage, an HTTP API,
  authentication, or a real credential**: no database, object store,
  secret manager, HTTP framework, cloud SDK, or network dependency was
  added; no ingestion API endpoint, uploader, or deployment exists;
  nothing is durable beyond process memory. No new dependency was added.
  This work is **uncommitted**, pending independent review; it does not
  authorize Phase 4C (an HTTP API), production storage, credentials,
  deployment, tagging, or a release. **Phase 4B has since been committed
  and pushed** (commit `363cdf179945d7c93b78a25edb7b7fc416ac8da8`, `feat:
  add ingestion storage reference layer`; both `CI` and `Web CI` passed).
  **Phase 4C — authentication implementation — has since implemented the
  `<lookup_id>.<secret>` token format** (`token_format.py`), **secure
  token generation and Argon2id hashing** (`token_issuance.py`,
  `argon2_backend.py`, adding the one approved new dependency,
  `argon2-cffi`, confined to `argon2_backend.py`), **the three-layer
  abuse-protection scope keys and a credential-free capabilities check**
  (`abuse_protection.py`), and **a framework-independent authentication
  coordinator and scope-authorization function**
  (`authenticator.py`: `AuthenticationCoordinator`, `AuthenticatedPrincipal`,
  `authorize`) — implementing `TokenStore`'s complete, approved
  `lookup`/`verify_secret`/`mark_revoked` interface end to end via
  `Argon2SecretVerifier`, and the exact Layer 1 (pre-Argon2id,
  per-`lookup_id`) / Layer 2 (per-source, covering malformed tokens and
  unknown `lookup_id`s and the future capabilities endpoint) / Layer 3
  (per-authenticated-token, checked only after success, `is_blocked`-only
  — this coordinator never calls `AttemptLimiter.record_failure` for
  Layer 3, since an ordinary successful request is not a "failure")
  ordering §F specifies. A documented manual, out-of-band provisioning
  procedure (`docs/manual-token-provisioning.md`) covers `provision_token`;
  no production insertion mechanism, self-service UI, or real customer
  token exists. **None of this is an HTTP API, a network-reachable
  endpoint, production storage, or a real credential**: no HTTP
  framework, route, handler, or server; no database, object store, or
  cloud SDK; `provision_token` never inserts into any `TokenStore`
  (storage insertion for a real deployment remains an explicit later
  production-store responsibility). **A subsequent, focused Phase 4C
  security correction pass** fixed three independently
  reproduced contract violations found in the initial Phase 4C
  implementation: (1) `ParsedToken`/`ProvisionedToken` were
  `dataclasses.dataclass`es whose `repr`/`str` were redacted but whose
  fields were still fully recoverable via `dataclasses.asdict()` —
  replaced with plain, `__slots__`-only, non-dataclass value objects
  (`_secure_value.ImmutableRedactedValue`) that are immutable, have no
  instance `__dict__`, are not dataclasses, are not JSON-serializable,
  and explicitly refuse pickling (`__reduce__`/`__getstate__`), confirmed
  by a mutation test that the plaintext secret genuinely appears in a raw
  `pickle.dumps()` byte stream without that refusal; (2) the public
  `provision_token(..., hasher=...)` parameter let any caller substitute
  an insecure hasher (e.g. one returning recoverable plaintext), which
  would then be stored verbatim in `TokenRecord.secret_hash` — the
  parameter was removed outright; `provision_token` now always uses the
  real `Argon2SecretVerifier` and additionally validates its own output
  as genuine Argon2id (`argon2_backend.require_argon2id_hash`) before
  constructing a `TokenRecord`, failing closed if that invariant is
  violated; tests needing a fast stand-in now construct `TokenRecord`
  directly with an opaque, secret-free placeholder hash instead; (3)
  `Argon2SecretVerifier` delegated to `argon2-cffi`'s `PasswordHasher.verify()`
  without checking the encoded hash's algorithm, so a well-formed Argon2i
  or Argon2d hash verified successfully against its correct secret,
  violating the Argon2id-only contract — every hash `Argon2SecretVerifier`
  is asked to verify or willing to produce is now first parsed with the
  library's own `argon2.extract_parameters` and checked to be exactly
  `Type.ID`; `__init__` additionally refuses to wrap a non-Argon2id-
  configured `PasswordHasher`, and `hash()` independently re-validates
  its own output as defense in depth. All three were confirmed via a
  reproduce-before-fix probe and a deliberate source mutation per
  guarantee, each caught by its intended test before being reverted.
  **Phase 4C (including this correction pass) has since been committed
  and pushed** (commit `90e2a8848b2df6fd3befeb83c737b06166866bc1`, `feat:
  add ingestion authentication layer`; both `CI` and `Web CI` passed).
  **Phase 4D — ingestion API implementation — has since implemented the
  four `/api/v1` endpoints** (`GET /api/v1/capabilities`,
  `POST /api/v1/reports`, `GET /api/v1/reports/{ingestion_id}`,
  `DELETE /api/v1/reports/{ingestion_id}`) under a new
  `src/cloudops_guard/ingestion_api/` package, built on Phase 4B's storage
  interfaces and Phase 4C's authentication coordinator exactly as designed
  — a hand-written raw-ASGI dispatcher (`app.py`, deliberately never using
  Starlette's `Router`/`Route` classes, whose defaults — trailing-slash
  redirects, automatic `HEAD`/`OPTIONS`, framework-default error bodies —
  this contract forbids), strict JSON decoding (`strict_json.py`:
  duplicate-object-key rejection at every nesting level, `NaN`/`Infinity`
  rejection, lone-surrogate rejection), bounded incremental body reading
  mirroring `web/worker/readBoundedBody.ts` (`bounded_body.py`), a
  closed-envelope validator (`envelope.py`), an RFC 8785 fingerprint
  function (`fingerprint.py`, the one new dependency `rfc8785` confined to
  it), report validation reusing the existing `AuditReport`/
  `GitLabAuditReport` Pydantic models plus a new server-side
  `MAX_FINDINGS_PER_REPORT` ceiling and summary-recomputation check
  (`report_validation.py`), a cross-store failure-recovery coordinator
  (`coordinator.py`), explicitly-invoked retention-sweep/purge functions
  (`lifecycle.py`), and allowlist-only structured logging
  (`logging_utils.py`). Applied three pre-authorized contract corrections
  during implementation: added a fresh `request_id` field to the
  capabilities success response (the milestone document's own example was
  corrected to match); added `ReportBlobStore.put_if_absent` (§H) as the
  safe "reserve a brand-new key" primitive `POST /api/v1/reports`
  exclusively uses, since plain `put` cannot safely back that path without
  risking a silent overwrite under a generated-ID collision or a
  concurrent duplicate request — `put` itself is preserved, unused by
  Phase 4D, since Phase 4B's own already-approved reference implementation
  and test suite already treat it as part of the interface's contract; and
  added a `RequestRateLimiter` interface, deliberately separate from
  `AttemptLimiter`, for ordinary (non-failure) request-volume throttling —
  the unauthenticated capabilities endpoint's own request budget, and
  Layer 3's per-authenticated-token budget — since `AttemptLimiter.
  record_failure` means "a failure happened" and must never be called to
  count an ordinary successful request (Phase 4C's own Layer 3 check could
  never actually trigger for exactly this reason). The cross-store
  failure-recovery design never overwrites another request's blob, deletes
  only a request's own unused reservation on a definite pre-commit
  rejection (an idempotency-key conflict or a metadata-level ID conflict,
  both raised before any store mutation) or on losing the atomic dedup
  race, and deliberately leaves a reserved blob alone on any other,
  ambiguous `MetadataStore` exception rather than risk orphaning a record
  that might have already committed. A real-loopback-server concurrency
  suite (`uvicorn` on `127.0.0.1`, an ephemeral port, genuine concurrent
  HTTP requests via `httpx.AsyncClient`) proved, under real network I/O
  rather than only in-process calls, that concurrent identical `POST`s
  (with and without a shared `idempotency_key`) produce exactly one `201`
  and no duplicate records/blobs, that a concurrent generated-ID collision
  is fully absorbed by retry, and that the capabilities and per-token
  request-rate ceilings are never exceeded under real concurrent load —
  each scenario run 20 times with zero flakes observed. That same
  real-server testing surfaced and fixed one genuine defect before this
  work was reported complete: the abuse-protection source identifier
  originally included the client's own ephemeral TCP port
  (`f"{host}:{port}"`), which a real client's connection pool varies per
  connection — trivially defeating Layer 2/2.5 source-scoped throttling
  for anyone willing to open more than one connection; fixed to use the
  peer host alone. Mutation-verification (deliberate source mutation,
  confirmed test failure, then restore) was performed for all eight of
  the task's named highest-risk guarantees: bounded streaming, duplicate-
  key rejection, RFC 8785 fingerprint composition, tenant-isolation/
  identical-404, concurrent dedup, blob-collision/no-overwrite, atomic
  request-rate accounting, and purge ordering. **None of this is a
  production service**: `create_app` performs no I/O (confirmed by a
  socket/thread-spy import probe); no HTTP endpoint here is
  network-reachable or customer-reachable outside a caller's own
  explicitly-started local/loopback test server; no real customer token,
  production database, object store, secret manager, or deployment
  infrastructure exists. This work is **uncommitted**, pending independent
  review; it does not authorize Phase 4E (the CLI uploader), production
  storage, credentials, deployment, tagging, or a release. **A subsequent,
  narrow Phase 4D correction pass** (also uncommitted) fixed six
  independently-reproduced issues found in the initial Phase 4D
  implementation: (1) `lifecycle.purge_retired_ingestion` deleted a
  `received` record's live blob before `mark_purged` ever validated its
  status, so calling it too early destroyed data and only then raised
  `ValueError` — fixed by adding `MetadataStore.get_any_status` (a new,
  tenant-scoped, status-agnostic lookup distinct from the existing,
  deliberately RECEIVED-only `get`) and checking eligibility before any
  blob deletion, relying on the lifecycle's own monotonic
  received→retired→deleted ordering for safety under concurrent
  retirement/purge; (2) `report_schema_version: 1.0` (a JSON number with
  an integer value, which the approved contract accepts) was wrongly
  rejected, while an unsafe integer or a `1e400`-style exponential
  overflow to `inf` buried anywhere in a report — including an
  ignored/unvalidated extra field — reached RFC 8785 canonicalization
  uncaught and became a `500`; fixed with a new recursive numeric-domain
  validator in `strict_json.py` (rejecting non-finite floats and
  integers outside `+-(2**53-1)` anywhere in the decoded document,
  before envelope parsing) plus a defensive `rfc8785.CanonicalizationError`
  catch in `fingerprint.py`, and `envelope.py` now accepts an
  integer-valued float for `report_schema_version` without coercing it;
  (3) every header lookup used `Headers.get()`, which silently resolves
  a repeated header to only its first occurrence — fixed by reading the
  raw ASGI header list directly and requiring exactly one
  `Authorization`/`Content-Type` header, rejecting any `Content-Length`
  duplicate (even an agreeing one) before authentication is even
  attempted, and rejecting any `Content-Encoding` occurrence; (4) route
  dispatch collapsed empty path segments, so `/api/v1/capabilities/`,
  `/api//v1/capabilities`, and similar double/trailing-slash variants
  silently aliased the real routes — fixed by matching only the four
  exact declared path shapes, with no segment-collapsing, so every alias
  is now a `404`, never a redirect; (5) every handler called blocking
  Argon2id authentication, report validation, RFC 8785 fingerprinting,
  and synchronous store operations directly on the event loop, so one
  request's slow work fully serialized every other concurrent request
  behind it — fixed by moving each handler's blocking portion onto
  AnyIO's bounded worker-thread pool (`anyio.to_thread.run_sync`, a new
  explicit dependency though already present transitively via
  `starlette`), proven by a real-loopback test whose instrumented
  `MetadataStore` wrapper uses a `threading.Barrier` to force two
  concurrent requests to be simultaneously inside
  `create_or_get_received`, and by a responsiveness test showing a
  concurrent capabilities call completes without waiting on a
  deliberately slow secret verifier; (6) added
  `tests/fixtures/ingestion_fingerprint_fixtures_v1.json`, a shared,
  versioned RFC 8785 fingerprint fixture set (representative
  multi-finding Kubernetes/GitLab reports, a key-order equivalence pair,
  Unicode/RTL/combining-character coverage, and an int/float numeric
  canonicalization equivalence pair — every case independently confirmed
  genuinely accepted, and every `expected_fingerprint` computed once,
  offline, never at test-collection time by the implementation under
  test), consumable unchanged by Phase 4E; also re-reviewed
  `coordinator.py`'s cross-store recovery and confirmed (with a new,
  precise `ValueError`-shaped test) that only the two documented
  `MetadataStore` exception types are treated as a safe, definite
  pre-commit cleanup signal, while every other exception — including an
  internal precondition `ValueError` this coordinator's own bug would
  have to trigger — correctly stays in the conservative
  "leave the blob alone" bucket. Every issue was independently reproduced
  before being fixed; every fix has a regression test, and several
  (the blob-purge ordering, the numeric-domain rejection, the
  singleton-header enforcement, the exact-route matching, and the
  thread-offload concurrency guarantee) have a deliberate
  mutation-verification pass confirming the added test actually detects
  the reintroduced defect. No production deployment, credential, or
  infrastructure was touched by this correction pass. **A second, final,
  narrow Phase 4D correction pass** (also uncommitted) fixed four further
  independently-reproduced issues: (1) the first pass's own
  `get_any_status`-based purge-eligibility check was still a check-*then*-
  act operation — an old `deleted` record's tombstone could expire and its
  `(tenant_id, ingestion_id)` key be reused by a genuinely new `received`
  identity before `purge_retired_ingestion`'s own unconditional
  `ReportBlobStore.delete` call ran, silently destroying that new
  identity's live blob, and two concurrent purgers could race the same
  way — fixed by replacing that check entirely with a monotonic,
  purely-internal per-key generation counter and an **exclusive**
  purge-claim mechanism (`PurgeClaim`, `MetadataStore.begin_purge`/
  `release_purge_claim`/`finalize_purge`, never customer-visible): at
  most one caller is ever granted a claim for a given generation, so a
  second, independently-delayed caller's own `ReportBlobStore.delete`
  call structurally never happens at all — verified, independently of the
  eventual test suite, via direct reproduction scripts for both the exact
  reused-identity sequence and the two-concurrent-purgers race, plus 6
  new deterministic barrier/spy-based tests; (2) the first pass's own
  thread-offload refactor (item 5 above) had moved `read_bounded_body`
  *before* authentication for `POST /api/v1/reports`, so a missing,
  malformed, duplicated, or invalid credential — or a rate-limited or
  insufficient-scope one — caused `receive()` to be called before the
  request was ever rejected, contradicting `app.py`'s own comment
  claiming the opposite — fixed by splitting `_ingest_report_blocking`
  into a separate `_authenticate_and_authorize_for_write` offload that
  now runs, and is awaited to completion, strictly before
  `read_bounded_body`, with the resulting `AuthenticatedPrincipal` passed
  into the second offload rather than re-authenticated; a new
  `bounded_body.validate_declared_content_length` factors out the cheap,
  read-free declared-`Content-Length` check so it too runs before
  authentication; proven by 7 new ASGI receive-spy tests (one control,
  six failure modes: missing/malformed/duplicated/invalid/rate-limited/
  insufficient-scope, each with a spy `receive` that raises
  `AssertionError` if ever called) plus a mutation-verification pass
  reverting the ordering and confirming all six fail; (3)
  `coordinator.create_ingestion` called `config.clock()` and constructed
  the `IngestionRecord` candidate *after* a successful `put_if_absent`
  reservation but *outside* any `try` guarding it, so either raising left
  an orphaned, never-cleaned-up blob reservation — since this is always
  strictly before `create_or_get_received` is ever called, it is never
  ambiguous the way that call's own exceptions are — fixed by wrapping
  both operations in their own `try`/`except Exception` that deletes the
  owned reservation and re-raises, deliberately kept separate from (and
  proven, by a new regression test, not to widen) the existing
  `IngestionIdConflict`/`IdempotencyKeyConflict`-only handling for
  `create_or_get_received` itself — correcting this same document's
  earlier, first-correction-pass claim that this guarantee was already
  fully satisfied, which was incorrect; (4) a syntactically valid
  document with roughly 1,000 nested arrays or objects let a bare
  `RecursionError` escape `strict_json.strict_decode_json` entirely (an
  unsanitized `500`, and independently the same recursive helpers this
  contract's own strict-decode validation used could themselves exhaust
  the stack) — fixed by replacing the two separate recursive helpers
  (`_reject_lone_surrogates`/`_reject_unsafe_numbers`) with one
  **iterative** (explicit-stack, never Python call recursion) combined
  walk enforcing a new, conservative, documented `_MAX_NESTING_DEPTH`
  ceiling (64) before either of its other two per-node checks, `json.loads`
  itself now also mapping any `RecursionError` it might independently
  raise to the same sanitized `400 invalid_request`, and
  `fingerprint.compute_report_fingerprint` — callable directly, bypassing
  `strict_decode_json` entirely (e.g. a future uploader computing this
  same fingerprint locally) — independently catching `RecursionError`
  from `rfc8785.dumps` as a defensive backstop alongside its existing
  `CanonicalizationError` catch. Every one of the four issues was
  independently reproduced before any code changed; every fix has
  dedicated regression tests (27 new, across
  `test_ingestion_api_lifecycle.py`, `test_ingestion_api_reports_post.py`,
  `test_ingestion_api_coordinator.py`, `test_ingestion_api_strict_json.py`,
  and `test_ingestion_api_fingerprint_conformance.py`) and an explicit
  mutation-verification pass (temporarily reverting the fix, confirming
  the new test(s) fail, then restoring and reconfirming they pass) for
  all four. The full pytest suite grew from 1996 to 2023 (all passing);
  the real-loopback concurrency suites
  (`test_ingestion_api_concurrency.py`,
  `test_ingestion_api_thread_offload_concurrency.py`,
  `test_ingestion_authenticator_concurrency.py`) were run repeatedly,
  passing 176/176 every time. No new dependency was added; no production
  deployment, credential, or infrastructure was touched. This second
  correction pass is also **uncommitted**, pending independent review; it
  does not authorize Phase 4E, production storage, credentials,
  deployment, tagging, or a release. **A third, final Phase 4D purge-claim
  hardening pass** (also uncommitted) fixed three further
  independently-reproduced defects in the second pass's own claim
  protocol, and corrects that second pass's own description of its
  generation-scoped purge-claim mechanism as having "closed" the race —
  it closed only the two races that pass itself reproduced, not the
  three below: (1) `PurgeClaim` carried only `(tenant_id, ingestion_id,
  generation)`, and `_active_purge_claims` stored only the generation —
  reproduced: claim A acquired and released; claim B acquired for the
  same, unchanged generation; releasing A *again* incorrectly deleted
  B's active entry (an ABA problem — a generation-only comparison cannot
  distinguish two separate acquisitions against an unchanged generation),
  and `finalize_purge(A)` succeeded even after A had been released —
  fixed by adding a globally-unique `claim_id` to `PurgeClaim` and to the
  internal active-claim record (`_ActivePurgeClaim`), so `release_purge_claim`/
  `finalize_purge` now compare **both** `generation` and `claim_id`
  against whatever is currently active, never generation alone; (2)
  `mark_purged` — preserved, unused by `lifecycle.purge_retired_ingestion`,
  but still part of the same `MetadataStore` — never consulted
  `_active_purge_claims` at all, so it could bypass the claim protocol
  entirely: reproduced by retiring a record, acquiring a claim via
  `begin_purge`, then calling `mark_purged` directly, which succeeded
  and created a tombstone while the claim was still active and its
  holder had not yet physically deleted anything — once that tombstone
  would later expire and the ID be reused, the claim holder's own
  still-pending blob deletion would target the new identity's live
  blob — fixed by having `mark_purged` raise, under the same lock, when
  an exact active claim exists for the record; (3) `purge_retired_ingestion`
  acquired its claim before calling `config.clock()`, and never wrapped
  its own `finalize_purge` call, so a clock failure, a naive timestamp,
  a `deleted_at` preceding `retired_at`, or a finalize failure could each
  leak a claim permanently — fixed by adopting the stronger protocol the
  task itself invited: `begin_purge` now takes the proposed deletion
  timestamp `at` directly and atomically validates it *and* constructs
  the complete eventual `deleted` candidate record and tombstone before
  ever granting a claim (so a validation failure can never leave one
  dangling — there is nothing yet to release), `finalize_purge` no
  longer takes `at` at all (it only commits the already-validated
  candidate `begin_purge` captured), and every remaining step from claim
  acquisition onward in `purge_retired_ingestion` is wrapped so that any
  exception releases exactly that call's own claim before re-raising.
  Every issue was independently reproduced before being fixed (direct
  store-level reproduction scripts and test assertions for all three,
  matching each item's own before-fix description above); 18 new
  regression tests were added across a new `tests/
  test_ingestion_metadata_store_purge_claims.py` (13 tests, direct
  `InMemoryMetadataStore`-level coverage of the unique-acquisition and
  `mark_purged`-coordination guarantees, including the exact named
  A-release/B-acquire/A-release-again and A-release/A-finalize
  scenarios) and `tests/test_ingestion_api_lifecycle.py` (5 new tests, a
  `TestPurgeClaimExceptionSafety` class covering `config.clock()`
  raising, a naive timestamp, `deleted_at` preceding `retired_at`, a
  finalize failure releasing its claim for retry, and a single
  end-to-end test driving all five of this pass's own failure modes
  against the same record in sequence before proving a completely
  ordinary purge and a subsequent tombstone-expiry-and-reuse cycle are
  both unaffected). Mutation-verified all four of the task's own named
  scenarios: reverting `finalize_purge`'s comparison to the pre-fix,
  generation-only form (also faithfully reproducing "a released claim
  can still finalize," since the old form never checked
  `_active_purge_claims` at all) failed 7 tests across both new files;
  disabling `mark_purged`'s active-claim guard failed 2 tests; omitting
  the exception-path claim release around `purge_retired_ingestion`'s
  own `finalize_purge` call failed 2 tests — all four mutations were
  reverted and the full suite reconfirmed green afterward. The full
  pytest suite grew from 2023 to 2041 (all passing); the real-loopback
  concurrency suites were re-run and remained green. No new dependency
  was added; no production deployment, credential, or infrastructure was
  touched. This third correction pass is also **uncommitted**, pending
  independent review; it does not authorize Phase 4E, production
  storage, credentials, deployment, tagging, or a release.
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

- Report files a user selects **in the website's demo/explorer routes** are
  processed **locally in the browser only** and are **never uploaded** to
  any server. This is separate from, and unaffected by, the v0.4.0+
  CLI-only uploader below — the website itself never uploads a report, and
  nothing in `web/` may ever call the ingestion API.
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

## Ingestion API and uploader invariants (v0.4.0+)

These apply once v0.4.0 ingestion-API/uploader implementation begins (Phase
4B onward); see `docs/milestones/v0.4.0-ingestion-api.md` for full
rationale. **Phase 4A was documentation and contract design only. Phase 4B
(committed; see above) has implemented local, in-memory reference
storage/token interfaces (`src/cloudops_guard/ingestion/`) satisfying the
storage-layer mechanics some of the invariants below describe — atomic
create-or-return-existing, the idempotency window, and the retirement/
purge/tombstone lifecycle are now real, tested Python code. Phase 4C
(uncommitted; see above) has since implemented real Argon2id hashing and
verification, the `lookup_id`/`secret` token structure, a framework-
independent authentication coordinator enforcing the three-layer
abuse-protection ordering below (Argon2id is ever invoked only after
Layers 1 and 2 both pass), and per-scope authorization — still no HTTP
API, no uploader, no production credential store, and no deployment exist
yet — everything below that describes the API surface, the uploader CLI,
or a real customer credential remains unimplemented. No real credential
has been issued or provisioned.**

- The ingestion API is a separate service and separate security boundary
  from the public website — it must never be folded into `web/`'s Worker,
  routes, or Astro build, and the website's own "never uploads a report"
  invariant above must remain true regardless of the ingestion API's
  existence.
- Uploading a report is never automatic and never browser-triggered. It
  requires a separate, explicit CLI command (`cloudops-guard upload`) with
  explicit interactive confirmation, or a documented `--yes` flag for
  non-interactive/CI use — selecting or opening a report file, in the CLI
  or the website, must never itself cause network activity.
- **No capabilities call, identity/authentication check, or upload
  request may occur before an exact typed `UPLOAD` confirmation (or
  `--yes`, which explicitly stands in for it)** — not even the
  unauthenticated capabilities endpoint. `--dry-run` must exist, must
  require no credential to be configured, and must perform full local
  validation and print what would be sent — including a locally-computed
  `report_fingerprint` (see below) — without ever making a network
  request. The pre-upload summary must never print a server-derived
  tenant name (the uploader has not contacted the server yet); an
  optional local alias may be shown only if explicitly labeled as a
  non-authoritative local label, never as a verified identity.
- A non-interactive invocation without `--yes` or `--dry-run` must fail
  closed with a clear error — never silently proceed and never hang.
- Customer/tenant identity must be derived only from the authenticated
  bearer token's server-side lookup — never trusted from a client-supplied
  field in the request body, query string, or header naming a tenant. The
  request envelope is a closed set of fields; an unknown top-level field
  (including one that names an identity) is rejected outright, never
  silently ignored.
- Bearer tokens are structured as a non-secret, indexed `lookup_id` plus an
  independently-random `secret` — never a single opaque value hashed as
  the lookup key (a salted hash cannot itself be a deterministic lookup
  key). Only `secret` is sensitive; it is never stored except as an
  Argon2id hash, never in a recoverable form, and never embedded in a URL,
  report body, log line, or the uploader's command line/shell history
  (read only from `CLOUDOPS_GUARD_INGESTION_TOKEN` or a local credential
  file, mirroring the existing `CLOUDOPS_GUARD_GITLAB_TOKEN`
  env-var-only precedent). Customer-scoped, revocable effective on the
  next request, minimum necessary scope per endpoint.
- The report schema version and the API's major version (`/api/v1`) are
  independent axes — never conflate a schema-version change with an
  API-version change or vice versa.
- Deterministic, machine-readable error codes only; never expose an
  internal exception, stack trace, or infrastructure detail in an API
  response.
- Two independent size ceilings, never conflated: the maximum size of the
  `report` field's own value (10 MiB, matching the existing
  `MAX_REPORT_FILE_BYTES` used for local report import) and the maximum
  size of the entire HTTP request body (the report ceiling plus a small,
  fixed envelope-overhead allowance) — both enforced server-side, the
  request-body ceiling checked first against both the declared
  `Content-Length` and the actual bytes read, before attempting to parse
  or store anything.
- Before schema validation or fingerprinting, the JSON decoder must reject
  a duplicate object-member name (at every object level, not the envelope
  alone), a bare `NaN`/`Infinity`/`-Infinity` literal, malformed/invalid
  Unicode, and any envelope field of the wrong type — in particular,
  `report_schema_version` must be a JSON integer; a numeric string (e.g.
  `"1"`) is invalid, never coerced.
- Every ingestion is identified by a single deterministic
  `report_fingerprint` (RFC 8785 canonicalization of `{platform,
  report_schema_version, report}`, then SHA-256, computed only after the
  strict-decode and schema checks above both succeed) — computable
  identically, with no network round-trip, by the uploader and the server,
  and used for idempotency. Never hash `report` content alone in isolation
  from `platform`/`report_schema_version`.
- Ingestion deduplication (by `report_fingerprint`, and by
  `idempotency_key` when supplied) must be a single atomic
  create-or-return-existing storage operation — never a separate
  lookup-then-write pair, which cannot guarantee correctness under
  concurrent requests for the same content. At most one `received` record
  may exist per `(tenant_id, report_fingerprint)`, enforced at the storage
  layer, even when two identical requests race each other.
- A report's end-of-life is never claimed to be instantaneous, and is
  never attributed to the wrong trigger: it is **retired** either by an
  explicit customer `DELETE` request or, automatically, when its
  retention period elapses with no such request — both produce the same
  `retired` status and a `retired_at` timestamp (never a field name
  implying a customer action that did not happen), distinguished only by
  a `reason` (`customer_requested`/`retention_expired`). Physical,
  irreversible purge of the underlying bytes (and, later, backups)
  completes asynchronously, within a bounded window, after retirement —
  the API response must never claim physical deletion before it has
  actually occurred. A bounded post-purge tombstone keeps repeated
  `DELETE` calls idempotent (and never overwrites an already-recorded
  `reason`); once that tombstone itself expires, the ID becomes
  indistinguishable from one that never existed.
- `GET`/`DELETE` on an unknown `ingestion_id`, one that belongs to a
  different tenant, one that has been retired (for either reason), and
  one whose tombstone has since expired must all return the identical
  response — never a distinguishable error that would let a caller
  enumerate or probe another tenant's ingestion IDs or a past retirement.
- Every error response uses one fixed, minimal envelope
  (`{ok, error, request_id}`) with no exception for any error code —
  an unsupported-schema-version error does not carry the set of supported
  values; a client discovers those from `GET /api/v1/capabilities`
  instead. Every endpoint's applicable 401/403/404/405/429/500 responses
  are enumerated per endpoint, and any HTTP method other than the one(s)
  an endpoint defines is `405 method_not_allowed` with an `Allow` header.
- Authentication has three independent, layered abuse-protection tiers:
  an inexpensive check *before* Argon2id is ever invoked, scoped to a
  single `lookup_id` (so guessing secrets against one known-valid ID
  cannot force unbounded expensive hashing); a broader, source-scoped
  limit covering an unknown `lookup_id` and the unauthenticated
  capabilities endpoint (neither has a token to scope a limiter against);
  and the existing authenticated per-token limit, unchanged, applying only
  after both of the above and Argon2id verification succeed. No vendor or
  numeric production threshold is selected until an implementation phase.
- Ingestion-service logs must never contain report content, a bearer token
  value, or any request-body field beyond the fixed, documented allowlist
  (`docs/milestones/v0.4.0-ingestion-api.md` §C) — mirroring this
  project's existing "never log authentication headers" / "never log
  report fields" discipline below.
- Storage requirements once a concrete implementation exists: TLS in
  transit for every hop; encryption at rest for metadata, reports, and
  backups alike; least-privilege service credentials per store; bounded
  backup deletion (a backup containing purged data must itself be rotated
  out within a bounded window, never retained indefinitely); and an
  explicit, recorded region/data-residency decision as a mandatory
  precondition of any production deployment (Phase 4G) — never a default a
  cloud provider's SDK happened to pick.

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
