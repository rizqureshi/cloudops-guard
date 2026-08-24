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
  validation-only (it never deploys or publishes). Local Vitest coverage
  is 381 tests (up from Phase 3F's 319). **Phase 3G is implemented but not
  yet closed**: per this milestone's own process, a phase closes only
  after independent ZIP review and a successful `CI`/`Web CI` run against
  the approved commit, and no commit has been created for this work yet.
  The check catalogue and other product pages, the contact/feedback
  endpoint(s), any Worker endpoint, and any Cloudflare/deployment
  configuration remain **not yet implemented**; full automated
  accessibility (`axe`) scanning and the Firefox/WebKit legs of the
  Playwright matrix remain Phase 3J.
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
