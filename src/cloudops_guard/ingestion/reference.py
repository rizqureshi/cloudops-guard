"""Local, in-memory reference implementations of the Phase 4A storage
interfaces (`interfaces.py`) -- **not production storage**. Data held here
never survives process exit, is never encrypted, replicated, or backed up,
and is not durable in any sense a real customer's data would need to be.
These exist so the interfaces are exhaustively unit-testable now, and so a
future phase has a known-correct behavioral reference to implement a real
backing store against -- not so this code can be deployed anywhere.

No network client, HTTP framework, database driver, cloud SDK, secret
manager, or background scheduler is imported or started by this module.
See `interfaces.py`'s module docstring for the cross-store boundary this
package deliberately does not paper over (no ingestion coordinator, no
cross-store transaction, no `pending` status).
"""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .errors import IdempotencyKeyConflict, IngestionIdConflict
from .interfaces import (
    AttemptLimiter,
    MetadataStore,
    ReportBlobStore,
    RequestRateLimiter,
    SecretVerifier,
    TokenStore,
)
from .models import (
    IngestionRecord,
    IngestionStatus,
    PurgeClaim,
    RetirementReason,
    TokenRecord,
    Tombstone,
)

# Fixed, non-sliding, per §E's idempotency semantics -- never a constructor
# parameter (a real "24 hours" is not something callers should be able to
# quietly shrink or extend; only the injected clock varies in tests, never
# this duration).
IDEMPOTENCY_KEY_WINDOW = dt.timedelta(hours=24)

# §C's proposed default -- explicitly documented there as "configurable per
# pilot agreement," unlike the idempotency window above, so this one *is* a
# constructor parameter.
DEFAULT_TOMBSTONE_RETENTION = dt.timedelta(days=90)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _require_timezone_aware(value: dt.datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class _ActivePurgeClaim:
    """Internal-only (never part of any public interface): everything
    `InMemoryMetadataStore` needs to remember about the currently active
    purge claim for one `(tenant_id, ingestion_id)` key. `generation`
    and `claim_id` together are this store's own definition of an "exact
    claim" -- `generation` alone is insufficient (see `PurgeClaim`'s
    docstring). `purged_record`/`tombstone` are the exact, already-
    validated candidates `begin_purge` built and validated atomically
    with claim acquisition; `finalize_purge` commits them verbatim,
    unchanged, rather than reconstructing or re-validating anything.
    """

    generation: int
    claim_id: int
    purged_record: IngestionRecord
    tombstone: Tombstone


@dataclass(frozen=True, slots=True)
class _KeyBinding:
    """Internal-only: which ingestion an `idempotency_key` currently
    points at, and the window it was bound within. Never exposed outside
    this module.
    """

    ingestion_id: str
    received_at: dt.datetime


class InMemoryMetadataStore(MetadataStore):
    """A local, in-memory reference `MetadataStore`.

    Thread-safe: a single `threading.Lock` guards every operation that
    reads-then-writes shared state, held for the operation's *entire*
    duration -- `create_or_get_received` in particular is never
    implemented as a separately-locked lookup followed by a
    separately-locked write (§4.1's explicit requirement); the whole
    three-step algorithm runs under one lock acquisition.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], dt.datetime] | None = None,
        tombstone_retention: dt.timedelta = DEFAULT_TOMBSTONE_RETENTION,
    ) -> None:
        self._clock = clock if clock is not None else _utc_now
        self._tombstone_retention = tombstone_retention
        self._lock = threading.Lock()

        self._records: dict[tuple[str, str], IngestionRecord] = {}
        self._tombstones: dict[tuple[str, str], Tombstone] = {}
        # (tenant_id, ingestion_id) -> a monotonically-increasing
        # generation number, assigned fresh every time create_or_get_received's
        # own step 3 actually creates a NEW record under that exact key --
        # including a tombstone-expiry-then-reuse cycle, which §E.4
        # otherwise makes indistinguishable from "this key was always
        # this identity." `begin_purge`/`finalize_purge` (Phase 4D second
        # correction pass, item 1) use this purely as an internal,
        # never-customer-visible optimistic-concurrency check -- a global
        # counter (not per-key) is sufficient and simpler, since all that
        # matters is that reuse always produces a strictly higher number
        # than whatever a stale claim captured.
        self._generations: dict[tuple[str, str], int] = {}
        self._next_generation = 0
        # (tenant_id, ingestion_id) -> the EXCLUSIVE, in-progress purge
        # claim currently active for that key (`begin_purge`/
        # `release_purge_claim`/`finalize_purge`/`mark_purged`) -- at
        # most one caller is ever granted the right to physically delete
        # a given key's blob at a time, which is what makes two
        # concurrent purgers racing the same record safe even under an
        # arbitrarily slow `ReportBlobStore.delete` call (see
        # `begin_purge`'s own docstring). Stores a full `_ActivePurgeClaim`
        # (generation + a globally-unique claim_id + the already-
        # validated delete candidates), never just a generation number --
        # a generation-only comparison cannot distinguish two separate
        # acquisitions against the same, unchanged generation (purge-claim
        # hardening pass, item 1).
        self._active_purge_claims: dict[tuple[str, str], _ActivePurgeClaim] = {}
        # A globally-unique counter, never reused, handed out as
        # PurgeClaim.claim_id on every successful begin_purge acquisition
        # -- see _ActivePurgeClaim's own docstring for why generation
        # alone cannot serve this purpose.
        self._next_claim_id = 0
        # (tenant_id, report_fingerprint) -> ingestion_id of the currently
        # active "received" record, if any. A retired record is removed
        # from this index immediately (mark_retired), so a later
        # create_or_get_received for the same fingerprint correctly starts
        # a genuinely new record rather than finding a stale one.
        self._active_fingerprints: dict[tuple[str, str], str] = {}
        # (tenant_id, idempotency_key) -> the binding's target and anchor
        # time. Deliberately *not* proactively pruned when a bound record
        # retires: create_or_get_received re-checks the bound record's
        # live status on every lookup (see "still_active" below), so a
        # stale entry left here is simply inert, never incorrectly reused.
        self._idempotency_bindings: dict[tuple[str, str], _KeyBinding] = {}

    # -- MetadataStore -----------------------------------------------------

    def create_or_get_received(
        self,
        tenant_id: str,
        report_fingerprint: str,
        new_ingestion_id: str,
        new_record: IngestionRecord,
        idempotency_key: str | None = None,
    ) -> tuple[IngestionRecord, bool]:
        if new_record.tenant_id != tenant_id:
            raise ValueError("new_record.tenant_id must match the given tenant_id.")
        if new_record.report_fingerprint != report_fingerprint:
            raise ValueError(
                "new_record.report_fingerprint must match the given report_fingerprint."
            )
        if new_record.ingestion_id != new_ingestion_id:
            raise ValueError("new_record.ingestion_id must match new_ingestion_id.")
        if new_record.status is not IngestionStatus.RECEIVED:
            raise ValueError("new_record must have status=IngestionStatus.RECEIVED.")

        # The entire three-step algorithm below runs under one lock
        # acquisition -- never a separately-locked lookup followed by a
        # separately-locked write.
        with self._lock:
            now = self._clock()

            # Step 1: idempotency-key check, if a key was supplied.
            if idempotency_key is not None:
                key_key = (tenant_id, idempotency_key)
                binding = self._idempotency_bindings.get(key_key)
                if binding is not None:
                    bound_record = self._records.get((tenant_id, binding.ingestion_id))
                    still_active = (
                        bound_record is not None
                        and bound_record.status is IngestionStatus.RECEIVED
                        and now <= binding.received_at + IDEMPOTENCY_KEY_WINDOW
                    )
                    if still_active:
                        if bound_record.report_fingerprint == report_fingerprint:
                            return bound_record, False
                        raise IdempotencyKeyConflict(
                            "idempotency_key is already bound to a different "
                            "report_fingerprint for this tenant."
                        )
                    # Inactive binding (window elapsed, or the bound
                    # record has since retired/deleted): fall through --
                    # this request is evaluated fresh, on its own content.

            # Step 2: content-based dedup against any currently active
            # "received" record for this exact (tenant_id,
            # report_fingerprint) pair.
            fingerprint_key = (tenant_id, report_fingerprint)
            existing_ingestion_id = self._active_fingerprints.get(fingerprint_key)
            if existing_ingestion_id is not None:
                existing_record = self._records[(tenant_id, existing_ingestion_id)]
                if idempotency_key is not None:
                    self._idempotency_bindings[(tenant_id, idempotency_key)] = _KeyBinding(
                        ingestion_id=existing_record.ingestion_id,
                        received_at=existing_record.received_at,
                    )
                return existing_record, False

            # Step 3: create. Only reached when neither step 1 nor step 2
            # resolved this call to an existing record -- so if
            # new_ingestion_id already identifies *any* record for this
            # tenant (live, retired, or deleted-but-still-tombstoned),
            # that is a genuine collision against a different identity,
            # never a legitimate replay. Reject it before touching any
            # store state, rather than silently overwriting the existing
            # record and leaving _active_fingerprints/_idempotency_bindings
            # pointing at a record with the wrong report_fingerprint.
            self._expire_tombstone_if_needed(tenant_id, new_ingestion_id)
            if (tenant_id, new_ingestion_id) in self._records:
                raise IngestionIdConflict(
                    f"ingestion_id {new_ingestion_id!r} already identifies a different "
                    f"record for this tenant."
                )

            self._records[(tenant_id, new_ingestion_id)] = new_record
            # A fresh generation for this exact key, every time -- this
            # is what lets a stale purge claim (see begin_purge/
            # finalize_purge) detect that its key has since been reused
            # by a genuinely different identity, even after a full
            # tombstone-expiry-then-reuse cycle.
            self._next_generation += 1
            self._generations[(tenant_id, new_ingestion_id)] = self._next_generation
            self._active_fingerprints[fingerprint_key] = new_ingestion_id
            if idempotency_key is not None:
                self._idempotency_bindings[(tenant_id, idempotency_key)] = _KeyBinding(
                    ingestion_id=new_ingestion_id, received_at=new_record.received_at
                )
            return new_record, True

    def get(self, tenant_id: str, ingestion_id: str) -> IngestionRecord | None:
        with self._lock:
            self._expire_tombstone_if_needed(tenant_id, ingestion_id)
            record = self._records.get((tenant_id, ingestion_id))
            if record is None or record.status is not IngestionStatus.RECEIVED:
                return None
            return record

    def get_any_status(self, tenant_id: str, ingestion_id: str) -> IngestionRecord | None:
        with self._lock:
            self._expire_tombstone_if_needed(tenant_id, ingestion_id)
            # Unlike `get`, no status filter -- the whole point is to let
            # a lifecycle-internal caller (`lifecycle.purge_retired_ingestion`)
            # see a `received` record too, so it can refuse to touch a
            # blob that is still live, before ever calling
            # ReportBlobStore.delete.
            return self._records.get((tenant_id, ingestion_id))

    def mark_retired(
        self, tenant_id: str, ingestion_id: str, at: dt.datetime, reason: RetirementReason
    ) -> IngestionRecord | None:
        _require_timezone_aware(at, "at")
        with self._lock:
            self._expire_tombstone_if_needed(tenant_id, ingestion_id)
            key = (tenant_id, ingestion_id)
            record = self._records.get(key)
            if record is None:
                return None
            if record.status is not IngestionStatus.RECEIVED:
                # Idempotent: already retired (for either reason) or
                # deleted -- return the existing record UNCHANGED, never
                # overwriting its true original reason/retired_at.
                return record

            # Construct and validate the complete candidate record BEFORE
            # mutating any store state. `model_copy(update=...)` never
            # validates -- constructing via the normal constructor runs
            # IngestionRecord's own model_validator (status/timestamp
            # invariants, e.g. retired_at >= received_at) and its
            # field_validator (rejects an invalid `reason`), so a rejected
            # candidate raises here, before any dict is touched, leaving
            # the record unchanged and still `received`.
            retired_record = IngestionRecord(
                tenant_id=record.tenant_id,
                ingestion_id=record.ingestion_id,
                report_fingerprint=record.report_fingerprint,
                received_at=record.received_at,
                status=IngestionStatus.RETIRED,
                reason=reason,
                retired_at=at,
                deleted_at=None,
            )
            self._records[key] = retired_record

            fingerprint_key = (tenant_id, record.report_fingerprint)
            if self._active_fingerprints.get(fingerprint_key) == ingestion_id:
                del self._active_fingerprints[fingerprint_key]

            return retired_record

    def mark_purged(
        self, tenant_id: str, ingestion_id: str, at: dt.datetime
    ) -> IngestionRecord | None:
        _require_timezone_aware(at, "at")
        with self._lock:
            self._expire_tombstone_if_needed(tenant_id, ingestion_id)
            key = (tenant_id, ingestion_id)
            record = self._records.get(key)
            if record is None:
                return None
            if record.status is IngestionStatus.DELETED:
                return record  # idempotent
            if record.status is not IngestionStatus.RETIRED:
                raise ValueError("mark_purged requires an already-retired record.")

            # Purge-claim hardening pass, item 2: refuse to run while an
            # exclusive purge claim is active for this exact key --
            # checked under the SAME lock as everything else in this
            # method, so there is no window between this check and the
            # mutation below where a claim could be acquired or released
            # out from under it. Without this, this legacy method could
            # bypass the claim protocol entirely: a caller holding an
            # active claim (about to physically delete this record's
            # blob, on its own schedule) has no way to know this method
            # just moved the record to `deleted` -- and by the time that
            # claim holder's own delayed blob deletion runs, the
            # tombstone this call just created may have already expired
            # and this exact key been reused by a genuinely new
            # `received` record, so the claim holder's delete call would
            # destroy the NEW identity's live blob (reproduced exactly
            # this way before this fix). Raising here, rather than
            # silently no-op'ing, makes the conflict loud and immediate
            # rather than deferring it to a much harder-to-diagnose
            # failure later.
            if key in self._active_purge_claims:
                raise ValueError(
                    "mark_purged cannot run while an exclusive purge claim is "
                    "active for this record; use begin_purge/finalize_purge instead."
                )

            # Construct and validate BOTH the candidate deleted record and
            # its Tombstone before committing either mutation. Building
            # `purged_record` via the normal constructor (never
            # `model_copy(update=...)`, which skips validation) enforces
            # IngestionRecord's own status/timestamp invariants (e.g.
            # deleted_at >= retired_at); Tombstone's constructor enforces
            # the same ordering independently. Only once both candidates
            # exist and are valid do we touch `_records`/`_tombstones` --
            # so a rejected candidate can never leave a deleted record
            # without its tombstone, or vice versa.
            purged_record = IngestionRecord(
                tenant_id=record.tenant_id,
                ingestion_id=record.ingestion_id,
                report_fingerprint=record.report_fingerprint,
                received_at=record.received_at,
                status=IngestionStatus.DELETED,
                reason=record.reason,
                retired_at=record.retired_at,
                deleted_at=at,
            )
            tombstone = Tombstone(
                tenant_id=tenant_id,
                ingestion_id=ingestion_id,
                reason=purged_record.reason,  # type: ignore[arg-type]  # non-None: status is DELETED
                retired_at=purged_record.retired_at,  # type: ignore[arg-type]
                deleted_at=at,
            )

            self._records[key] = purged_record
            self._tombstones[key] = tombstone
            return purged_record

    def begin_purge(self, tenant_id: str, ingestion_id: str, at: dt.datetime) -> PurgeClaim | None:
        # Purge-claim hardening pass, item 3: `at` is validated, and the
        # complete eventual `deleted` candidate (and its Tombstone) is
        # constructed and validated, HERE -- atomically with claim
        # acquisition, under this same lock -- rather than later at
        # finalize_purge time. A timestamp/candidate validation failure
        # (not timezone-aware, or `at` precedes `retired_at`) therefore
        # always raises BEFORE any claim is granted, so it can never leave
        # a claim dangling -- there is nothing for a caller to release,
        # because nothing was ever acquired.
        _require_timezone_aware(at, "at")
        with self._lock:
            self._expire_tombstone_if_needed(tenant_id, ingestion_id)
            key = (tenant_id, ingestion_id)
            record = self._records.get(key)
            if record is None:
                return None
            if record.status is IngestionStatus.RECEIVED:
                raise ValueError("begin_purge requires an already-retired record.")
            if record.status is IngestionStatus.DELETED:
                # Nothing left to physically delete -- the caller must
                # not touch ReportBlobStore at all for this case (this
                # exact key may already belong to an entirely different,
                # newer identity by now).
                return None
            # EXCLUSIVE: only one caller may hold an active claim for
            # this key at a time -- a second, concurrent begin_purge call
            # while a claim is already active gets None here, and
            # therefore never calls ReportBlobStore.delete at all. This
            # is what closes the "two purgers, one's tombstone expires
            # and the key is reused before the other physically deletes"
            # race completely: only one caller ever performs the physical
            # deletion for a given claim, so there is no second,
            # independently-delayed `ReportBlobStore.delete` call left
            # that could target a since-reused identity's blob.
            if key in self._active_purge_claims:
                return None

            # Build and validate the complete delete candidate now --
            # IngestionRecord's/Tombstone's own constructors raise
            # (uncaught, propagating straight out of this method) if `at`
            # precedes `retired_at`. Reached only after every eligibility
            # check above passed, so a rejected candidate here never
            # leaves any store state mutated.
            purged_record = IngestionRecord(
                tenant_id=record.tenant_id,
                ingestion_id=record.ingestion_id,
                report_fingerprint=record.report_fingerprint,
                received_at=record.received_at,
                status=IngestionStatus.DELETED,
                reason=record.reason,
                retired_at=record.retired_at,
                deleted_at=at,
            )
            tombstone = Tombstone(
                tenant_id=tenant_id,
                ingestion_id=ingestion_id,
                reason=purged_record.reason,  # type: ignore[arg-type]  # non-None: status is DELETED
                retired_at=purged_record.retired_at,  # type: ignore[arg-type]
                deleted_at=at,
            )

            generation = self._generations.get(key, 0)
            self._next_claim_id += 1
            claim_id = self._next_claim_id
            self._active_purge_claims[key] = _ActivePurgeClaim(
                generation=generation,
                claim_id=claim_id,
                purged_record=purged_record,
                tombstone=tombstone,
            )
            return PurgeClaim(
                tenant_id=tenant_id,
                ingestion_id=ingestion_id,
                generation=generation,
                claim_id=claim_id,
            )

    def release_purge_claim(self, claim: PurgeClaim) -> None:
        """Releases `claim` **only if it is still the exact currently
        active claim** -- compared on both `generation` and `claim_id`
        (purge-claim hardening pass, item 1). Repeated release of an
        already-released, already-superseded, or already-finalized claim
        is always a safe no-op, and never affects whatever claim (if
        any) is currently active -- this is exactly what closes the
        reproduced "A released, B acquired, A released again cancels B"
        bug: A's second release call now compares its own `claim_id`
        against whatever is currently active (B's), finds no match, and
        does nothing.
        """
        with self._lock:
            key = (claim.tenant_id, claim.ingestion_id)
            active = self._active_purge_claims.get(key)
            if (
                active is not None
                and active.generation == claim.generation
                and active.claim_id == claim.claim_id
            ):
                del self._active_purge_claims[key]

    def finalize_purge(self, claim: PurgeClaim) -> IngestionRecord | None:
        with self._lock:
            key = (claim.tenant_id, claim.ingestion_id)
            self._expire_tombstone_if_needed(*key)
            active = self._active_purge_claims.get(key)
            if (
                active is None
                or active.generation != claim.generation
                or active.claim_id != claim.claim_id
            ):
                # Not the exact currently active claim: released,
                # already finalized once before, superseded by a later
                # acquisition for the same still-current generation (the
                # ABA case `claim_id` exists specifically to catch),
                # fabricated, or its generation is simply no longer
                # current. Never mutate metadata, and never touch
                # whatever claim (if any) IS currently active -- a stale
                # finalize call must be a complete no-op with respect to
                # any other claim.
                return None

            # `active` IS this exact claim -- commit the already-
            # validated candidates begin_purge built and captured at
            # acquisition time, verbatim. Nothing here can fail: `at`
            # and the candidates were already fully validated before any
            # claim was ever granted.
            self._records[key] = active.purged_record
            self._tombstones[key] = active.tombstone
            del self._active_purge_claims[key]
            return active.purged_record

    def get_tombstone(self, tenant_id: str, ingestion_id: str) -> Tombstone | None:
        with self._lock:
            self._expire_tombstone_if_needed(tenant_id, ingestion_id)
            return self._tombstones.get((tenant_id, ingestion_id))

    def list_expired_for_retention_sweep(
        self, older_than: dt.datetime
    ) -> Iterable[IngestionRecord]:
        _require_timezone_aware(older_than, "older_than")
        with self._lock:
            candidates = [
                record
                for record in self._records.values()
                if record.status is IngestionStatus.RECEIVED and record.received_at < older_than
            ]
        # Sorted for deterministic results independent of dict insertion
        # order (already deterministic in CPython, but sorting makes the
        # guarantee explicit and language-implementation-independent).
        candidates.sort(key=lambda r: (r.received_at, r.tenant_id, r.ingestion_id))
        return candidates

    # -- internal ------------------------------------------------------

    def _expire_tombstone_if_needed(self, tenant_id: str, ingestion_id: str) -> None:
        """Must be called with `self._lock` already held. Lazily expires a
        tombstone whose retention window has elapsed -- Phase 4B has no
        background sweep, so expiry is checked on every access instead.
        """
        key = (tenant_id, ingestion_id)
        tombstone = self._tombstones.get(key)
        if tombstone is None:
            return
        if self._clock() - tombstone.deleted_at > self._tombstone_retention:
            del self._tombstones[key]
            self._records.pop(key, None)


class InMemoryReportBlobStore(ReportBlobStore):
    """A local, in-memory reference `ReportBlobStore` -- not a production
    object store. See this module's own docstring for what that means.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._blobs: dict[str, bytes] = {}

    def put(self, storage_key: str, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes.")
        with self._lock:
            # `bytes(...)` always yields an immutable object decoupled
            # from any mutable `bytearray` the caller might still hold and
            # later mutate -- stored/returned bytes never expose mutable
            # internal state.
            self._blobs[storage_key] = bytes(data)

    def put_if_absent(self, storage_key: str, data: bytes) -> bool:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes.")
        with self._lock:
            # The existence check and the write happen under the same
            # lock acquisition -- never a separate `get`-then-`put` pair,
            # which could not guarantee correctness under two concurrent
            # calls for the same storage_key (the same reasoning
            # `create_or_get_received`, above, already applies here).
            if storage_key in self._blobs:
                return False
            self._blobs[storage_key] = bytes(data)
            return True

    def get(self, storage_key: str) -> bytes | None:
        with self._lock:
            stored = self._blobs.get(storage_key)
            return stored  # `bytes` is itself immutable; no copy needed

    def delete(self, storage_key: str) -> None:
        with self._lock:
            self._blobs.pop(storage_key, None)  # repeated deletion is always safe


class InMemoryTokenStore(TokenStore):
    """A local, in-memory reference `TokenStore` implementing the
    complete, approved three-method interface. `lookup` and
    `mark_revoked` are plain storage mechanics; `verify_secret` delegates
    entirely to an injected `SecretVerifier` -- this class never hashes,
    compares, or otherwise verifies a secret itself. `secret_verifier` is
    a required constructor argument (never defaulted) precisely so this
    class can never fall back to an implicit, unsafe comparison: a caller
    must always supply the verification logic explicitly, whether a real
    Argon2id-backed verifier (Phase 4C) or a deterministic test fake.
    """

    def __init__(self, secret_verifier: SecretVerifier) -> None:
        self._secret_verifier = secret_verifier
        self._lock = threading.Lock()
        self._tokens: dict[str, TokenRecord] = {}

    def register_for_testing(self, record: TokenRecord) -> None:
        """Reference/test-only seeding hook -- **not** part of the
        `TokenStore` interface. A real Phase 4C provisioning flow inserts
        records through its own out-of-band, manual procedure (§F); this
        method exists solely so this reference implementation is testable
        without that flow existing yet.
        """
        with self._lock:
            self._tokens[record.lookup_id] = record

    def lookup(self, lookup_id: str) -> TokenRecord | None:
        with self._lock:
            return self._tokens.get(lookup_id)  # frozen model: safe to return directly

    def verify_secret(self, presented_secret: str, secret_hash: str) -> bool:
        # Pure delegation -- no hashing, comparison, or other secret
        # handling of any kind happens in this class. The injected
        # verifier is the sole source of truth for the result.
        return self._secret_verifier(presented_secret, secret_hash)

    def mark_revoked(self, lookup_id: str) -> None:
        with self._lock:
            record = self._tokens.get(lookup_id)
            if record is None:
                return
            self._tokens[lookup_id] = record.model_copy(update={"revoked": True})


class InMemoryAttemptLimiter(AttemptLimiter):
    """A minimal, deterministic reference `AttemptLimiter` -- a fixed
    failure-count threshold with no time-based decay, so behavior never
    depends on wall-clock timing. Not a rate-limiting product: no
    provider, no header-derived source trust, and no production threshold
    is selected here (§F) -- `threshold` is caller-configured,
    reference/test-only.
    """

    def __init__(self, threshold: int) -> None:
        if threshold < 1:
            raise ValueError("threshold must be at least 1.")
        self._threshold = threshold
        self._lock = threading.Lock()
        self._failure_counts: dict[str, int] = {}

    def record_failure(self, scope_key: str) -> None:
        with self._lock:
            self._failure_counts[scope_key] = self._failure_counts.get(scope_key, 0) + 1

    def is_blocked(self, scope_key: str) -> bool:
        with self._lock:
            return self._failure_counts.get(scope_key, 0) >= self._threshold

    def reset_for_testing(self, scope_key: str) -> None:
        """Reference/test-only reset hook -- **not** part of the
        `AttemptLimiter` interface (§H declares only `record_failure`/
        `is_blocked`; this reference primitive intentionally has no time
        dimension at all, so a test that wants to simulate one scope's
        failures clearing needs an explicit reset).
        """
        with self._lock:
            self._failure_counts.pop(scope_key, None)


class InMemoryRequestRateLimiter(RequestRateLimiter):
    """A minimal, deterministic reference `RequestRateLimiter` (Phase 4D)
    -- a fixed request-count budget with no time-based decay or sliding
    window, so behavior never depends on wall-clock timing. Not a
    production rate-limiting product, and no numeric production threshold
    is selected here -- `threshold` is caller-configured,
    reference/test-only, exactly like `InMemoryAttemptLimiter`.
    """

    def __init__(self, threshold: int) -> None:
        if threshold < 1:
            raise ValueError("threshold must be at least 1.")
        self._threshold = threshold
        self._lock = threading.Lock()
        self._request_counts: dict[str, int] = {}

    def check_and_record_request(self, scope_key: str) -> bool:
        with self._lock:
            # The check and the increment happen under one lock
            # acquisition -- never a separate is_blocked-then-record pair,
            # which could not guarantee the configured ceiling is never
            # exceeded by two concurrent callers racing for the same
            # scope_key.
            count = self._request_counts.get(scope_key, 0)
            if count >= self._threshold:
                return False
            self._request_counts[scope_key] = count + 1
            return True

    def reset_for_testing(self, scope_key: str) -> None:
        """Reference/test-only reset hook -- **not** part of the
        `RequestRateLimiter` interface.
        """
        with self._lock:
            self._request_counts.pop(scope_key, None)
