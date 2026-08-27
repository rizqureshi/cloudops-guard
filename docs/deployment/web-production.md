# CloudOps Guard web — production deployment (Phase 3K)

**This document describes deployment configuration that exists but has
never been used.** Writing this document, `.github/workflows/deploy-web.yml`,
and `web/deploy/render-wrangler-configs.mjs` does not deploy, publish, tag,
or release anything. No Cloudflare Worker, route, domain, DNS record,
Turnstile widget, Email Service binding, secret, preview, or production
site exists as a result of Phase 3K. Nothing in this phase runs Wrangler,
contacts a Cloudflare API, or triggers `deploy-web.yml`. An actual
deployment happens only when a repository owner later, separately, and
explicitly dispatches that workflow — at the time they decide to do so,
exactly as v0.1.0 and v0.2.0's tagging and release publication required a
separate, explicit authorization each time.

## 1. Topology and privacy rationale

Production uses **two independent Cloudflare deployment units**, never one:

1. **Static-assets unit.** Serves the built Astro site (`web/dist`) through
   [Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/).
   It has **no `main` Worker entry point** — a request for a missing asset
   is answered by Cloudflare's own static-asset handling
   (`not_found_handling: "404-page"`, the correct mode for a
   fully-prerendered SSG site), never by application code. This unit is
   configured as the hostname's own **custom domain** — it is the origin
   for the site.
2. **Contact-API unit.** The existing, unmodified `web/worker/contact.ts`,
   routed to **only** the exact path `<hostname>/api/contact` — never
   `/api/*` or another wildcard. It carries no static assets at all.

This split exists for the same reason the rest of this project separates
report-handling code from the contact/feedback boundary (see `CLAUDE.md`
and `docs/milestones/v0.3.0-interactive-web-demo.md`, §M/§N): the site
that serves report-derived content and the Worker that can send an email
are architecturally distinct deployment units with independent, minimal
permissions, not one Worker that happens to branch on a path. A
compromise or misconfiguration of the static unit cannot grant it the
contact unit's Email/Turnstile capability, because it never has that
capability to begin with — it has no `main`, no bindings, and no secret.

Both units set `workers_dev: false` and `preview_urls: false`: neither
unit is ever reachable at a `*.workers.dev` address or an ephemeral
preview URL, only at the configured custom domain/route.

## 2. Required Node and pinned Wrangler versions

- **Node 24.x** — matches `web/package.json`'s `engines.node` and
  `.github/workflows/web-ci.yml`. Deployment must run on Node 24, not a
  different major version, to match the same runtime every other check in
  this project already validates against.
- **Wrangler, pinned to exactly `wrangler@4.102.0`** — invoked via
  `npx --yes wrangler@4.102.0 ...` in `deploy-web.yml`, never an
  unpinned `wrangler@latest` or a third-party deploy Action. A future
  operator who wants to move to a newer Wrangler version should do so as
  its own reviewed change to `deploy-web.yml`, not silently via floating
  version resolution.
- **Workers compatibility date, pinned to exactly `2025-01-01`** — both
  generated Wrangler configs (`web/deploy/render-wrangler-configs.mjs`'s
  `COMPATIBILITY_DATE` constant) use this single fixed value. It was
  deliberately reviewed and selected as this project's compatibility
  baseline, not left unfinished for a future operator to fill in — this
  configuration is deployment-ready as written. A future operator who
  wants to adopt a newer Workers runtime default should do so as its own
  separate, reviewed change to that constant, never by computing the date
  dynamically and never by treating the current value as unfinished work.

## 3. Environment variables and secrets, by name and classification

### `web/deploy/render-wrangler-configs.mjs` inputs (read directly from `process.env`)

| Name | Classification | Purpose |
|---|---|---|
| `DEPLOY_OUT_DIR` | Operational (runner-local path) | Absolute, pre-existing, non-symlink directory the two config files are written into. |
| `DEPLOY_HOSTNAME` | Configuration | Production hostname, e.g. `www.example.com`. |
| `DEPLOY_ZONE_NAME` | Configuration | The hostname's DNS zone; hostname must equal the zone or be a subdomain of it. |
| `DEPLOY_STATIC_WORKER_NAME` | Configuration | Cloudflare Worker name for the static-assets unit. |
| `DEPLOY_CONTACT_WORKER_NAME` | Configuration | Cloudflare Worker name for the contact-API unit (must differ from the static name). |
| `DEPLOY_CONTACT_TO_EMAIL` | Sensitive (destination address) | The Email binding's `destination_address`. |
| `DEPLOY_CONTACT_FROM_EMAIL` | Sensitive (sender address) | The Email binding's `allowed_sender_addresses` entry and the contact Worker's `CONTACT_FROM_EMAIL` var. |
| `DEPLOY_WEB_ROOT` | Operational (runner-local path) | Absolute path to the checked-out `web/` directory — used only to resolve `web/worker/contact.ts` and `web/dist` to absolute paths. |

### GitHub `production` environment — **secrets**

| Name | Classification | Purpose |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | Secret credential | Scoped Cloudflare API token used only by the two `wrangler deploy` steps. |
| `CLOUDFLARE_ACCOUNT_ID` | Secret-adjacent (treated as a secret) | The target Cloudflare account. |
| `CONTACT_TO_EMAIL` | Sensitive (destination address) | Mapped into the renderer as `DEPLOY_CONTACT_TO_EMAIL`. |
| `CONTACT_FROM_EMAIL` | Sensitive (sender address) | Mapped into the renderer as `DEPLOY_CONTACT_FROM_EMAIL`. |

### GitHub `production` environment — **variables**

| Name | Classification | Purpose |
|---|---|---|
| `PUBLIC_TURNSTILE_SITE_KEY` | Public (safe to expose to the browser) | The **real production** Turnstile site key, used for the deployment job's own production build — never Cloudflare's public *test* key (`1x00000000000000000000AA`) that `web-ci.yml`'s validation-only build uses, and never reused from that earlier test-key build. |
| `DEPLOY_HOSTNAME` | Configuration | Mapped straight into the renderer. |
| `DEPLOY_ZONE_NAME` | Configuration | Mapped straight into the renderer. |
| `DEPLOY_STATIC_WORKER_NAME` | Configuration | Mapped straight into the renderer. |
| `DEPLOY_CONTACT_WORKER_NAME` | Configuration | Mapped straight into the renderer. |

### Cloudflare Worker secret — **not stored in GitHub at all**

| Name | Classification | Provisioning |
|---|---|---|
| `TURNSTILE_SECRET_KEY` | Secret credential | **Manually pre-provisioned, before the first `deploy-web.yml` dispatch** — see §8 for the exact, non-circular procedure. Never in CI, never as a GitHub secret, never provisioned by `deploy-web.yml` itself. The rendered contact config only *declares* that this secret name is required (`"secrets": { "required": ["TURNSTILE_SECRET_KEY"] }`, a field Wrangler itself enforces — see §8); its value is never read, requested, or written by any file in this repository. |

No file in this repository ever contains a real value for any of the
sensitive/secret rows above — only their names, as documented here.

## 4. Cloudflare account and zone prerequisites

Before any deployment is attempted:

- A Cloudflare account with Workers, Workers Static Assets, and Email
  Routing/Email Workers enabled.
- The production zone (`DEPLOY_ZONE_NAME`) already added to that account
  and its nameservers active (or, for a subdomain-only setup, the parent
  zone active and delegated appropriately).
- A scoped Cloudflare API token (never the account's Global API Key) with
  only the permissions the two `wrangler deploy` commands need — Workers
  Scripts edit, Workers Routes edit, and Account read, scoped to the
  specific account and zone. Do not grant broader account-level access
  than these two deployments require.

## 5. Custom-domain and exact-route prerequisites

- `DEPLOY_HOSTNAME` must be a hostname the operator controls within
  `DEPLOY_ZONE_NAME` — verified in the Cloudflare dashboard, not assumed.
- The static unit is attached to `DEPLOY_HOSTNAME` as a **Custom Domain**
  (`routes: [{ pattern: DEPLOY_HOSTNAME, custom_domain: true }]`) — this
  is the hostname's origin.
- The contact unit is attached to the **exact** route
  `DEPLOY_HOSTNAME/api/contact` only — confirm in the dashboard after
  deployment that no broader pattern (`/api/*`, `DEPLOY_HOSTNAME/*`) was
  substituted, and that the static unit does not also claim this path.

## 6. Email Service sender-domain and destination verification

- `DEPLOY_CONTACT_FROM_EMAIL`'s domain must be a domain with Email Routing
  enabled and verified in the same Cloudflare account/zone — Cloudflare's
  Email Workers `send_email` binding can only send from a domain you have
  verified control of.
- `DEPLOY_CONTACT_TO_EMAIL` (the `destination_address`) should be a
  monitored mailbox an operator actually reads — this is where every
  contact/feedback submission's email ultimately arrives.
- Confirm both addresses independently, outside of any generated config
  file (which never echoes them back to a log) — read them from the
  GitHub environment secret configuration screen and from Cloudflare's own
  Email Routing dashboard.

## 7. Turnstile production widget prerequisites

- A **production** Cloudflare Turnstile widget, created for
  `DEPLOY_HOSTNAME` specifically (not the public always-passing test
  widget `1x00000000000000000000AA` used everywhere in local development
  and in `web-ci.yml`).
- Its **site key** becomes the GitHub `production` environment variable
  `PUBLIC_TURNSTILE_SITE_KEY`.
- Its **secret key** is provisioned as described in §8 below — never as a
  GitHub secret.

## 8. Manual provisioning of `TURNSTILE_SECRET_KEY`

This is the one credential this repository's tooling deliberately never
touches, and it follows **one single, non-circular procedure** — not a
sequence that depends on `deploy-web.yml` having already run:

1. Before `deploy-web.yml` is ever dispatched for the first time, an
   operator with Cloudflare account access **manually creates the
   contact-API Worker through the Cloudflare dashboard** (Workers &
   Pages → Create → Worker, named exactly `<DEPLOY_CONTACT_WORKER_NAME>`,
   accepting the dashboard's own default starter code as-is). The
   dashboard's starter code is never used for anything: `deploy-web.yml`'s
   own first real dispatch immediately replaces it with the real,
   rendered contact-unit config and the existing, unmodified
   `web/worker/contact.ts`. The dashboard is the one unambiguous way to
   do this: an equivalent `wrangler deploy` CLI invocation run from an
   arbitrary local working directory could silently inherit an unrelated
   ambient `wrangler.json`/`wrangler.toml`, entry point, or compatibility
   date already present in that directory, producing an unintended or
   simply unusable bootstrap Worker -- the dashboard reads no local
   configuration file at all, so no such ambiguity is possible. This
   bootstrap creation is itself a **separate, expressly authorized future
   action**, performed manually by an operator outside a terminal, not by
   anything in this repository -- nothing here executes it.
2. That same operator then runs, locally, on their own machine:

   ```text
   wrangler secret put TURNSTILE_SECRET_KEY --name <DEPLOY_CONTACT_WORKER_NAME>
   ```

   and pastes the Turnstile widget's secret key when prompted.

3. Only once both steps above are complete is the environment ready for
   `deploy-web.yml`'s first dispatch.

This manual creation-and-secret sequence is **separately authorized manual
operator work, outside Phase 3K's scope** — Phase 3K implements and
documents the requirement, it does not perform the provisioning. It is a
one-time step (repeated only on key rotation): `deploy-web.yml` never
creates a Worker through the dashboard or the CLI, and never runs
`wrangler secret put`; the secret's value is never present in any file,
environment variable, or log this repository's automation produces.

`secrets.required` in the rendered contact config
(`"secrets": { "required": ["TURNSTILE_SECRET_KEY"] }`) is **Wrangler's own
enforced configuration field** — Wrangler itself refuses to `deploy` a
Worker whose declared required secret has not already been provisioned on
that Worker, independent of anything this repository's own tooling checks.
That is what makes `deploy-web.yml`'s contact-unit deploy step fail closed
if an operator skips this section: the workflow does not need its own
separate check for the secret's presence, because Wrangler's own deploy
command already refuses to proceed without it.

## 9. GitHub `production` environment setup and protection

Create a GitHub Environment named exactly `production` in this
repository's settings, holding the secrets and variables listed in §3,
and configure its protection rules to require:

- **A reviewer other than the initiator** — the person who dispatches
  `deploy-web.yml` cannot be the same person who approves the resulting
  deployment-job run.
- **Self-review prevented** — enforced by the setting above, not merely
  a convention.
- **Main-only deployment branches** — the environment's "Deployment
  branches" restriction limited to `main`, matching `deploy-web.yml`'s own
  independent check that the dispatch's `github.ref` is `refs/heads/main`
  (defense in depth: both the workflow and the environment enforce this).
- **Administrator bypass disabled**, where the repository's GitHub plan
  permits configuring that (some plans only offer this on organization
  accounts) — if unavailable on the current plan, record that limitation
  explicitly rather than assuming it is enforced.

## 10. The workflow's two separate approvals

Deploying requires two independent, human actions, not one:

1. **Dispatching** `deploy-web.yml` itself — supplying the exact
   confirmation phrase and the full 40-character commit SHA to deploy.
2. **Approving the `production` environment's protection gate** — a
   different person than whoever dispatched it, per §9.

The `validate` job (no Cloudflare credential of any kind) runs first and
independently verifies the dispatch ref, the confirmation phrase, the
commit SHA's format, that the checked-out `HEAD` matches it exactly, and
that the commit is reachable from `origin/main` — the `deploy` job cannot
start until `validate` succeeds, and cannot proceed past that until the
`production` environment's own reviewer approves it.

## 11. Pre-deployment checklist

Before ever dispatching `deploy-web.yml`:

- [ ] §4–§8 above are all genuinely complete (account, zone, custom
      domain, exact route, Email Routing, Turnstile production widget,
      `TURNSTILE_SECRET_KEY` provisioned).
- [ ] The GitHub `production` environment (§9) exists with its protection
      rules configured and verified, not just created.
- [ ] All secrets/variables in §3 are set to real production values, and
      no example/test/development value remains.
- [ ] `COMPATIBILITY_DATE` in `web/deploy/render-wrangler-configs.mjs` has
      been reviewed against Cloudflare's current Workers
      compatibility-date guidance — it is a fixed, human-reviewed
      constant, never computed automatically, so it can silently go stale
      if never revisited.
- [ ] The exact commit SHA to deploy has itself passed `CI` and `Web CI`
      on `main`.
- [ ] The person dispatching and the person approving the `production`
      environment gate are two different people.
- [ ] This is a **real, deliberate, explicitly authorized** deployment —
      not a rehearsal, and not triggered "to see what happens."

## 12. Future post-deployment verification checklist

After a real deployment (this checklist does not exist to be run today —
it is what a future operator should work through once a deployment has
actually happened):

- **Static routes**: every one of the 29 production routes (see
  `web/tests/e2e/support/routes.ts`) returns `200` at the real hostname.
- **CSP**: the served `Content-Security-Policy` header/meta content
  matches what `web/tests/e2e/product-quality.spec.ts` and
  `accessibility.spec.ts` already verify against the local build — no
  `unsafe-inline`/`unsafe-eval`/wildcard, `connect-src 'none'` on the
  three report-derived routes, and the contact routes' narrower policy.
- **Browser console**: no console/page error on any route, matching the
  existing automated no-console-error coverage.
- **Accessibility**: a fresh, real `axe` scan (or a manual spot-check)
  against the live site, not only the local build.
- **Privacy / local-only report behavior**: `/demo/kubernetes`,
  `/demo/gitlab`, and `/explorer` still make zero network requests when
  interacted with, and no report content ever reaches `localStorage`,
  `sessionStorage`, `IndexedDB`, or a cookie — re-verified against the
  live origin, not assumed to carry over from local testing.
- **Contact success path**: a real, deliberate test submission to
  `/request-demo` or `/feedback` completes successfully end-to-end
  (Turnstile verifies, the email arrives at `DEPLOY_CONTACT_TO_EMAIL`).
- **Contact failure path**: confirm the `503`/mailto-fallback UI still
  behaves correctly against the live Worker (this is harder to trigger
  deliberately against production — documenting the expected behavior
  from the existing automated `contact-form.spec.ts` coverage is an
  acceptable substitute if a genuine failure cannot be safely induced).

## 13. Non-atomic deployment: order, failure handling, and recovery

The two units are deployed by two separate `wrangler deploy` invocations
in `deploy-web.yml`, in this fixed order: **static-assets unit first,
then contact-API unit.** This is **not an atomic operation** — Cloudflare
does not offer a single transaction spanning two Worker deployments, and
neither does this workflow attempt to simulate one.

- **If the static-assets deploy fails**: the workflow step fails and the
  job stops (no `continue-on-error`) — the contact-API deploy step never
  runs. The site remains however it was before this dispatch (either not
  yet deployed at all, in a first-deployment scenario, or still serving
  whatever the previous successful deployment left in place).
- **If the static-assets deploy succeeds but the contact-API deploy
  fails**: the static site is now live/updated, but `/api/contact` is
  serving whatever it served before this dispatch (on a first deployment,
  nothing at all — a 404 at that route, since it does not exist yet).
  This is a real, visible inconsistency window a future operator must
  resolve manually: re-dispatch once the contact-unit failure's root
  cause is fixed, or manually redeploy just the contact unit.
- **Recovery/rollback guidance**: redeploying a previous known-good commit
  through the same workflow (supplying that commit's SHA) is the
  documented recovery path for either unit. **Rollback has not yet been
  tested** — this statement is deliberate and should not be read as an
  implicit guarantee that redeploying an older commit behaves identically
  to a dedicated rollback feature (Cloudflare's own deployment-history
  rollback, if used instead, has likewise not been exercised by this
  project). A future operator's first real deployment should treat
  rollback as an open question to verify, not a tested safety net.

## 14. What Phase 3K does not do

Repeated here deliberately, because it is the single most important fact
in this document:

- **Phase 3K does not deploy anything.** No Cloudflare Worker, route,
  custom domain, DNS record, Turnstile widget, or Email Service binding
  exists as a result of this phase's work.
- **Phase 3K does not publish, tag, or release anything.** No git tag, no
  GitHub Release, no preview URL, no production URL.
- **`deploy-web.yml` has never been dispatched.** Implementing and
  reviewing it is not the same as running it.
- Everything above is **configuration and documentation describing a
  future, separately authorized action** — not that action itself.
