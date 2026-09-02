"""Purge-claim hardening pass: unique-acquisition claim identity
(`PurgeClaim.claim_id`) and `mark_purged`/active-claim coordination for
`InMemoryMetadataStore`. Direct, store-level tests against
`begin_purge`/`release_purge_claim`/`finalize_purge`/`mark_purged` --
`test_ingestion_api_lifecycle.py` covers the same guarantees end to end
through `lifecycle.purge_retired_ingestion` and a real `ReportBlobStore`.

Reproduced-before-fix context (both items independently reproduced
against the second correction pass's own generation-only claim design,
recorded in detail in each test class's own docstring below):

1. `PurgeClaim` carried only `(tenant_id, ingestion_id, generation)`, and
   `_active_purge_claims` stored only the generation -- so releasing an
   *old*, already-superseded claim (by generation alone) could cancel a
   *different*, currently active claim for that same, unchanged
   generation (an ABA problem), and a released claim could still
   successfully `finalize_purge`.
2. `mark_purged` -- preserved, unused by `lifecycle.purge_retired_ingestion`,
   but still part of the same `MetadataStore` -- never checked
   `_active_purge_claims` at all, so it could transition a record to
   `deleted` while a `begin_purge` claim was still active for it,
   letting that claim's own later, delayed blob deletion destroy a
   genuinely different identity's blob once the tombstone this bypass
   created had expired and the key was reused.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cloudops_guard.ingestion.models import IngestionRecord, IngestionStatus, RetirementReason
from cloudops_guard.ingestion.reference import InMemoryMetadataStore

UTC = dt.UTC
T = dt.datetime(2026, 1, 1, tzinfo=UTC)


def _record(
    tenant_id: str, ingestion_id: str, fingerprint: str, received_at: dt.datetime = T
) -> IngestionRecord:
    return IngestionRecord(
        tenant_id=tenant_id,
        ingestion_id=ingestion_id,
        report_fingerprint=fingerprint,
        received_at=received_at,
        status=IngestionStatus.RECEIVED,
    )


def _store(clock_value: list[dt.datetime], **kwargs: object) -> InMemoryMetadataStore:
    return InMemoryMetadataStore(clock=lambda: clock_value[0], **kwargs)  # type: ignore[arg-type]


def _retired_store(clock_value: list[dt.datetime], **kwargs: object) -> InMemoryMetadataStore:
    """A store with one already-`retired` record, `tenant-a`/`ing-1`,
    ready for `begin_purge`. `clock_value[0]` is advanced past
    `retired_at` before returning, so callers can pass `clock_value[0]`
    straight through to `begin_purge` as a valid deletion timestamp
    without separately tracking the retirement time themselves.
    """
    store = _store(clock_value, **kwargs)
    store.create_or_get_received(
        "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
    )
    store.mark_retired(
        "tenant-a",
        "ing-1",
        clock_value[0] + dt.timedelta(hours=1),
        RetirementReason.CUSTOMER_REQUESTED,
    )
    clock_value[0] = clock_value[0] + dt.timedelta(hours=2)
    return store


class TestUniqueClaimAcquisition:
    """**Purge-claim hardening pass, item 1.** Reproduced before fix
    (against the pre-fix, generation-only `_active_purge_claims`):
    acquire claim A; release A; acquire claim B for the same, unchanged
    generation; release A *again* -- observed (reasoned through directly
    against the pre-fix comparison `self._active_purge_claims.get(key) ==
    claim.generation`, which is exactly what the fix below replaces):
    the second release of A, comparing only by generation, matches B's
    still-active entry and deletes it, so a third caller can now acquire
    claim C while B still believes it holds exclusivity. Also reproduced:
    `finalize_purge(A)` succeeding even after A was released (same root
    cause -- the pre-fix `finalize_purge` also compared only
    `current_generation != claim.generation`).
    """

    def test_a_release_b_acquire_a_release_again_leaves_b_active(self) -> None:
        clock = [T]
        store = _retired_store(clock)

        claim_a = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_a is not None
        store.release_purge_claim(claim_a)

        claim_b = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_b is not None
        assert claim_b.claim_id != claim_a.claim_id  # a fresh, distinct acquisition

        # The reproduced bug: releasing A a SECOND time, after B has
        # already been granted a claim for the same generation.
        store.release_purge_claim(claim_a)

        # B must still be exclusively active -- a third caller must get
        # nothing, proving A's stale second release did not cancel B.
        claim_c = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_c is None

    def test_repeated_release_of_an_old_claim_is_a_no_op(self) -> None:
        clock = [T]
        store = _retired_store(clock)

        claim_a = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_a is not None
        store.release_purge_claim(claim_a)
        # Releasing an already-released claim, repeatedly, must never
        # raise and must never affect anything.
        store.release_purge_claim(claim_a)
        store.release_purge_claim(claim_a)

        # A fresh claim can still be acquired normally.
        claim_b = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_b is not None

    def test_a_release_then_a_finalize_does_not_finalize(self) -> None:
        clock = [T]
        store = _retired_store(clock)

        claim_a = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_a is not None
        store.release_purge_claim(claim_a)

        result = store.finalize_purge(claim_a)
        assert result is None

        # The record must be untouched -- still RETIRED, no tombstone.
        record = store.get_any_status("tenant-a", "ing-1")
        assert record is not None
        assert record.status is IngestionStatus.RETIRED
        assert store.get_tombstone("tenant-a", "ing-1") is None

    def test_a_released_claim_cannot_finalize_even_with_no_successor(self) -> None:
        # Narrower variant of the above, isolating the exact named
        # scenario the task lists separately: A acquired, A released, A
        # finalized -- with no B ever acquired in between.
        clock = [T]
        store = _retired_store(clock)

        claim_a = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_a is not None
        store.release_purge_claim(claim_a)

        assert store.finalize_purge(claim_a) is None

    def test_a_superseded_by_b_cannot_finalize_but_b_still_can(self) -> None:
        clock = [T]
        store = _retired_store(clock)

        claim_a = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_a is not None
        store.release_purge_claim(claim_a)
        claim_b = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_b is not None

        # The stale claim A must not be able to finalize -- and must not
        # touch B's now-active claim in the attempt.
        assert store.finalize_purge(claim_a) is None
        record = store.get_any_status("tenant-a", "ing-1")
        assert record is not None
        assert record.status is IngestionStatus.RETIRED  # A's stale finalize had no effect

        # B, the exact currently active claim, finalizes normally.
        result = store.finalize_purge(claim_b)
        assert result is not None
        assert result.status is IngestionStatus.DELETED

    def test_finalize_succeeds_only_for_the_exact_active_claim(self) -> None:
        clock = [T]
        store = _retired_store(clock)

        claim_a = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_a is not None

        result = store.finalize_purge(claim_a)
        assert result is not None
        assert result.status is IngestionStatus.DELETED

        # The exact same claim object, reused a second time (a caller
        # bug, or a defensive double-call): must not finalize twice --
        # the claim was already consumed by its one successful use.
        assert store.finalize_purge(claim_a) is None

    def test_fabricated_claim_does_not_mutate_metadata_or_remove_another_claim(self) -> None:
        import dataclasses

        clock = [T]
        store = _retired_store(clock)

        claim_b = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_b is not None

        # A fabricated claim: correct tenant/ingestion_id/generation, but
        # a claim_id that was never actually granted by this store.
        fabricated = dataclasses.replace(claim_b, claim_id=claim_b.claim_id + 999_999)
        assert fabricated.claim_id != claim_b.claim_id

        store.release_purge_claim(fabricated)  # must be a no-op
        assert store.finalize_purge(fabricated) is None  # must not mutate anything

        # B's real, currently active claim must be completely unaffected
        # -- still able to finalize normally.
        result = store.finalize_purge(claim_b)
        assert result is not None
        assert result.status is IngestionStatus.DELETED

    def test_generation_only_equivalent_claim_is_not_treated_as_the_active_one(self) -> None:
        # A claim carrying the CORRECT generation but a WRONG claim_id --
        # exactly the shape the pre-fix, generation-only comparison could
        # not distinguish from the real, active claim.
        import dataclasses

        clock = [T]
        store = _retired_store(clock)

        real_claim = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert real_claim is not None

        generation_only_equivalent = dataclasses.replace(
            real_claim, claim_id=real_claim.claim_id + 1
        )
        assert store.finalize_purge(generation_only_equivalent) is None
        store.release_purge_claim(generation_only_equivalent)  # must not release the real claim

        # The real claim must still be exclusively active and able to
        # finalize.
        second_attempt = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert second_attempt is None  # still exclusively held by real_claim
        result = store.finalize_purge(real_claim)
        assert result is not None
        assert result.status is IngestionStatus.DELETED

    def test_only_one_active_claim_exists_per_record_at_a_time(self) -> None:
        clock = [T]
        store = _retired_store(clock)

        claim_a = store.begin_purge("tenant-a", "ing-1", clock[0])
        claim_b = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim_a is not None
        assert claim_b is None


class TestMarkPurgedCoordinatesWithActiveClaims:
    """**Purge-claim hardening pass, item 2.** Reproduced before fix:
    retire a record; acquire claim A via `begin_purge`; call the
    preserved, legacy `mark_purged` directly (nothing coordinated it
    with the active claim) -- observed (against the pre-fix
    `mark_purged`, which never consulted `_active_purge_claims` at all):
    the call succeeded, transitioning the record to `deleted` and
    creating its tombstone, while claim A was still active and its
    holder had not yet physically deleted anything. Once that tombstone
    would later expire and the same ID be reused by a genuinely new
    `received` record, claim A's own still-pending physical blob
    deletion (performed by whatever caller is legitimately driving the
    `begin_purge` -> delete -> `finalize_purge` sequence) would target
    that new identity's live blob.
    """

    def test_begin_purge_wins_first_mark_purged_cannot_transition(self) -> None:
        clock = [T]
        store = _retired_store(clock)

        claim = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim is not None

        with pytest.raises(ValueError, match="exclusive purge claim is"):
            store.mark_purged("tenant-a", "ing-1", clock[0])

        # mark_purged's own rejection must not have mutated anything --
        # still RETIRED, no tombstone, and the claim itself untouched.
        record = store.get_any_status("tenant-a", "ing-1")
        assert record is not None
        assert record.status is IngestionStatus.RETIRED
        assert store.get_tombstone("tenant-a", "ing-1") is None

        # The claim holder can still legitimately complete its own purge
        # afterward -- the rejection did not corrupt the claim.
        result = store.finalize_purge(claim)
        assert result is not None
        assert result.status is IngestionStatus.DELETED

    def test_mark_purged_wins_first_begin_purge_returns_no_claim(self) -> None:
        clock = [T]
        store = _retired_store(clock)

        purged = store.mark_purged("tenant-a", "ing-1", clock[0])
        assert purged is not None
        assert purged.status is IngestionStatus.DELETED

        # begin_purge, arriving after mark_purged already completed
        # unilaterally, correctly finds "nothing left to purge" --
        # exactly the pre-existing already-deleted branch, never a
        # claim.
        claim = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim is None

    def test_begin_purge_wins_first_ordering_never_lets_a_delayed_deletion_reach_a_reused_blob(
        self,
    ) -> None:
        # The exact reproduced sequence, driven to its conclusion: since
        # mark_purged can no longer race ahead of an active claim (proven
        # above), the dangerous state the original bug relied on --
        # "deleted" via mark_purged while a claim believes it still must
        # delete a blob later -- can no longer be reached at all. What
        # remains is the ordinary, safe path: the claim holder completes
        # its own purge normally, and reuse afterward is exactly the
        # already-proven-safe case from the second correction pass's own
        # tombstone-expiry-and-reuse tests.
        clock = [T]
        retention = dt.timedelta(days=90)
        store = _retired_store(clock, tombstone_retention=retention)

        claim = store.begin_purge("tenant-a", "ing-1", clock[0])
        assert claim is not None

        with pytest.raises(ValueError):
            store.mark_purged("tenant-a", "ing-1", clock[0])  # blocked, as proven above

        # The claim holder legitimately completes its own purge.
        result = store.finalize_purge(claim)
        assert result is not None and result.status is IngestionStatus.DELETED

        # Tombstone expires; the same ID is reused by a genuinely new
        # RECEIVED record.
        clock[0] = T + retention + dt.timedelta(days=1)
        _, created = store.create_or_get_received(
            "tenant-a", "sha256:b", "ing-1", _record("tenant-a", "ing-1", "sha256:b", clock[0])
        )
        assert created is True

        # No claim, no pending delayed deletion, and no caller holding a
        # stale reference to the old claim can do anything to the reused
        # identity: the old claim was already fully consumed by its one
        # successful finalize_purge call above.
        assert store.finalize_purge(claim) is None
        current = store.get_any_status("tenant-a", "ing-1")
        assert current is not None
        assert current.status is IngestionStatus.RECEIVED

    def test_mark_purged_wins_first_ordering_never_lets_a_delayed_deletion_reach_a_reused_blob(
        self,
    ) -> None:
        clock = [T]
        retention = dt.timedelta(days=90)
        store = _retired_store(clock, tombstone_retention=retention)

        # mark_purged wins first -- no claim is ever taken in this
        # ordering, so there is nothing that could later hold a stale
        # reference to try a delayed deletion against.
        purged = store.mark_purged("tenant-a", "ing-1", clock[0])
        assert purged is not None and purged.status is IngestionStatus.DELETED
        assert store.begin_purge("tenant-a", "ing-1", clock[0]) is None

        # Tombstone expires; the same ID is reused.
        clock[0] = T + retention + dt.timedelta(days=1)
        _, created = store.create_or_get_received(
            "tenant-a", "sha256:b", "ing-1", _record("tenant-a", "ing-1", "sha256:b", clock[0])
        )
        assert created is True

        # The reused identity is genuinely RECEIVED -- any purge attempt
        # against it (there being no leftover claim of any kind) is
        # rejected exactly like any other still-received record.
        with pytest.raises(ValueError, match="already-retired record"):
            store.begin_purge("tenant-a", "ing-1", clock[0])
        current = store.get_any_status("tenant-a", "ing-1")
        assert current is not None
        assert current.status is IngestionStatus.RECEIVED
