# Ingestion API pilot runbook (Phase 4F, preparatory only)

**This is a preparatory runbook, not a pilot authorization.** No pilot
customer has been identified, contacted, or onboarded. No token has
been issued. No infrastructure exists to onboard a pilot against. Every
name, tenant ID, and credential-shaped value in this document is a
placeholder — **never a real customer name or a real credential.**
Beginning a real pilot requires Phase 4G's own separate, explicit human
authorization — see `docs/pilots/phase-4g-authorization-checklist.md`,
which this runbook assumes is fully satisfied before any step below is
ever executed for real.

## 1. Pilot eligibility and scope

- A pilot customer is an organization that has explicitly agreed, in
  writing, to send CloudOps Guard audit reports (Kubernetes and/or
  GitLab, per the existing released contract) to the ingestion API for
  a bounded evaluation period.
- Scope is limited to what the existing, released CLI already produces
  (`report.json` from `cloudops-guard audit kubernetes`/`audit
  gitlab`) — never a bespoke or expanded data format for a specific
  pilot.
- A pilot does not imply any SLA, uptime commitment, or production
  support tier beyond what is explicitly agreed in the pilot's own
  written terms.

## 2. Explicit customer consent

Before any token is issued (Phase 4G), the customer must have
explicitly confirmed, in writing:

- What data will be sent (report content — findings, evidence, resource
  names, cluster/project identifiers; **never** Kubernetes Secrets,
  ConfigMap contents, container environment variable values, or
  application logs, per this project's existing read-only/no-secrets
  invariants, which the report format itself already enforces upstream
  of ingestion).
- Where it will be stored (the explicit region decided per
  `docs/deployment/ingestion-production.md` §8 — never left implicit).
- How long it will be retained and how it can be deleted (§4 below).
- That uploading is a separate, deliberate, manually-confirmed action
  (`cloudops-guard upload`, requiring an exact typed `UPLOAD` or
  `--yes`) — never automatic, and never triggered by running
  `cloudops-guard audit ...` alone.

## 3. What report data is, and is not, collected

**Collected** (once ingested): exactly the `report.json` contract
already released and unchanged by v0.4.0 — platform, findings (check
ID, title, severity, resource identifiers, evidence text, impact,
recommendation, timestamps), and the fixed envelope fields
(`platform`, `report_schema_version`, `idempotency_key` if supplied).

**Never collected, by construction, upstream of ingestion**: Kubernetes
Secret/ConfigMap contents, container environment variable values,
application logs, GitLab CI/CD variable values, job traces/artifacts,
raw or merged CI YAML — the existing collector-level invariants
(`CLAUDE.md`'s "Read-only invariant" and GitLab invariants) already
prevent these from ever appearing in a `report.json` in the first
place; the ingestion API has no code path that could recover them even
if it wanted to.

**Never collected by the ingestion API specifically**: the bearer
token value (only ever read from the `Authorization` header, never
logged or stored in recoverable form, §F); any client-supplied tenant/
customer identifier (rejected outright, `docs/reviews/
v0.4.0-phase-4f-security-readiness.md` threat 2).

## 4. Region and data-residency disclosure

The customer must be told, explicitly and in writing, the exact region
in which their metadata, report bytes, and backups of both will be
stored — per `docs/deployment/ingestion-production.md` §3/§8, the
provider and region decision is a **mandatory precondition to
*authorizing* Phase 4G**, not an activity performed during it and not a
default to discover later. This runbook cannot state a specific region,
because none has been selected — see
`docs/pilots/phase-4g-authorization-checklist.md`'s "Preconditions to
authorizing Phase 4G at all" for the exact required sequence (provider/
region decision, then cost/budget approval, then Phase 4G may be
authorized to begin, then Phase 4G provisions against that
already-recorded decision).

## 5. Retention and deletion behavior

- Default retention: 90 days from ingestion (§C's proposed default,
  configurable per pilot agreement — `IngestionApiConfig.
  retention_period`), triggering **automatic** retirement with
  `reason: "retention_expired"`.
- Customer-initiated deletion: `DELETE /api/v1/reports/{ingestion_id}`,
  producing `reason: "customer_requested"`.
- Both retirement triggers behave identically from the customer's point
  of view (§E.4): the record becomes unreadable via `GET` immediately;
  physical byte-level purge from primary storage completes
  asynchronously, within a bounded window (proposed: 30 days) — never
  claimed to be instantaneous.
- Repeated `DELETE` calls are idempotent and never overwrite the
  original retirement `reason`, even if called again after automatic
  expiry already retired the same record.

## 6. Token scopes and secure delivery

- A pilot token is proposed to carry all three scopes
  (`reports:write`/`reports:read`/`reports:delete`) by default, per §F
  ("a small pilot, not a broad platform").
- Provisioned only via the documented manual, out-of-band procedure
  (`docs/manual-token-provisioning.md`) by an authorized operator —
  never a self-service flow.
- Delivered to the customer through a secure, out-of-band channel of
  the operator's choosing (e.g. a password manager's secure-note-
  sharing feature) — **never** by email in plaintext, never in a
  support ticket, never in Slack/chat, never committed anywhere.
- The plaintext token is shown to the operator **exactly once** at
  provisioning time (`docs/manual-token-provisioning.md` step 4);
  nothing in this codebase logs, persists, or can later re-display it.

## 7. Token rotation and revocation

- Revocation takes effect on the *next* authentication attempt after
  the operator marks it revoked — no caching window
  (`docs/reviews/v0.4.0-phase-4f-security-readiness.md`, residual-risk
  section, independently confirmed by
  `tests/test_ingestion_authenticator_concurrency.py::
  test_revocation_completed_before_the_next_authenticate_call_fails_it`).
- Routine rotation (not tied to an incident): issue a new token via the
  same manual procedure, deliver it the same way, confirm the customer
  has switched their local configuration, then revoke the old token —
  never revoke before confirming the switch, to avoid an unplanned
  outage for the customer.
- Suspected-leak rotation: see §12 (incident response) — revoke
  immediately, do not wait for a confirmed switch first.

## 8. Customer-side uploader prerequisites

- The existing, released `cloudops-guard` CLI, with the `upload`
  command (Phase 4E) — `pip install cloudops-guard` (base install, no
  `api` extra required for the uploader specifically) or an equivalent
  already-documented install path.
- `CLOUDOPS_GUARD_INGESTION_TOKEN` set as an environment variable —
  **never** as a CLI flag (would appear in shell history/`ps` output),
  never committed to a file tracked by version control.
- The pilot's explicit `--endpoint` URL (or
  `CLOUDOPS_GUARD_INGESTION_URL`), ending in `/api/v1/reports`.

## 9. Dry-run and confirmation procedure

1. Run `cloudops-guard upload --report-dir <dir> --endpoint <url>
   --dry-run` first, always — performs full local validation and
   fingerprint computation, prints the exact summary that would be
   sent, and **makes zero network requests and requires no token to be
   configured**.
2. Review the printed summary (platform, endpoint, finding counts,
   severity summary, file size, `report_fingerprint`) — confirm it
   matches expectations before proceeding.
3. Only once satisfied, re-run without `--dry-run`. In an interactive
   terminal, type the exact, case-sensitive `UPLOAD` at the prompt
   (`Type UPLOAD to confirm sending this report to <endpoint>: `) — any
   other input, a blank line, or Ctrl-C aborts with no request sent. In
   CI/non-interactive use, pass `--yes` instead (still performs the
   same full local validation and prints the same summary first).

## 10. Synthetic preflight before real data

Before the very first real report is ever uploaded for a new pilot, the
customer (or the operator, on the customer's behalf, with the
customer's own generated synthetic report) should run the same
confirmation procedure (§9) against a **synthetic** report first —
proving connectivity, token validity, and endpoint correctness without
risking a real report on a first, unverified attempt. The web demo's
own synthetic fixtures
(`web/src/data/synthetic-kubernetes-report.json`,
`synthetic-gitlab-report-*.json`) are a reasonable model for what a
synthetic preflight report should look like, though the customer's own
locally-generated report against a non-production cluster/project is
preferable when available.

## 11. Upload verification

After a successful upload, the CLI prints the server's own confirmed
`ingestion_id`, `request_id`, and `received_at` (from the validated
`201`/`200` response, `src/cloudops_guard/uploader/response.py`) — the
customer should record the `ingestion_id` for their own reference. The
uploader independently verifies the server's returned
`report_fingerprint` matches the value it computed locally *before*
reporting success (`FingerprintMismatchError` otherwise) — a successful
upload is never reported unless this match holds.

## 12. Monitoring and support ownership

**Not yet defined — a Phase 4G precondition.** No production monitoring,
alerting, or on-call rotation exists, because no production deployment
exists. Before any real pilot begins, this section must be completed
with: who is paged on an ingestion-service failure, what the escalation
path is, and what response-time commitment (if any) applies.

## 13. Incident and suspected-token-leak response

1. **Revoke immediately** — do not wait for confirmation the leak is
   real; revocation is cheap and takes effect on the next request (§7).
2. **Issue a replacement token** via the same manual procedure (§6),
   delivered through a fresh secure channel (not the same channel that
   may have been compromised).
3. **Review ingestion-service logs** for the affected `lookup_id`/
   `tenant_id` for any request pattern inconsistent with the customer's
   own known usage — logs contain only the fixed allowlist (`request_id`,
   `ingestion_id`, `tenant_id`, timestamps, `report_fingerprint`,
   status, byte size, HTTP status, latency — never report content or
   the token value, `docs/reviews/v0.4.0-phase-4f-security-readiness.md`
   threat 7), so this review can identify *volume/timing* anomalies but
   never inspect exfiltrated report content directly from logs (there
   is none to inspect there).
4. **Notify the customer** of the suspected leak, the revocation, and
   the new token's delivery, per the pilot's own written incident-
   communication terms (§2).
5. **Record the incident** (what happened, when revoked, when replaced)
   for post-pilot review.

## 14. Customer deletion requests

A single-report deletion request is honored via
`DELETE /api/v1/reports/{ingestion_id}` (§5).

**A request for full pilot-data deletion is never satisfied merely by
deleting every `ingestion_id` the customer happens to have a record
of.** The ingestion API deliberately has no tenant-list or bulk-delete
endpoint (a deliberate, minimal Phase 4D scope choice, consistent with
§G's enumeration-prevention threat model — **this pass does not add
one, and none should ever be added as a public, customer-reachable
endpoint**), but relying solely on the customer's own retained ID list
is insufficient for a *complete* deletion, because:

- the customer may have lost or never recorded an `ingestion_id`;
- an upload may have been made (e.g. by a script, a former employee, or
  a CI job run on the customer's behalf) without that upload's
  `ingestion_id` ever being retained anywhere on the customer's side;
- a compromised token (§13) could have been used to create an
  ingestion the customer has no knowledge of at all.

**Full pilot-data deletion therefore requires an operator-side,
audited, tenant-scoped ingestion-inventory mechanism — see §16.** This
mechanism is **not** a public API endpoint; it is an operator-only tool
used during offboarding, out of band from anything the customer's own
token can reach. Until it exists and has been tested, a "full pilot
deletion" request can only be honored to the extent of the customer's
own known `ingestion_id`s, and this limitation must be disclosed to the
customer, never silently treated as complete (§16).

## 15. Pilot suspension

A pilot may be suspended (token revoked, no new ingestion accepted)
without full offboarding, for reasons including: the customer's own
request, a suspected security incident under investigation (§13), or a
sustained abuse-control trigger (§F/§G's rate-limiting/attempt-limiting
layers). Suspension is reversible (issue a replacement token to
resume); offboarding (§16) is not.

## 16. Offboarding and final-deletion confirmation

**Hard blocker: complete pilot offboarding cannot be guaranteed today,
and must not be attempted for a real pilot, until the mechanism in step
2 below exists and has been tested end to end.** This is listed as a
precondition item in
`docs/pilots/phase-4g-authorization-checklist.md` and as a consolidated
blocker in `docs/deployment/ingestion-production.md` §11. Nothing in
this section authorizes building that mechanism as part of this
(Phase 4F) correction pass — it is Phase 4G scope, recorded here so it
is not overlooked.

### Required mechanism (Phase 4G scope, not yet built)

Before any real pilot may be offboarded, Phase 4G must provide an
**audited, operator-only** method that, for a given tenant:

- returns a **complete** inventory of that tenant's ingestion records
  (every status: received, retired, and still-tombstoned) — derived
  from the metadata store's own tenant-scoped index, **never** from the
  customer's own retained `ingestion_id` list, so it also surfaces
  records the customer never recorded, never received, or (in the
  compromised-token case, §13) never even knew were created;
- lets an authorized operator request retirement/purge for every
  applicable record found, not only ones already known.

This mechanism must be:

- **tenant-scoped** — operating against exactly one `tenant_id` per
  invocation, never a cross-tenant listing;
- **least-privilege** — usable only by an authorized operator, through
  a distinct operator credential/role, never through a pilot customer's
  own bearer token or its scopes (§6);
- **logged without report or token content** — its own audit trail
  follows the same allowlist-only logging discipline as the rest of the
  service (`docs/reviews/v0.4.0-phase-4f-security-readiness.md`
  threat 7): which operator, which tenant, when, and the outcome —
  never report content, never a token value;
- **resistant to accidentally selecting another tenant** — e.g.
  requiring the operator to explicitly confirm the resolved tenant
  identity (not just a raw ID that could be transposed or mistyped)
  before any retirement/purge is requested against it.

**This is explicitly not a public, customer-reachable API endpoint.**
The public ingestion API's deliberate absence of a tenant-list or
bulk-delete endpoint (§14, §G's threat model) is unchanged by this
requirement.

### Offboarding procedure (once the mechanism above exists)

1. Revoke the pilot's token (§7).
2. Run the operator-only tenant-inventory mechanism above for the
   tenant and request retirement/purge for every record it returns —
   **not** only the `ingestion_id`s the customer separately provided
   (§14). Reconcile the two lists; if the inventory surfaces a record
   the customer did not know about, record that fact for the incident/
   post-pilot review (§13/§17) rather than silently deleting it without
   note.
3. Confirm, in writing to the customer, using **precisely one of the
   four distinct confirmation levels below** — never a blanket "your
   data has been fully deleted" statement that conflates them:
   - **Immediate unreadability**: every record is now unreadable via
     `GET`/`DELETE` (true as soon as retirement completes — may be
     confirmed right away).
   - **Confirmed primary purge**: the report bytes and full metadata
     for every record have been physically purged from primary
     storage. May be confirmed **only after this has been verified**
     (e.g. by re-querying primary storage/blob store directly for
     absence, not merely by observing that the proposed purge window,
     proposed 30 days, has elapsed — elapsed time alone is not
     verification).
   - **Tombstone disclosure**: per §E.4's design, a minimal tombstone
     (enough to keep repeated `DELETE` calls idempotent and to prevent
     `ingestion_id` reuse ambiguity) intentionally persists for a
     bounded period after primary purge. If a tombstone still exists by
     contract at the time of this communication, **that must be
     disclosed precisely** ("a minimal, non-content tombstone record
     persists until <date/window>; it contains no report content") —
     never described as though nothing remains.
   - **Backup rotation/expiry**: a statement that *all* retained
     customer-linked data — including backups — is gone may be issued
     **only after** the applicable backup rotation window has actually
     elapsed **and been verified** (e.g. confirming the specific backup
     generation containing the data has actually rotated out, not
     inferred from "the documented window has passed" alone,
     `docs/deployment/ingestion-production.md` §8).
4. Do not send the "all retained customer-linked data is gone"
   confirmation until *all four* levels above are satisfied and
   verified for *every* record found in step 2 — primary purge,
   tombstone expiry, and backup rotation/expiry alike.

### Tabletop test: incomplete customer-provided inventory

**Scenario**: a pilot customer requests full offboarding and provides
their own list of `ingestion_id`s. Unknown to the customer, one real
ingestion for their tenant is missing from that list (e.g. it was
created by an automated job whose output the customer never reviewed).

**Required outcome**: the offboarding procedure above must still
identify and retire/purge that missing record, because step 2 queries
the operator-only tenant-scoped inventory mechanism directly rather
than relying on the customer's own list. A procedure that instead
iterated only over the customer-provided `ingestion_id`s — the
behavior this correction pass removes — would silently leave that
record behind while still reporting successful full deletion, which is
exactly the gap this section exists to close. `tests/
test_v040_milestone_contract.py` includes a documentation-contract test
asserting this scenario and its required outcome are both described in
this document, so the description cannot silently regress back to
"delete every `ingestion_id` the customer has record of."

## 17. Known residual bearer-token replay risk

Disclosed to every pilot customer explicitly, matching §G's own
recorded residual risk: this design relies on TLS and revocable bearer
tokens for replay protection, not request signing or short-lived
nonces. A stolen, not-yet-revoked token can be replayed until revoked.
This is an accepted, documented pilot-scale tradeoff — the incident
response procedure (§13) is the operational mitigation, not a claim
that the risk is eliminated.

## 18. Prohibited data and unsupported uses

- **Never** send Kubernetes Secret/ConfigMap contents, application
  logs, or CI/CD variable values through this pipeline, even manually
  — the ingestion API's own envelope validation cannot detect "this
  finding's evidence field happens to contain a real secret" if a
  customer's own tooling somehow produced a non-standard report; the
  read-only collector invariants upstream are what actually prevent
  this, and this pilot is scoped to the standard, unmodified
  `report.json` output only.
- **Never** use the pilot ingestion endpoint for anything other than
  CloudOps Guard's own report format — it is not a general-purpose
  file-upload or logging service.
- **Never** treat a pilot token as usable beyond the single tenant it
  was issued for.

## 19. Exit criteria for continuing or ending the pilot

**Continue** (candidate for broader/permanent status, subject to
separate future authorization — never implied by this document) when:
all uploads have succeeded reliably, no unresolved security incident
occurred, retention/deletion behavior matched what was disclosed, and
the customer has expressed continued interest.

**End the pilot** when: the agreed evaluation period elapses without a
continuation decision, the customer requests it, a security incident
is not adequately resolved, or the pilot's own written terms' exit
conditions are met. Ending a pilot always includes full offboarding
(§16), regardless of the reason.
