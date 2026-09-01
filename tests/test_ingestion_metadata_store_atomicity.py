"""Concurrency tests for `InMemoryMetadataStore.create_or_get_received`
(§4.1: exactly one caller wins, every loser gets the same winning record,
per-tenant isolation, and IdempotencyKeyConflict on cross-fingerprint key
reuse under simultaneous requests).

No test in this file sleeps or otherwise depends on wall-clock timing --
every concurrency scenario is synchronized with a `threading.Barrier` so
all worker threads attempt their call at (as close as the runtime allows
to) the same instant, and each scenario is repeated multiple times to
exercise different thread-interleavings.
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest

from cloudops_guard.ingestion.errors import IdempotencyKeyConflict, IngestionIdConflict
from cloudops_guard.ingestion.models import IngestionRecord, IngestionStatus, RetirementReason
from cloudops_guard.ingestion.reference import InMemoryMetadataStore

UTC = dt.UTC
NOW = dt.datetime(2026, 1, 1, tzinfo=UTC)

WORKER_COUNT = 16
REPEATS = 8


def _record(tenant_id: str, ingestion_id: str, fingerprint: str) -> IngestionRecord:
    return IngestionRecord(
        tenant_id=tenant_id,
        ingestion_id=ingestion_id,
        report_fingerprint=fingerprint,
        received_at=NOW,
        status=IngestionStatus.RECEIVED,
    )


def _run_concurrently(worker_count: int, target) -> list:
    barrier = threading.Barrier(worker_count)
    results: list = [None] * worker_count
    errors: list = [None] * worker_count

    def run(index: int) -> None:
        barrier.wait()
        try:
            results[index] = target(index)
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors[index] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, errors


class TestConcurrentCreationWithoutIdempotencyKey:
    @pytest.mark.parametrize("_repeat", range(REPEATS))
    def test_exactly_one_winner_same_tenant_and_fingerprint(self, _repeat: int) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        tenant_id = "tenant-a"
        fingerprint = "sha256:same"

        def call(index: int):
            return store.create_or_get_received(
                tenant_id,
                fingerprint,
                f"ing-{index}",
                _record(tenant_id, f"ing-{index}", fingerprint),
            )

        results, errors = _run_concurrently(WORKER_COUNT, call)

        assert all(error is None for error in errors)
        created_flags = [created for _record_result, created in results]
        assert created_flags.count(True) == 1

        winning_records = {record.ingestion_id for record, _created in results}
        assert len(winning_records) == 1

    @pytest.mark.parametrize("_repeat", range(REPEATS))
    def test_different_fingerprints_do_not_interfere(self, _repeat: int) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        tenant_id = "tenant-a"

        def call(index: int):
            fingerprint = f"sha256:{index}"
            return store.create_or_get_received(
                tenant_id,
                fingerprint,
                f"ing-{index}",
                _record(tenant_id, f"ing-{index}", fingerprint),
            )

        results, errors = _run_concurrently(WORKER_COUNT, call)

        assert all(error is None for error in errors)
        assert all(created is True for _record_result, created in results)


class TestConcurrentCreationWithIdempotencyKey:
    @pytest.mark.parametrize("_repeat", range(REPEATS))
    def test_exactly_one_winner_same_key_same_fingerprint(self, _repeat: int) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        tenant_id = "tenant-a"
        fingerprint = "sha256:same"
        key = "idem-key-1"

        def call(index: int):
            return store.create_or_get_received(
                tenant_id,
                fingerprint,
                f"ing-{index}",
                _record(tenant_id, f"ing-{index}", fingerprint),
                idempotency_key=key,
            )

        results, errors = _run_concurrently(WORKER_COUNT, call)

        assert all(error is None for error in errors)
        created_flags = [created for _record_result, created in results]
        assert created_flags.count(True) == 1
        winning_records = {record.ingestion_id for record, _created in results}
        assert len(winning_records) == 1

    @pytest.mark.parametrize("_repeat", range(REPEATS))
    def test_same_key_competing_fingerprints_exactly_one_winner(self, _repeat: int) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        tenant_id = "tenant-a"
        key = "idem-key-1"

        def call(index: int):
            fingerprint = f"sha256:fp-{index}"
            return store.create_or_get_received(
                tenant_id,
                fingerprint,
                f"ing-{index}",
                _record(tenant_id, f"ing-{index}", fingerprint),
                idempotency_key=key,
            )

        results, errors = _run_concurrently(WORKER_COUNT, call)

        successes = [r for r in results if r is not None]
        conflicts = [e for e in errors if e is not None]

        assert len(successes) == 1
        assert successes[0][1] is True
        assert len(conflicts) == WORKER_COUNT - 1
        assert all(isinstance(error, IdempotencyKeyConflict) for error in conflicts)


class TestConcurrentCreationAcrossTenants:
    @pytest.mark.parametrize("_repeat", range(REPEATS))
    def test_same_fingerprint_different_tenants_each_get_own_record(self, _repeat: int) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        fingerprint = "sha256:shared"

        def call(index: int):
            tenant_id = f"tenant-{index}"
            return store.create_or_get_received(
                tenant_id,
                fingerprint,
                f"ing-{index}",
                _record(tenant_id, f"ing-{index}", fingerprint),
            )

        results, errors = _run_concurrently(WORKER_COUNT, call)

        assert all(error is None for error in errors)
        assert all(created is True for _record_result, created in results)
        ingestion_ids = {record.ingestion_id for record, _created in results}
        assert len(ingestion_ids) == WORKER_COUNT


class TestIngestionIdCollisionRejection:
    """A `new_ingestion_id` that already identifies a *different* record
    (live, retired, deleted-but-tombstoned) must never be silently
    overwritten by step 3 of `create_or_get_received` -- doing so would
    leave `_active_fingerprints`/`_idempotency_bindings` pointing at a
    record with the wrong `report_fingerprint`. These tests only reach
    step 3 by construction: every scenario below uses inputs that do not
    match step 1 (idempotency key) or step 2 (content fingerprint dedup),
    so a legitimate replay is never mistaken for a collision.
    """

    def test_same_tenant_same_ingestion_id_different_fingerprint_is_rejected(self) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        tenant_id = "tenant-a"
        original, created = store.create_or_get_received(
            tenant_id, "sha256:a", "ing-1", _record(tenant_id, "ing-1", "sha256:a")
        )
        assert created is True

        with pytest.raises(IngestionIdConflict):
            store.create_or_get_received(
                tenant_id, "sha256:b", "ing-1", _record(tenant_id, "ing-1", "sha256:b")
            )

    def test_original_record_and_both_lookup_indexes_remain_correct_after_rejection(self) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        tenant_id = "tenant-a"
        original, _created = store.create_or_get_received(
            tenant_id, "sha256:a", "ing-1", _record(tenant_id, "ing-1", "sha256:a")
        )

        with pytest.raises(IngestionIdConflict):
            store.create_or_get_received(
                tenant_id, "sha256:b", "ing-1", _record(tenant_id, "ing-1", "sha256:b")
            )

        # The original record is untouched (proves _records was never
        # overwritten by the rejected candidate).
        assert store.get(tenant_id, "ing-1") == original

        # The original fingerprint index still resolves to the original
        # record on a legitimate replay (proves _active_fingerprints was
        # never repointed at the rejected candidate).
        replay, replay_created = store.create_or_get_received(
            tenant_id, "sha256:a", "ing-1", _record(tenant_id, "ing-1", "sha256:a")
        )
        assert replay_created is False
        assert replay == original

        # The rejected fingerprint was never registered as active either.
        fresh, fresh_created = store.create_or_get_received(
            tenant_id, "sha256:b", "ing-2", _record(tenant_id, "ing-2", "sha256:b")
        )
        assert fresh_created is True
        assert fresh.ingestion_id == "ing-2"

    def test_collision_with_a_retired_identity_is_rejected(self) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        tenant_id = "tenant-a"
        store.create_or_get_received(
            tenant_id, "sha256:a", "ing-1", _record(tenant_id, "ing-1", "sha256:a")
        )
        store.mark_retired(tenant_id, "ing-1", NOW, RetirementReason.CUSTOMER_REQUESTED)

        with pytest.raises(IngestionIdConflict):
            store.create_or_get_received(
                tenant_id, "sha256:b", "ing-1", _record(tenant_id, "ing-1", "sha256:b")
            )

    def test_collision_with_a_deleted_tombstoned_identity_is_rejected(self) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        tenant_id = "tenant-a"
        store.create_or_get_received(
            tenant_id, "sha256:a", "ing-1", _record(tenant_id, "ing-1", "sha256:a")
        )
        store.mark_retired(tenant_id, "ing-1", NOW, RetirementReason.CUSTOMER_REQUESTED)
        store.mark_purged(tenant_id, "ing-1", NOW)

        with pytest.raises(IngestionIdConflict):
            store.create_or_get_received(
                tenant_id, "sha256:b", "ing-1", _record(tenant_id, "ing-1", "sha256:b")
            )
        # The tombstone itself is unaffected by the rejected attempt.
        assert store.get_tombstone(tenant_id, "ing-1") is not None

    def test_collision_check_is_skipped_once_the_tombstone_has_expired(self) -> None:
        clock = [NOW]
        store = InMemoryMetadataStore(
            clock=lambda: clock[0], tombstone_retention=dt.timedelta(days=1)
        )
        tenant_id = "tenant-a"
        store.create_or_get_received(
            tenant_id, "sha256:a", "ing-1", _record(tenant_id, "ing-1", "sha256:a")
        )
        store.mark_retired(tenant_id, "ing-1", clock[0], RetirementReason.CUSTOMER_REQUESTED)
        store.mark_purged(tenant_id, "ing-1", clock[0])

        clock[0] = NOW + dt.timedelta(days=2)
        # Once the tombstone has fully expired, "ing-1" is indistinguishable
        # from an ID that never existed (§E.4) -- reuse must succeed.
        reused, created = store.create_or_get_received(
            tenant_id,
            "sha256:b",
            "ing-1",
            _record(tenant_id, "ing-1", "sha256:b"),
        )
        assert created is True
        assert reused.report_fingerprint == "sha256:b"

    def test_same_ingestion_id_in_different_tenants_remains_isolated_and_allowed(self) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        record_a, created_a = store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )
        record_b, created_b = store.create_or_get_received(
            "tenant-b", "sha256:b", "ing-1", _record("tenant-b", "ing-1", "sha256:b")
        )
        assert created_a is True
        assert created_b is True
        assert record_a.tenant_id == "tenant-a"
        assert record_b.tenant_id == "tenant-b"
        assert store.get("tenant-a", "ing-1") == record_a
        assert store.get("tenant-b", "ing-1") == record_b

    def test_rejection_leaves_idempotency_bindings_unchanged(self) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        tenant_id = "tenant-a"
        key = "idem-1"
        original, _created = store.create_or_get_received(
            tenant_id,
            "sha256:a",
            "ing-1",
            _record(tenant_id, "ing-1", "sha256:a"),
            idempotency_key=key,
        )

        # A different ingestion_id colliding with an unrelated identity,
        # using a *different* idempotency_key, must not disturb the first
        # key's binding.
        store.create_or_get_received(
            tenant_id, "sha256:c", "ing-2", _record(tenant_id, "ing-2", "sha256:c")
        )
        with pytest.raises(IngestionIdConflict):
            store.create_or_get_received(
                tenant_id,
                "sha256:d",
                "ing-2",
                _record(tenant_id, "ing-2", "sha256:d"),
                idempotency_key="idem-2",
            )

        # The original key still replays to the original record.
        replay, replay_created = store.create_or_get_received(
            tenant_id,
            "sha256:a",
            "ing-1",
            _record(tenant_id, "ing-1", "sha256:a"),
            idempotency_key=key,
        )
        assert replay_created is False
        assert replay == original


class TestConcurrentIngestionIdCollision:
    @pytest.mark.parametrize("_repeat", range(REPEATS))
    def test_concurrent_different_fingerprint_calls_with_same_new_ingestion_id(
        self, _repeat: int
    ) -> None:
        store = InMemoryMetadataStore(clock=lambda: NOW)
        tenant_id = "tenant-a"

        def call(index: int):
            fingerprint = f"sha256:fp-{index}"
            return store.create_or_get_received(
                tenant_id, fingerprint, "ing-shared", _record(tenant_id, "ing-shared", fingerprint)
            )

        results, errors = _run_concurrently(WORKER_COUNT, call)

        successes = [r for r in results if r is not None]
        conflicts = [e for e in errors if e is not None]

        # Exactly one winner -- every other caller gets a clean
        # IngestionIdConflict, never a second record and never a
        # corrupted index.
        assert len(successes) == 1
        assert successes[0][1] is True
        assert len(conflicts) == WORKER_COUNT - 1
        assert all(isinstance(error, IngestionIdConflict) for error in conflicts)

        winning_record = successes[0][0]
        # The store's own state agrees with the winner, and the winning
        # fingerprint's own dedup path still resolves correctly
        # afterward (proves _active_fingerprints was not corrupted).
        assert store.get(tenant_id, "ing-shared") == winning_record
        replay, replay_created = store.create_or_get_received(
            tenant_id,
            winning_record.report_fingerprint,
            "ing-shared",
            _record(tenant_id, "ing-shared", winning_record.report_fingerprint),
        )
        assert replay_created is False
        assert replay == winning_record
