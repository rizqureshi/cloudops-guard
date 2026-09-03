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

**The two items below are not Phase 4G activities. They are
prerequisites that must already be satisfied before Phase 4G may be
authorized to begin — Phase 4G executes a decision already made, it
does not make the decision itself.** Every item in "Phase 4G execution
checklist" below presupposes both of these are already done.

- [ ] **Provider and region/data-residency decision** — a separate,
  explicit human decision naming the actual cloud provider and region
  for every store, service, and backup. `docs/deployment/
  ingestion-production.md` §3 and §8 explicitly defer this decision to
  the user; it is not implied or satisfied by approving that document's
  architecture-*pattern* recommendation, and it is not something Phase
  4G selects on its own once started.
- [ ] **Provider-specific cost estimate and budget approval** — a real,
  numerical cost estimate priced against the *actually-selected*
  provider's current published rates for the pilot's expected traffic,
  reviewed and explicitly approved by the user. `docs/deployment/
  ingestion-production.md` §3's cost comparisons are qualitative only
  and are never a substitute for this. This step is itself gated on the
  provider/region decision immediately above — it cannot happen first.

**Only once both boxes above are checked may a Phase 4G authorization
request be considered at all.** The checklist below is what Phase 4G
itself must then still do, execution steps within an already-authorized
phase — not further preconditions to authorizing it.

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

## Required before any box in the execution checklist may be checked

**This list presupposes both preconditions above (provider/region,
cost/budget) are already satisfied — it does not re-decide them.**

1. A separate, explicit human decision to provision each specific
   resource, made with full awareness of its cost and operational
   commitment.
2. Real adapter implementations for `MetadataStore`/`ReportBlobStore`/
   `TokenStore`/`AttemptLimiter`/`RequestRateLimiter` against the
   chosen provider's actual products — none exist yet (only the
   in-memory reference implementations, Phase 4B).
3. A production entrypoint that constructs a real `IngestionApiConfig`
   and calls `production_readiness.validate_production_config` before
   accepting any request — does not exist yet.
4. A completed disaster-recovery runbook (`docs/deployment/
   ingestion-production.md` §10 names this as a known blocker).
5. A completed monitoring/support-ownership plan
   (`docs/pilots/ingestion-pilot-runbook.md` §12 names this as not yet
   defined).
6. A specific pilot customer's written, informed consent
   (`docs/pilots/ingestion-pilot-runbook.md` §2).
7. An audited, tenant-scoped, operator-only ingestion-inventory and
   retirement/purge mechanism, tested end to end — required before
   complete pilot offboarding can ever be guaranteed
   (`docs/pilots/ingestion-pilot-runbook.md` §16 names this as a hard
   blocker; it must never rely solely on customer-retained
   `ingestion_id`s).

This checklist itself grants no authorization. It exists so that a
future Phase 4G request can be checked against a concrete, written list
of what must happen — each with its own separate, explicit, human
sign-off — rather than any single approval being read as covering all
of them at once.
