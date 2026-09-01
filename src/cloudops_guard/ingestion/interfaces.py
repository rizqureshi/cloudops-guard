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

from .models import IngestionRecord, RetirementReason, TokenRecord, Tombstone


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
    """§H's `ReportBlobStore` interface, exactly. `storage_key` must
    always come from `storage_keys.derive_storage_key` -- never a
    caller-supplied filename or a report field.
    """

    @abstractmethod
    def put(self, storage_key: str, data: bytes) -> None:
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
