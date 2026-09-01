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
from .interfaces import AttemptLimiter, MetadataStore, ReportBlobStore, SecretVerifier, TokenStore
from .models import IngestionRecord, IngestionStatus, RetirementReason, TokenRecord, Tombstone

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
