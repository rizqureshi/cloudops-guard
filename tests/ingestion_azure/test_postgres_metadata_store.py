"""Behavioral parity tests for `PostgresMetadataStore` against a real,
local PostgreSQL instance -- mirrors the guarantees
`tests/test_ingestion_metadata_store_lifecycle.py`,
`tests/test_ingestion_metadata_store_atomicity.py`, and
`tests/test_ingestion_metadata_store_purge_claims.py` already establish
for `InMemoryMetadataStore`, proving the real adapter preserves the exact
same observable behavior (Phase 4B/4D/purge-claim-hardening-pass
semantics), not merely "some" atomic-dedup/purge-claim behavior.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cloudops_guard.ingestion.errors import IdempotencyKeyConflict, IngestionIdConflict
from cloudops_guard.ingestion.models import IngestionRecord, IngestionStatus, RetirementReason
from cloudops_guard.ingestion_azure.pool import IngestionDatabasePool
from cloudops_guard.ingestion_azure.postgres_metadata_store import PostgresMetadataStore

pytestmark = pytest.mark.postgres


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


@pytest.fixture()
def store(postgres_conninfo: str) -> PostgresMetadataStore:
    pool = IngestionDatabasePool(postgres_conninfo)
    pool.open()
    yield PostgresMetadataStore(
        pool,
        idempotency_key_window=dt.timedelta(hours=24),
        tombstone_retention=dt.timedelta(days=1),
    )
    pool.close()


class TestCreateOrGetReceived:
    def test_first_call_creates(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        record, created = store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        assert created is True
        assert record.status is IngestionStatus.RECEIVED

    def test_second_call_same_fingerprint_replays(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        first, _ = store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        second, created = store.create_or_get_received(
            "tenant-a", "fp1", "ing2", _record("tenant-a", "ing2", "fp1", now)
        )
        assert created is False
        assert second.ingestion_id == first.ingestion_id

    def test_different_tenants_same_fingerprint_both_create(
        self, store: PostgresMetadataStore
    ) -> None:
        now = dt.datetime.now(dt.UTC)
        _, created_a = store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        _, created_b = store.create_or_get_received(
            "tenant-b", "fp1", "ing2", _record("tenant-b", "ing2", "fp1", now)
        )
        assert created_a is True
        assert created_b is True

    def test_ingestion_id_collision_against_different_fingerprint_raises(
        self, store: PostgresMetadataStore
    ) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        with pytest.raises(IngestionIdConflict):
            store.create_or_get_received(
                "tenant-a", "fp2", "ing1", _record("tenant-a", "ing1", "fp2", now)
            )

    def test_idempotency_key_replay_same_fingerprint(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        first, _ = store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now), idempotency_key="k1"
        )
        second, created = store.create_or_get_received(
            "tenant-a", "fp1", "ing2", _record("tenant-a", "ing2", "fp1", now), idempotency_key="k1"
        )
        assert created is False
        assert second.ingestion_id == first.ingestion_id

    def test_idempotency_key_conflict_on_different_fingerprint(
        self, store: PostgresMetadataStore
    ) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now), idempotency_key="k1"
        )
        with pytest.raises(IdempotencyKeyConflict):
            store.create_or_get_received(
                "tenant-a",
                "fp2",
                "ing2",
                _record("tenant-a", "ing2", "fp2", now),
                idempotency_key="k1",
            )

    def test_idempotency_key_window_expiry_allows_fresh_ingestion(
        self, postgres_conninfo: str
    ) -> None:
        clock_time = [dt.datetime(2026, 1, 1, tzinfo=dt.UTC)]
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        store = PostgresMetadataStore(pool, idempotency_key_window=dt.timedelta(hours=24))
        now = clock_time[0]
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now), idempotency_key="k1"
        )
        # Retire so the fingerprint-based dedup path doesn't also match --
        # isolating this test to the idempotency-key-window boundary alone.
        store.mark_retired(
            "tenant-a", "ing1", now + dt.timedelta(hours=1), RetirementReason.CUSTOMER_REQUESTED
        )

        later = now + dt.timedelta(hours=25)
        record, created = store.create_or_get_received(
            "tenant-a",
            "fp1",
            "ing2",
            _record("tenant-a", "ing2", "fp1", later),
            idempotency_key="k1",
        )
        assert created is True
        assert record.ingestion_id == "ing2"
        pool.close()

    def test_retired_record_does_not_dedup_a_resend(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        first, _ = store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)
        second, created = store.create_or_get_received(
            "tenant-a", "fp1", "ing2", _record("tenant-a", "ing2", "fp1", now)
        )
        assert created is True
        assert second.ingestion_id != first.ingestion_id


class TestGetAndGetAnyStatus:
    def test_get_returns_none_for_retired(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)
        assert store.get("tenant-a", "ing1") is None
        assert store.get_any_status("tenant-a", "ing1") is not None

    def test_get_scoped_to_tenant(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        assert store.get("tenant-b", "ing1") is None


class TestMarkRetired:
    def test_idempotent_preserves_original_reason(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        first = store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)
        second = store.mark_retired(
            "tenant-a", "ing1", now + dt.timedelta(seconds=1), RetirementReason.RETENTION_EXPIRED
        )
        assert second.reason is RetirementReason.CUSTOMER_REQUESTED
        assert second.retired_at == first.retired_at

    def test_unknown_returns_none(self, store: PostgresMetadataStore) -> None:
        assert (
            store.mark_retired(
                "tenant-a", "missing", dt.datetime.now(dt.UTC), RetirementReason.CUSTOMER_REQUESTED
            )
            is None
        )


class TestPurgeClaims:
    def test_full_lifecycle(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)
        claim = store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))
        assert claim is not None
        final = store.finalize_purge(claim)
        assert final.status is IngestionStatus.DELETED
        tombstone = store.get_tombstone("tenant-a", "ing1")
        assert tombstone is not None

    def test_begin_purge_on_received_raises(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        with pytest.raises(ValueError):
            store.begin_purge("tenant-a", "ing1", now)

    def test_second_concurrent_claim_is_refused(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)
        claim_a = store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))
        claim_b = store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))
        assert claim_a is not None
        assert claim_b is None

    def test_release_then_reacquire(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)
        claim_a = store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))
        store.release_purge_claim(claim_a)
        claim_b = store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))
        assert claim_b is not None
        assert claim_b.claim_id != claim_a.claim_id

    def test_aba_release_of_old_claim_does_not_cancel_new_one(
        self, store: PostgresMetadataStore
    ) -> None:
        """Reproduces the exact ABA scenario the purge-claim hardening
        pass closed for the in-memory reference implementation
        (CLAUDE.md's own Phase 4D history): claim A acquired and
        released; claim B acquired for the same, still-current
        generation; releasing A *again* must not cancel B.
        """
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)
        claim_a = store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))
        store.release_purge_claim(claim_a)
        claim_b = store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))
        assert claim_b is not None

        store.release_purge_claim(claim_a)  # stale release -- must be a no-op

        # B must still be able to finalize.
        final = store.finalize_purge(claim_b)
        assert final is not None
        assert final.status is IngestionStatus.DELETED

    def test_stale_claim_cannot_finalize(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)
        claim = store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))
        store.release_purge_claim(claim)
        assert store.finalize_purge(claim) is None

    def test_mark_purged_refuses_while_claim_active(self, store: PostgresMetadataStore) -> None:
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)
        store.begin_purge("tenant-a", "ing1", now + dt.timedelta(seconds=1))
        with pytest.raises(ValueError):
            store.mark_purged("tenant-a", "ing1", now + dt.timedelta(seconds=1))


class TestTombstoneExpiryAndReuse:
    def test_tombstone_expires_and_key_is_reusable(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        store = PostgresMetadataStore(
            pool,
            idempotency_key_window=dt.timedelta(hours=24),
            tombstone_retention=dt.timedelta(seconds=1),
        )
        now = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp1", "ing1", _record("tenant-a", "ing1", "fp1", now)
        )
        store.mark_retired("tenant-a", "ing1", now, RetirementReason.CUSTOMER_REQUESTED)
        claim = store.begin_purge("tenant-a", "ing1", now)
        store.finalize_purge(claim)
        assert store.get_tombstone("tenant-a", "ing1") is not None

        # Simulate elapsed time by using a later "now" for a fresh
        # operation -- create_or_get_received's own tombstone-expiry
        # check uses the DATABASE's now(), which has genuinely advanced
        # by the time this second call executes 1+ second later.
        import time

        time.sleep(1.2)
        record, created = store.create_or_get_received(
            "tenant-a", "fp1", "ing2", _record("tenant-a", "ing2", "fp1", dt.datetime.now(dt.UTC))
        )
        assert created is True
        assert store.get_tombstone("tenant-a", "ing1") is None
        pool.close()


class TestListExpiredForRetentionSweep:
    def test_returns_only_received_older_than_cutoff(self, store: PostgresMetadataStore) -> None:
        old = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
        recent = dt.datetime.now(dt.UTC)
        store.create_or_get_received(
            "tenant-a", "fp-old", "ing-old", _record("tenant-a", "ing-old", "fp-old", old)
        )
        store.create_or_get_received(
            "tenant-a", "fp-new", "ing-new", _record("tenant-a", "ing-new", "fp-new", recent)
        )
        cutoff = dt.datetime(2021, 1, 1, tzinfo=dt.UTC)
        expired = list(store.list_expired_for_retention_sweep(cutoff))
        assert [r.ingestion_id for r in expired] == ["ing-old"]
