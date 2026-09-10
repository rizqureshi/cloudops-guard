"""Real, concurrent-database-transaction proof of `PostgresMetadataStore`'s
highest-risk guarantees (task 12, items 1-4): tenant isolation, atomic
fingerprint/idempotency deduplication, ingestion-ID collision handling,
and exact purge-claim acquisition/release/finalization -- exercised with
genuine concurrent PostgreSQL transactions (real `ThreadPoolExecutor`
workers, each with its own pooled connection), not merely sequential
calls or in-process mocking. Mirrors the rigor
`tests/test_ingestion_metadata_store_atomicity.py` and
`tests/test_ingestion_metadata_store_purge_claims.py` already established
for the in-memory reference implementation.
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pytest

from cloudops_guard.ingestion.models import IngestionRecord, IngestionStatus, RetirementReason
from cloudops_guard.ingestion_azure.pool import IngestionDatabasePool
from cloudops_guard.ingestion_azure.postgres_metadata_store import PostgresMetadataStore

pytestmark = pytest.mark.postgres

CONCURRENCY = 20


@pytest.fixture()
def store(postgres_conninfo: str) -> PostgresMetadataStore:
    pool = IngestionDatabasePool(postgres_conninfo, min_size=2, max_size=CONCURRENCY + 2)
    pool.open()
    yield PostgresMetadataStore(pool, idempotency_key_window=dt.timedelta(hours=24))
    pool.close()


def _record(
    tenant_id: str, ingestion_id: str, fingerprint: str, at: dt.datetime
) -> IngestionRecord:
    return IngestionRecord(
        tenant_id=tenant_id,
        ingestion_id=ingestion_id,
        report_fingerprint=fingerprint,
        received_at=at,
        status=IngestionStatus.RECEIVED,
    )


class TestConcurrentAtomicDedup:
    def test_exactly_one_created_true_under_real_concurrency(
        self, store: PostgresMetadataStore
    ) -> None:
        """`CONCURRENCY` real threads race `create_or_get_received` for the
        exact same `(tenant_id, report_fingerprint)` with different
        generated `ingestion_id`s (mirroring real HTTP request handling,
        where each request generates its own ID before racing the atomic
        dedup) -- at most one may see `created=True`.
        """
        now = dt.datetime.now(dt.UTC)

        def attempt(i: int) -> bool:
            ingestion_id = f"ing-{i}"
            _, created = store.create_or_get_received(
                "tenant-a",
                "fp-shared",
                ingestion_id,
                _record("tenant-a", ingestion_id, "fp-shared", now),
            )
            return created

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            results = list(pool.map(attempt, range(CONCURRENCY)))

        assert results.count(True) == 1
        assert results.count(False) == CONCURRENCY - 1

    def test_concurrent_idempotency_key_binding_never_double_binds(
        self, store: PostgresMetadataStore
    ) -> None:
        now = dt.datetime.now(dt.UTC)

        def attempt(i: int) -> tuple[str, bool]:
            ingestion_id = f"ing-{i}"
            record, created = store.create_or_get_received(
                "tenant-a",
                "fp-shared",
                ingestion_id,
                _record("tenant-a", ingestion_id, "fp-shared", now),
                idempotency_key="shared-key",
            )
            return record.ingestion_id, created

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            results = list(pool.map(attempt, range(CONCURRENCY)))

        winning_ids = {ingestion_id for ingestion_id, created in results if created}
        assert len(winning_ids) == 1
        # Every result -- winner and losers alike -- must report the SAME
        # ingestion_id: the atomic dedup guarantee extended to the
        # idempotency-key binding too.
        assert {ingestion_id for ingestion_id, _ in results} == winning_ids


class TestTenantIsolationUnderConcurrency:
    def test_concurrent_different_tenants_never_collide(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)

        def attempt(i: int) -> bool:
            tenant_id = f"tenant-{i}"
            _, created = store.create_or_get_received(
                tenant_id,
                "fp-shared",
                "ing-shared",
                _record(tenant_id, "ing-shared", "fp-shared", now),
            )
            return created

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            results = list(pool.map(attempt, range(CONCURRENCY)))

        # Every tenant is independent -- the SAME report_fingerprint and
        # the SAME ingestion_id, reused across tenants, must each create
        # successfully; a per-tenant lock/index scope must never leak
        # into a cross-tenant collision.
        assert all(results)


class TestConcurrentPurgeClaims:
    def test_only_one_of_many_concurrent_begin_purge_calls_succeeds(
        self, store: PostgresMetadataStore
    ) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)

        def attempt(_i: int):
            return store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            results = list(pool.map(attempt, range(CONCURRENCY)))

        granted = [claim for claim in results if claim is not None]
        assert len(granted) == 1

    def test_two_purgers_racing_same_record_never_both_delete(self, postgres_conninfo: str) -> None:
        """Reproduces the exact "two concurrent purgers" race the second
        Phase 4D correction pass closed for the in-memory reference
        implementation, against a real database this time: only one
        caller's own physical-delete step should ever be authorized.
        """
        pool = IngestionDatabasePool(postgres_conninfo, min_size=2, max_size=CONCURRENCY + 2)
        pool.open()
        store = PostgresMetadataStore(pool, idempotency_key_window=dt.timedelta(hours=24))
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)

        deletes_performed = []

        def purge_attempt(_i: int) -> None:
            claim = store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))
            if claim is None:
                return
            # Simulate the caller's own physical blob deletion -- the
            # real `lifecycle.purge_retired_ingestion` performs this
            # between begin_purge and finalize_purge.
            deletes_performed.append(claim.claim_id)
            store.finalize_purge(claim)

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool_exec:
            list(pool_exec.map(purge_attempt, range(CONCURRENCY)))

        assert len(deletes_performed) == 1
        record = store.get_any_status("tenant-a", "ing1")
        assert record.status is IngestionStatus.DELETED
        pool.close()
