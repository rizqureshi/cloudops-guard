"""Abstract interfaces for the v0.4.0 ingestion storage layer
(`docs/milestones/v0.4.0-ingestion-api.md` §H).

These are Python ABCs, not a network- or database-facing SDK -- they exist
so a future implementation (a real database, a real object store, a real
secret manager: all later-phase decisions, §I) can be swapped in behind the
exact same call sites this package's own reference implementations
(`reference.py`) satisfy today. Phase 4B provides only local, in-memory
reference implementations against these interfaces -- never a production
store, never a network client, never a cloud SDK.

**Cross-store boundary (Phase 4A's own §8 requirement, recorded here
because this module is where both stores' independence is most visible)**:
`MetadataStore` and `ReportBlobStore` are two independent stores. An
ingestion may be reported as `received` only after its report bytes are
durably stored (`ReportBlobStore.put` succeeds) *and* its metadata record
is durably created (`MetadataStore.create_or_get_received` succeeds) --
never on the basis of only one of the two. Phase 4B implements neither an
ingestion coordinator nor any cross-store transaction: nothing in this
package calls both stores together, and nothing here claims an atomic
transaction spans two independent production stores (no such guarantee is
possible in general, once these interfaces are backed by two genuinely
separate production systems). **A future Phase 4D must define its own
failure-recovery strategy** for the case where one store's write succeeds
and the other's fails (e.g. a blob written but metadata creation failing,
or vice versa) -- this package deliberately defines no `pending` status and
no HTTP-layer workaround for that case; a caller that does not itself
observe both underlying writes succeed must never present partial or
orphaned reference-store state as a successful `received` ingestion.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Protocol

from .models import IngestionRecord, PurgeClaim, RetirementReason, TokenRecord, Tombstone


class MetadataStore(ABC):
    """§H's `MetadataStore` interface, exactly."""

    @abstractmethod
    def create_or_get_received(
        self,
        tenant_id: str,
        report_fingerprint: str,
        new_ingestion_id: str,
        new_record: IngestionRecord,
        idempotency_key: str | None = None,
    ) -> tuple[IngestionRecord, bool]:
        """Atomically create-or-return-existing (§E's idempotency
        semantics; §H's own interface comment spells out the exact
        three-step algorithm this must implement). Returns
        `(record, created)`. Raises `errors.IdempotencyKeyConflict` if
        `idempotency_key` is actively bound to a different
        `report_fingerprint` for this tenant.
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, tenant_id: str, ingestion_id: str) -> IngestionRecord | None:
        """`None` both when unknown and when `status != "received"`
        (§E.3/E.4).
        """
        raise NotImplementedError

    @abstractmethod
    def get_any_status(self, tenant_id: str, ingestion_id: str) -> IngestionRecord | None:
        """**Phase 4D correction pass, item 1**: the tenant-scoped
        lifecycle-eligibility lookup `lifecycle.purge_retired_ingestion`
        uses to decide *before ever touching `ReportBlobStore`* whether a
        purge is actually safe. Unlike `get`, this returns the record
        regardless of its current `status` (`received`, `retired`, or
        `deleted`) -- `get`'s own RECEIVED-only filtering exists
        specifically to mask a retired/deleted record from an ordinary
        customer `GET` (§E.3), which is the wrong behavior for an
        internal caller that needs to tell a live `received` record apart
        from an already-retired one *before* deciding whether physically
        deleting its blob is safe. Returns `None` for an unknown ID, an
        ID belonging to a different tenant, or one whose tombstone has
        since expired (identical scoping to `get`) -- never a foreign
        tenant's record. This method itself never mutates any state, so
        it carries no atomicity requirement of its own; the monotonic
        `received -> retired -> deleted` lifecycle (never backward) is
        what makes a subsequent, separate blob-deletion decision based on
        this read still safe under concurrent retirement/purge calls (see
        `lifecycle.py`).
        """
        raise NotImplementedError

    @abstractmethod
    def mark_retired(
        self, tenant_id: str, ingestion_id: str, at: dt.datetime, reason: RetirementReason
    ) -> IngestionRecord | None:
        """Idempotent: if already retired or deleted, returns the
        *existing* record unchanged -- never overwrites an
        already-recorded `reason` or `retired_at`, regardless of which
        `reason` this call itself passed. Returns `None` if unknown.
        """
        raise NotImplementedError

    @abstractmethod
    def mark_purged(
        self, tenant_id: str, ingestion_id: str, at: dt.datetime
    ) -> IngestionRecord | None:
        """Transitions a `retired` record to `deleted`, creating its
        tombstone. Idempotent; returns `None` if unknown.

        **Preserved, but no longer fully independent of the purge-claim
        mechanism below** -- for the same reason `ReportBlobStore.put`
        was preserved unused alongside `put_if_absent` in the first
        Phase 4D correction pass: Phase 4B's own already-approved
        reference implementation and test suite
        (`tests/test_ingestion_metadata_store_lifecycle.py`,
        `tests/test_ingestion_metadata_store_atomicity.py`) already treat
        this method as part of this interface's contract, and
        `lifecycle.purge_retired_ingestion` still exclusively uses
        `begin_purge`/`finalize_purge` instead, never this method
        directly. **Purge-claim hardening pass, item 2**: this method
        must now, under the same lock guarding claim state, refuse to
        run while an exact active `PurgeClaim` exists for this record --
        reproduced before this fix: retire a record; acquire a claim via
        `begin_purge`; call this method directly (bypassing the claim
        protocol entirely, since nothing coordinated the two); its
        tombstone then expires and the same ID is reused by a genuinely
        new `received` record with a live blob; the original claim
        holder's own, still-pending physical blob deletion then runs
        against that new identity's blob, destroying it, and its
        eventual `finalize_purge` call correctly detects the staleness
        but cannot undo the deletion that already happened. Concrete
        implementations must raise (never silently no-op returning a
        record, which could mislead a caller into believing this call
        succeeded) when an exact active claim exists for this
        `(tenant_id, ingestion_id)`, regardless of which caller
        (`begin_purge` or this method) is invoked first for a given
        record -- see the reference implementation's own docstring for
        the exact exception raised.
        """
        raise NotImplementedError

    @abstractmethod
    def begin_purge(self, tenant_id: str, ingestion_id: str, at: dt.datetime) -> PurgeClaim | None:
        """**Phase 4D second correction pass, item 1; hardened further by
        the purge-claim hardening pass.** The single atomic "is it safe
        to physically delete this record's blob right now" check --
        always called immediately before `ReportBlobStore.delete`, never
        as an earlier, separate read (e.g. `get_any_status`) whose result
        is cached and acted upon later, which is exactly the gap that let
        a stale purge attempt physically delete a *different*, newer
        identity's blob after a tombstone expired and its
        `(tenant_id, ingestion_id)` key was reused (reproduced and fixed
        by the second correction pass -- see `lifecycle.py`).

        **`at` -- the proposed deletion timestamp -- is now taken and
        fully validated here, atomically with claim acquisition, rather
        than later at `finalize_purge` time.** Concrete implementations
        must construct and validate the *complete* eventual `deleted`
        candidate record (and its `Tombstone`) here -- raising whatever
        `at`'s own timezone-awareness check or the candidate's own
        validation raises (e.g. `at` not timezone-aware, or `at`
        preceding the record's `retired_at`) **before granting any
        claim** -- so that a timestamp/candidate-validation failure can
        never leave a claim dangling for a caller to physically delete a
        blob for a purge that could never actually complete. This is the
        "stronger protocol" the hardening pass adopted specifically to
        close that window: with this validation moved here,
        `finalize_purge` no longer needs (and no longer accepts) an `at`
        parameter at all -- it only re-verifies this exact claim is still
        the active one and commits the already-validated candidate.

        Raises `ValueError` if the record is currently `received` (never
        returns for this case -- mirrors `mark_purged`'s own
        precondition, now enforced before any store access whatsoever),
        or if `at`/the resulting candidate fails validation as described
        above. Returns `None` if unknown, belonging to a different
        tenant, its tombstone has already expired, or the record is
        already `deleted` -- in every one of these cases there is
        nothing left for the caller to physically delete, and the caller
        must not call `ReportBlobStore.delete` at all (an
        already-`deleted` record's own idempotent re-purge must never
        touch the blob store, since by then that exact key may already
        belong to an entirely different, newer identity).

        Returns a `PurgeClaim` -- bound to this record's current internal
        generation **and** to a fresh, globally-unique `claim_id` --
        only when the record is genuinely `retired` and every validation
        above passed. The caller must physically delete the blob next,
        then pass this exact claim to `finalize_purge`.

        **Exclusive, and bound to a unique acquisition, not merely a
        generation**: at most one caller is ever granted a claim for a
        given `(tenant_id, ingestion_id)` at a time -- a second,
        concurrent `begin_purge` call while a claim is already active
        returns `None`, the same as if nothing were eligible, and
        therefore never calls `ReportBlobStore.delete` at all. Crucially,
        each granted claim also carries a unique `claim_id`: releasing an
        *earlier* claim (e.g. one already superseded by a later
        acquisition for the same still-unchanged generation) must never
        be able to cancel a *different*, currently active claim, and a
        released or superseded claim must never be able to
        `finalize_purge` successfully -- see `release_purge_claim`'s and
        `finalize_purge`'s own docstrings for the exact "exact claim"
        comparison this requires (`generation` **and** `claim_id`
        together, never `generation` alone). This is what makes two
        genuinely concurrent purgers racing the *same* `retired` record
        safe even when `ReportBlobStore.delete` itself is slow (e.g. real
        network I/O in a future production backend): only one caller's
        delete call for a given active claim ever happens, so there is no
        second, independently-delayed delete call left that could later
        target a since-reused identity's blob.
        """
        raise NotImplementedError

    @abstractmethod
    def release_purge_claim(self, claim: PurgeClaim) -> None:
        """Releases `claim` **only if it is still the exact currently
        active claim** for its `(tenant_id, ingestion_id)` -- compared on
        **both** `generation` and `claim_id`, never `generation` alone
        (see `PurgeClaim`'s own docstring for why a generation-only
        comparison is insufficient: releasing an old, already-superseded
        claim by generation alone could incorrectly cancel a different,
        currently active claim for the same generation -- an ABA
        problem). Never completes the purge itself -- the caller's own
        physical blob deletion (or some later step) failed, so a later
        retry (by this caller or another) must be allowed to re-acquire a
        claim and try again, rather than finding this record permanently
        marked "claimed" by a caller that will never finish.

        Repeated release of the same, already-released (or already
        superseded, or already finalized) claim is always a no-op --
        never raises, and never affects whatever claim (if any) is
        currently active. Releasing a stale, fabricated, or
        generation-only-equivalent claim (one that does not exactly
        match the currently active claim's `claim_id`) is likewise
        always a no-op.
        """
        raise NotImplementedError

    @abstractmethod
    def finalize_purge(self, claim: PurgeClaim) -> IngestionRecord | None:
        """Atomically re-verifies `claim` is still the exact currently
        active claim for its `(tenant_id, ingestion_id)` -- compared on
        **both** `generation` and `claim_id`, never `generation` alone --
        and, if so, commits the exact `deleted` candidate record (and its
        `Tombstone`) `begin_purge` already validated and captured at
        claim-acquisition time (identical outcome to `mark_purged`,
        minus its own timestamp/candidate construction, which already
        happened). `at` is no longer a parameter here -- it was already
        taken, validated, and baked into the claim by `begin_purge`,
        specifically so this step, reached only after the caller's own
        physical blob deletion has already succeeded, has nothing left to
        validate and can fail only if the claim itself is no longer
        current.

        If `claim` is not the exact currently active claim -- because it
        was already released, already finalized once before (a claim is
        consumed exactly once by a successful `finalize_purge`), already
        superseded by a later acquisition for the same still-current
        generation, is fabricated, or its generation is no longer current
        (e.g. a full retire -> purge -> tombstone-expire -> reuse cycle
        completed since `claim` was issued) -- returns `None` **without
        mutating anything and without releasing or otherwise touching
        whatever claim, if any, is currently active**: the metadata layer
        itself is never corrupted into claiming the wrong acquisition
        `deleted`, even though nothing at this layer alone can
        retroactively undo a physical blob deletion the caller may
        already have performed against a since-superseded identity
        (closing that residual, cross-store gap completely would require
        a transaction spanning both stores, which this architecture
        deliberately does not have, §H) -- see `lifecycle.py` for how the
        caller is structured to keep that window as small as physically
        possible.
        """
        raise NotImplementedError

    @abstractmethod
    def get_tombstone(self, tenant_id: str, ingestion_id: str) -> Tombstone | None:
        """`None` both when never existed and once tombstone retention has
        elapsed.
        """
        raise NotImplementedError

    @abstractmethod
    def list_expired_for_retention_sweep(
        self, older_than: dt.datetime
    ) -> Iterable[IngestionRecord]:
        """`received` records whose `received_at` predates `older_than`,
        in deterministic order. Consumed by a future retention-sweep
        *caller* this package does not implement (§I) -- this method only
        returns candidates; it starts no thread, scheduler, cron job, or
        queue itself.
        """
        raise NotImplementedError


class ReportBlobStore(ABC):
    """§H's `ReportBlobStore` interface. `storage_key` must always come
    from `storage_keys.derive_storage_key` -- never a caller-supplied
    filename or a report field.

    **`put_if_absent` (Phase 4D correction)**: `put` alone cannot safely
    back a "reserve a brand-new key" write path, because it always
    overwrites -- a caller that generates a fresh `ingestion_id` and then
    calls plain `put` has no way to distinguish "this key was genuinely
    free" from "this key already held someone else's bytes, which I just
    silently destroyed." `POST /api/v1/reports` (Phase 4D) exclusively
    uses `put_if_absent` for exactly this reason: reserving a new report's
    bytes must never be able to overwrite existing content, even under a
    generated-ID collision or a concurrent duplicate request. `put`
    itself is preserved, unchanged, only because Phase 4B's own
    already-approved reference implementation and test suite
    (`InMemoryReportBlobStore`, `tests/test_ingestion_blob_store.py`)
    already treat it as part of this interface's contract -- no Phase 4D
    code path calls it.
    """

    @abstractmethod
    def put(self, storage_key: str, data: bytes) -> None:
        raise NotImplementedError

    @abstractmethod
    def put_if_absent(self, storage_key: str, data: bytes) -> bool:
        """Atomically: if `storage_key` does not currently hold any
        bytes, stores `data` under it and returns `True`; if it already
        holds bytes (from any caller, at any earlier time), does **not**
        overwrite them and returns `False`. Concurrency-safe: under two
        simultaneous calls for the same `storage_key`, exactly one
        returns `True` and stores its bytes; the other returns `False`
        and never touches the store.
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, storage_key: str) -> bytes | None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, storage_key: str) -> None:
        """Repeated deletion of the same key is always safe (never
        raises).
        """
        raise NotImplementedError


class SecretVerifier(Protocol):
    """The authentication-boundary verifier §F/§H describe as
    `TokenStore.verify_secret` in the milestone document -- declared here
    as an **injectable callable boundary**. Phase 4B provides no concrete
    production implementation of this protocol: not Argon2id (a new
    dependency this phase does not add), and never a plaintext comparison
    presented as a production implementation. A `TokenStore` delegates its
    `verify_secret` method to an injected `SecretVerifier` (see
    `InMemoryTokenStore.__init__` in `reference.py`) so a future Phase 4C
    can inject a real Argon2id-backed verifier, and a test can inject a
    deterministic fake, at the exact same call site.
    """

    def __call__(self, presented_secret: str, secret_hash: str) -> bool: ...


class TokenStore(ABC):
    """Storage mechanics for `TokenRecord` (§H) -- the complete,
    approved three-method interface: `lookup`, `verify_secret`, and
    `mark_revoked`. `verify_secret` is present with its exact approved
    signature, but Phase 4B's own reference implementation
    (`InMemoryTokenStore`) never performs real cryptographic
    verification itself -- it delegates to an injected `SecretVerifier`
    (see above). Real Argon2id-backed verification is Phase 4C work;
    Phase 4B ships the complete interface and a reference implementation
    that is exhaustively testable via a deterministic fake verifier,
    never a plaintext comparison and never a placeholder that could be
    mistaken for production verification.
    """

    @abstractmethod
    def lookup(self, lookup_id: str) -> TokenRecord | None:
        raise NotImplementedError

    @abstractmethod
    def verify_secret(self, presented_secret: str, secret_hash: str) -> bool:
        """A pure, stateless verification of `presented_secret` against
        `secret_hash` (§H). Phase 4B's reference implementation
        delegates this entirely to an injected `SecretVerifier` -- it
        contains no hashing or comparison logic of its own.
        """
        raise NotImplementedError

    @abstractmethod
    def mark_revoked(self, lookup_id: str) -> None:
        """A no-op if `lookup_id` is unknown. Visible on the very next
        `lookup` call.
        """
        raise NotImplementedError


class AttemptLimiter(ABC):
    """§H's `AttemptLimiter` interface, exactly -- a generic primitive
    keyed by an opaque scope string (`"lookup_id:<...>"` for Layer 1,
    `"source:<...>"` for Layer 2, §F). Phase 4B provides no three-layer
    decision flow, no production threshold, and no header-trust logic --
    only this primitive and a minimal deterministic reference
    implementation.
    """

    @abstractmethod
    def record_failure(self, scope_key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_blocked(self, scope_key: str) -> bool:
        raise NotImplementedError


class RequestRateLimiter(ABC):
    """A generic, concurrency-safe **ordinary-request-volume** limiter --
    introduced by Phase 4D, deliberately separate from `AttemptLimiter`.

    `AttemptLimiter.record_failure` means exactly "a failure happened"
    (a wrong secret, a Layer 1/2 block) -- it must never be called to
    count an ordinary *successful* request, which is not a failure by any
    reading of that name. Phase 4C's own `AuthenticationCoordinator`
    illustrated the resulting gap directly: its "Layer 3" check read
    `AttemptLimiter.is_blocked` but had nothing that could ever legitimately
    call `record_failure` for a successful request, so that check could
    never actually become true in practice. `RequestRateLimiter` is the
    truthful replacement, used for both of Phase 4D's ordinary-request-
    volume throttles: the unauthenticated capabilities endpoint (source-
    scoped, `abuse_protection.source_scope_key`) and the per-authenticated-
    token budget the (renamed) `AuthenticationCoordinator.authenticate`
    Layer 3 step enforces (`abuse_protection.token_scope_key`) -- checked
    only *after* authentication succeeds.

    A single method, deliberately atomic (never a separate `is_blocked`
    read followed by a separate increment, which cannot guarantee
    correctness under concurrent requests for the same scope key -- the
    same reasoning `MetadataStore.create_or_get_received`, §H, already
    established for this package).
    """

    @abstractmethod
    def check_and_record_request(self, scope_key: str) -> bool:
        """Atomically checks whether `scope_key` is currently within its
        configured request budget and, if so, counts this request against
        it in the same atomic step. Returns `True` if the request is
        allowed (and has now been counted); returns `False` if the budget
        is already exhausted -- a rejected request is never itself
        counted, so it does not further starve a caller that is already
        over budget.
        """
        raise NotImplementedError
