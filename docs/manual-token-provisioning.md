# Manual bearer-token provisioning procedure (v0.4.0 Phase 4C)

**This document describes a manual, out-of-band procedure a human
operator would follow once a real ingestion service exists — it does not
describe anything runnable against production today.** Phase 4C
implements the token-generation and Argon2id-hashing *mechanics* this
procedure uses (`src/cloudops_guard/ingestion/token_issuance.py`,
`argon2_backend.py`), and a documented manual procedure for using them,
but:

- **No real customer token has been generated or issued.** Every token
  in this repository's tests and this document's own examples is a
  synthetic value used for illustration or automated testing only.
- **No production credential store exists.** `provision_token` (below)
  returns a `TokenRecord` value; nothing in this codebase inserts one
  into a real, durable, production `TokenStore` — that insertion
  mechanism is an explicit later production-store responsibility
  (`docs/milestones/v0.4.0-ingestion-api.md` §I).
- **No self-service token UI exists**, and this procedure does not
  describe building one. It is a manual procedure for an authorized
  operator, mirroring exactly how this project already provisions the
  GitLab collector's own token
  (`CLOUDOPS_GUARD_GITLAB_TOKEN`) — out of band, by a human, never
  through a self-service flow this codebase implements.
- **No HTTP endpoint exists to receive a provisioned token.** Phase 4D
  (a separate, not-yet-authorized phase) is required before any of this
  is reachable over a network.

## The procedure

1. **An authorized operator supplies the tenant ID and explicit scopes.**
   These are business decisions made by a human with the authority to
   grant a customer ingestion access — never inferred, defaulted, or
   guessed by the tooling itself. Example (a fictional tenant, for
   illustration only):

   ```python
   tenant_id = "acme-pilot"
   scopes = [TokenScope.REPORTS_WRITE, TokenScope.REPORTS_READ]
   ```

2. **The tool generates `lookup_id` and `secret` independently**, each
   drawn from Python's `secrets` module (`token_issuance.generate_lookup_id`/
   `generate_secret`) — never from a seeded, deterministic, or otherwise
   predictable source in this or any future phase's production path.

3. **Only `lookup_id`, the Argon2id hash of `secret`, `tenant_id`,
   `scopes`, `revoked=False`, and `created_at` are captured** in the
   resulting `TokenRecord` (`token_issuance.provision_token`'s return
   value). `secret` itself is never written into that record, logged, or
   retained by this function in any recoverable form once it returns.

4. **The complete plaintext token is returned exactly once**, via
   `ProvisionedToken.token`, for the operator to deliver to the customer
   through a secure, out-of-band channel of their own choosing (e.g. a
   password manager's secure-note-sharing feature, not any channel this
   codebase implements). Nothing in this codebase prints, logs, or
   persists that value anywhere; the operator's own delivery channel is
   entirely outside this codebase's responsibility.

   ```python
   from cloudops_guard.ingestion.token_issuance import provision_token
   from cloudops_guard.ingestion.models import TokenScope

   issued = provision_token("acme-pilot", [TokenScope.REPORTS_WRITE, TokenScope.REPORTS_READ])

   # Deliver issued.token to the customer now, out of band, exactly once.
   # Its shape is <lookup_id>.<secret>, e.g.
   #   <22-character-lookup-id>.<43-character-secret>
   # (a placeholder only — this document never contains a real, usable
   # credential; every value here is either a shape description or a
   # fictional string a test uses for illustration)

   # issued.token_record is what a future production TokenStore
   # implementation is responsible for durably storing. In this local
   # reference implementation, for testing only:
   #   token_store.register_for_testing(issued.token_record)
   ```

5. **The plaintext token must never be:**
   - committed to Git (this document, and every test fixture in this
     repository, uses only synthetic or clearly-fake example values —
     never grep this repository for a real one, because none exists);
   - placed in a command-line argument (visible in shell history and
     process listings — `provision_token` is a plain function call, not
     a CLI flag, specifically to avoid this);
   - printed in a log (nothing in `token_issuance.py` or
     `argon2_backend.py` logs anything, ever, at any level);
   - included in a report or a JSON request body (there is no such body
     in Phase 4C — that is Phase 4D's `/api/v1/reports` envelope, which
     `docs/milestones/v0.4.0-ingestion-api.md` §E already documents as
     accepting `platform`/`report_schema_version`/`report`/
     `idempotency_key` only, never a token field);
   - placed in a URL (the milestone document's §F already requires the
     token be transmitted only via an `Authorization: Bearer <token>`
     header, once a transport exists);
   - copied into documentation as a usable credential (every example
     value in this document is illustrative, generated for this document
     only, and grants access to nothing).

6. **No real production insertion mechanism exists in Phase 4C.** Turning
   `reference.InMemoryTokenStore.register_for_testing` into a disguised
   production provisioning API was explicitly avoided — that method
   remains what its own docstring says: a test-only seeding hook for a
   local, in-memory, non-durable reference store, never wired to
   anything resembling a real credential store.

7. **No real customer token may be generated during implementation or
   testing.** Every `provision_token` call in this repository's test
   suite uses a fictional tenant ID (e.g. `"tenant-a"`) and exists only
   to exercise the generation/hashing/authentication code paths — never
   to produce a value delivered to, or usable by, any real party.

8. **Examples use unmistakably non-secret placeholders.** Every token-
   shaped value in this document, and in this package's tests, is either
   generated fresh by the test itself (and discarded when the test
   process exits) or an obviously-synthetic string (e.g.
   `"lookup-1.secret-value"` in a unit test asserting parse-rejection
   behavior) — never formatted to look like, or be mistaken for, a real
   issued credential.
