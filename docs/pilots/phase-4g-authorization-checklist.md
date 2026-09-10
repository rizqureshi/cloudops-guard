# Phase 4G authorization checklist

**None of the following may occur without separate, explicit,
contemporaneous human approval — implementing, reviewing, or
documenting a procedure for any of these (Phase 4F, and everything that
preceded it) is never, by itself, that approval.** This mirrors
`docs/deployment/web-production.md`'s own repeated statement of the
same rule for the v0.3.0 website's own Phase 3K deployment tooling, and
`docs/milestones/v0.4.0-ingestion-api.md` §I's own statement that
"implementing and reviewing this phase's deployment tooling is **not
itself** that authorization."

## Preconditions to authorizing Phase 4G at all

**The three items below are not Phase 4G activities. They are
prerequisites that must already be satisfied before Phase 4G may be
authorized to begin — Phase 4G executes a decision already made, it
does not make the decision itself.** Every item in "Phase 4G execution
checklist" below presupposes all three of these are already done.

- [x] **Provider and region/data-residency decision** — **recorded
  2026-09-09**: Microsoft Azure, Canada Central (`canadacentral`). Every
  data-bearing resource Phase 4G-A's own infrastructure-as-code defines
  (`infra/azure/`) is hard-coded to this region — see `docs/deployment/
  azure-ingestion-production.md` §0. This decision was made and recorded
  by the user, never selected by this document, `docs/deployment/
  ingestion-production.md`, or any automated process.
- [ ] **Provider-specific cost estimate and budget approval** —
  **UNRESOLVED as of this correction pass (2026-09-10).** The figure
  originally recorded here (~CAD $46–72/month) was superseded by a
  corrected, unit-priced recalculation
  (`docs/deployment/azure-ingestion-production.md` §5) that includes two
  cost sources the original estimate omitted entirely: the Key Vault
  private endpoint (correction-pass item 4) and the managed-networking
  resources Azure documents as an additional charge for a custom-VNet
  Container Apps environment (correction-pass item 4's workload-profiles
  switch). **The corrected range is ~CAD $58–124/month — its upper bound
  exceeds the recorded CAD $100/month alerting ceiling by roughly 24%.**
  This box is unchecked, not because the region/provider decision is in
  doubt (it isn't — see the box above), but because the specific,
  numerical cost/budget precondition this box exists to record is no
  longer accurate and has not been re-approved. **`infra/azure/modules/
  budget.bicep`'s `monthlyAmount` remains unchanged at 100** — this
  correction pass does not alter a Recorded human decision on its own
  authority. Phase 4G-B remains blocked until a human makes one of the
  two decisions §5 itself names: (a) approve a revised budget ceiling
  with adequate margin above the corrected $124/month worst case, or (b)
  approve a redesigned, lower-cost architecture after reviewing the
  security trade-offs that redesign would mean giving up (the private
  Key Vault endpoint and/or the workload-profiles Container Apps
  environment this same correction pass added). Neither decision is this
  document's, or any automated process's, to make.
- [ ] **Private Key Vault operator-access mechanism decision** —
  **UNRESOLVED as of this correction pass (2026-09-10).**
  `infra/azure/modules/keyvault.bicep` sets `publicNetworkAccess:
  'Disabled'`, reachable only through its own private endpoint inside
  the VNet — by design, and not something this pass proposes changing.
  That design means §9 step 5's own `az keyvault secret set` secret-
  population command is **not reachable** from an ordinary engineer's
  laptop or an ordinary GitHub-hosted Actions runner, and this pass
  found no already-approved execution environment inside the trusted
  network boundary that could run it. `docs/deployment/
  azure-ingestion-production.md` §9 step 5 compares three candidate
  options (a hardened ephemeral in-VNet operator VM, an approved private
  self-hosted GitHub Actions runner, or an explicitly approved private-
  connectivity path such as an existing VPN/Bastion session), with their
  respective RBAC scope, logging/audit, teardown, secret-handling, and
  cost implications — **a human must choose one (or a documented
  alternative) before Phase 4G-B can populate a single real secret
  value.** This pass does not provision, silently select, or default to
  any of them, and does **not** temporarily enable public Key Vault
  access as a workaround.

**The provider/region decision above remains valid and checked. The
cost/budget precondition and the private Key Vault operator-access
decision are not — a Phase 4G authorization request cannot yet be
considered until both are re-resolved.** This does **not** authorize
Phase 4G-B's own execution even once all three boxes are eventually
checked: the checklist below is what Phase 4G itself must still do,
execution steps within an already-authorized phase, never automatically
satisfied by the decisions above.

## Phase 4G execution checklist

- [ ] **Infrastructure provisioning** — no cloud/database/object-store/
  secret-manager/rate-limiter-store resource has been created for this
  project. Creating any of them requires explicit approval, separate
  from approval of `docs/deployment/ingestion-production.md`'s
  architecture *recommendation*, and presupposes the provider/region
  precondition above is already satisfied.
- [ ] **Production deployment** — no ingestion-service instance has been
  deployed anywhere. Dispatching any future Phase 4G deployment
  workflow (§12 of `docs/deployment/ingestion-production.md`) requires
  explicit approval at the time of dispatch, every time — not a
  standing authorization from having approved the workflow's design.
- [ ] **Secret creation** — no TLS certificate, database credential,
  object-storage credential, or secret-manager entry has been created
  for this service.
- [ ] **Token issuance** — no bearer token has been issued to any real
  party. Every token in this repository's tests and documentation is
  synthetic (`docs/manual-token-provisioning.md`).
- [ ] **Real customer onboarding** — no customer has been contacted,
  agreed to pilot terms, or been onboarded per
  `docs/pilots/ingestion-pilot-runbook.md`. That runbook is preparatory
  documentation only.
- [ ] **Real report upload** — no real (non-synthetic, non-test) report
  has ever been sent to any ingestion-API instance, because no
  network-reachable, customer-reachable instance exists.

## What Phase 4F actually completed (for context, not authorization)

- An independent security review of Phases 4B–4E against
  `docs/milestones/v0.4.0-ingestion-api.md` §G's threat model, with
  every threat exercised against the real implementation
  (`docs/reviews/v0.4.0-phase-4f-security-readiness.md`).
- One new, narrowly-scoped, locally-testable production-hardening
  addition (`src/cloudops_guard/ingestion_api/production_readiness.py`
  — a fail-closed guard a future production entrypoint must call; no
  such entrypoint exists yet).
- One new adversarial regression test closing a previously-untested
  (but already-correct) gap in uploader endpoint validation
  (`tests/test_uploader_endpoint.py::
  TestPrivateAndLinkLocalAddressesOverPlainHttp`).
- A provider-neutral architecture *recommendation*, explicitly labeled
  pending approval (`docs/deployment/ingestion-production.md`).
- A deployment-workflow *design*, not an executable workflow file
  (`docs/deployment/ingestion-production.md` §12).
- Preparatory pilot documentation using only placeholder values
  (`docs/pilots/ingestion-pilot-runbook.md`, this checklist).

**None of the above provisions anything, deploys anything, or
authorizes any item in the unchecked list above.**

## What Phase 4G-A actually completed (for context, not authorization)

- Real Azure production adapters (`src/cloudops_guard/ingestion_azure/`),
  a hardened container image (`Dockerfile`), modular Bicep
  infrastructure-as-code (`infra/azure/`), and a manual-dispatch-only
  deployment/rollback workflow (`.github/workflows/
  deploy-ingestion-azure.yml`) — see `docs/deployment/
  azure-ingestion-production.md` for the complete description.
- The provider/region decision (checked above) and a real, numerical
  cost estimate — since corrected by a later pass to ~CAD $58–124/month
  and **no longer checked above**, pending a human re-decision on the
  budget ceiling or the architecture (see that precondition's own entry
  for the full explanation).
- **Phase 4G-A creates no Azure resource, reads no real credential, and
  contacts no live Azure subscription.** Every adapter/container/
  Bicep template was tested against local, offline stand-ins only (a
  real PostgreSQL container, a real local Azurite emulator) — never a
  real Azure account. The deployment workflow itself was authored and
  validated (`actionlint`, real YAML parsing) but never dispatched.
- **No endpoint is live. No token exists. No customer data has been
  uploaded. The static website (`web/`) remains a wholly separate
  service, unaffected by any of this.**

## Required before any box in the execution checklist may be checked

**This list presupposes the provider/region precondition above is
already satisfied — it does not re-decide it. The cost/budget
precondition is currently unresolved (see its own entry above); nothing
in this execution checklist may be authorized until it is re-resolved,
in addition to whatever separate approvals the items below name.**

1. A separate, explicit human decision to provision each specific
   resource, made with full awareness of its cost and operational
   commitment. **Still required — not satisfied by Phase 4G-A**, which
   implemented deployable code and infrastructure-as-code but created no
   resource (`docs/deployment/azure-ingestion-production.md` §1).
2. Real adapter implementations for `MetadataStore`/`ReportBlobStore`/
   `TokenStore`/`AttemptLimiter`/`RequestRateLimiter` against the
   chosen provider's actual products. **Implemented in Phase 4G-A**
   (`src/cloudops_guard/ingestion_azure/`) and tested against a real,
   local PostgreSQL instance and a real, local Azurite emulator — never
   against a real Azure subscription. Managed-identity authentication to
   Blob Storage in particular remains unverified against live Azure
   (`docs/deployment/azure-ingestion-production.md` §8/§11).
3. A production entrypoint that constructs a real `IngestionApiConfig`
   and calls `production_readiness.validate_production_config` before
   accepting any request. **Implemented in Phase 4G-A**
   (`src/cloudops_guard/ingestion_azure/entrypoint.py`) and proven, in a
   real local container, to fail closed without configuration and to
   open real PostgreSQL connections and serve `GET /api/v1/capabilities`
   correctly when configured — never run against real Azure
   infrastructure.
4. A completed disaster-recovery runbook. **Drafted in Phase 4G-A**
   (`docs/deployment/azure-ingestion-production.md` §7: RPO 24h, RTO 8h,
   a restore-test procedure) but **the restore drill itself has not been
   executed** — this remains a hard blocker until it has.
5. A completed monitoring/support-ownership plan
   (`docs/pilots/ingestion-pilot-runbook.md` §12 names this as not yet
   defined). **Alerting infrastructure exists** (`infra/azure/modules/
   monitoring.bicep`) but is not deployed, and the on-call recipient
   address is still a placeholder.
6. A specific pilot customer's written, informed consent
   (`docs/pilots/ingestion-pilot-runbook.md` §2).
7. An audited, tenant-scoped, operator-only ingestion-inventory and
   retirement/purge mechanism, tested end to end — required before
   complete pilot offboarding can ever be guaranteed
   (`docs/pilots/ingestion-pilot-runbook.md` §16 names this as a hard
   blocker; it must never rely solely on customer-retained
   `ingestion_id`s). **Implemented in Phase 4G-A**
   (`src/cloudops_guard/ingestion_azure/inventory.py`,
   `ops_cli.py tenant-offboard-plan`/`tenant-offboard-execute`) — never
   relies on a customer-provided ID list (proven by a dedicated tabletop
   test against a real database, `docs/deployment/
   azure-ingestion-production.md` §8), tenant-identity double-entry,
   exact confirmation phrase, and an append-only audit trail. Tested
   against real PostgreSQL only — never yet exercised against a live
   pilot customer's actual data.

This checklist itself grants no authorization. It exists so that a
future Phase 4G request can be checked against a concrete, written list
of what must happen — each with its own separate, explicit, human
sign-off — rather than any single approval being read as covering all
of them at once.
