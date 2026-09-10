"""Regression tests for the real purge sweep (correction-pass item 7),
run against real local PostgreSQL and Azurite -- never against the
in-memory reference stores, since `run_purge_sweep` itself requires a
`PostgresMetadataStore` (the `list_retired_for_purge_sweep` query it
depends on is a Postgres-specific extension, not part of the portable
`MetadataStore` interface).

Reuses the exact `real_config` fixture pattern already established by
`test_cross_store_recovery.py` (real `PostgresMetadataStore` +
`AzureBlobReportBlobStore`, stub token store/limiters -- this module
never authenticates or rate-limits anything).
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest

from cloudops_guard.ingestion.models import RetirementReason
from cloudops_guard.ingestion.storage_keys import derive_storage_key
from cloudops_guard.ingestion_api.config import IngestionApiConfig
from cloudops_guard.ingestion_api.coordinator import create_ingestion
from cloudops_guard.ingestion_azure.blob_store import AzureBlobReportBlobStore
from cloudops_guard.ingestion_azure.pool import IngestionDatabasePool
from cloudops_guard.ingestion_azure.postgres_metadata_store import PostgresMetadataStore
from cloudops_guard.ingestion_azure.purge_sweep import run_purge_sweep

pytestmark = [pytest.mark.postgres, pytest.mark.azurite]


class _StubTokenStore:
    def lookup(self, lookup_id):  # pragma: no cover -- unused by these tests
        raise NotImplementedError

    def verify_secret(self, presented_secret, secret_hash):  # pragma: no cover
        raise NotImplementedError

    def mark_revoked(self, lookup_id):  # pragma: no cover
        raise NotImplementedError


class _StubLimiter:
    def record_failure(self, scope_key):  # pragma: no cover
        raise NotImplementedError

    def is_blocked(self, scope_key):  # pragma: no cover
        raise NotImplementedError


class _StubRateLimiter:
    def check_and_record_request(self, scope_key):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture()
def real_config(postgres_conninfo: str, azurite_container_client):
    client, container_name = azurite_container_client
    pool = IngestionDatabasePool(postgres_conninfo)
    pool.open()
    metadata_store = PostgresMetadataStore(pool, idempotency_key_window=dt.timedelta(hours=24))
    blob_store = AzureBlobReportBlobStore(client, container_name=container_name)
    config = IngestionApiConfig(
        metadata_store=metadata_store,
        blob_store=blob_store,
        token_store=_StubTokenStore(),
        lookup_limiter=_StubLimiter(),
        source_limiter=_StubLimiter(),
        token_rate_limiter=_StubRateLimiter(),
        capabilities_rate_limiter=_StubRateLimiter(),
    )
    yield config
    pool.close()


def _receive(config: IngestionApiConfig, tenant_id: str, marker: str):
    report = {"platform": "kubernetes", "findings": [], "marker": marker}
    report_bytes = str(report).encode()
    record, created = create_ingestion(
        config=config,
        tenant_id=tenant_id,
        platform="kubernetes",
        report_schema_version=1,
        report=report,
        report_bytes=report_bytes,
        idempotency_key=None,
    )
    assert created is True
    return record


def _retire(config: IngestionApiConfig, tenant_id: str, ingestion_id: str) -> None:
    result = config.metadata_store.mark_retired(
        tenant_id, ingestion_id, config.clock(), RetirementReason.CUSTOMER_REQUESTED
    )
    assert result is not None


class TestEligibilityAndRetentionTiming:
    def test_only_retired_records_are_purged(self, real_config: IngestionApiConfig) -> None:
        received = _receive(real_config, "tenant-a", "still-received")
        retired = _receive(real_config, "tenant-a", "retired")
        _retire(real_config, "tenant-a", retired.ingestion_id)

        result = run_purge_sweep(real_config)

        assert result.purged == (("tenant-a", retired.ingestion_id),)
        assert result.candidates_considered == 1

        still_received = real_config.metadata_store.get("tenant-a", received.ingestion_id)
        assert still_received is not None
        assert still_received.status.value == "received"

    def test_no_eligible_records_is_a_clean_no_op(self, real_config: IngestionApiConfig) -> None:
        _receive(real_config, "tenant-a", "only-received")
        result = run_purge_sweep(real_config)
        assert result == run_purge_sweep(real_config)  # idempotent, both empty
        assert result.candidates_considered == 0
        assert result.purged == ()
        assert result.failures == ()


class TestNeverDeletesLiveRecords:
    def test_received_records_blob_and_metadata_survive_a_sweep(
        self, real_config: IngestionApiConfig
    ) -> None:
        received = _receive(real_config, "tenant-a", "must-survive")
        retired = _receive(real_config, "tenant-a", "must-be-purged")
        _retire(real_config, "tenant-a", retired.ingestion_id)

        run_purge_sweep(real_config)

        record = real_config.metadata_store.get("tenant-a", received.ingestion_id)
        assert record is not None
        assert record.status.value == "received"
        blob_key = derive_storage_key("tenant-a", received.ingestion_id)
        assert real_config.blob_store.get(blob_key) is not None

    def test_purged_record_blob_and_metadata_are_actually_gone(
        self, real_config: IngestionApiConfig
    ) -> None:
        retired = _receive(real_config, "tenant-a", "goes-away")
        _retire(real_config, "tenant-a", retired.ingestion_id)
        blob_key = derive_storage_key("tenant-a", retired.ingestion_id)
        assert real_config.blob_store.get(blob_key) is not None

        run_purge_sweep(real_config)

        assert real_config.blob_store.get(blob_key) is None
        record = real_config.metadata_store.get_any_status("tenant-a", retired.ingestion_id)
        assert record is not None
        assert record.status.value == "deleted"


class TestBoundedBatches:
    def test_batch_size_caps_a_single_run_and_a_second_run_finishes_the_rest(
        self, real_config: IngestionApiConfig
    ) -> None:
        ids = []
        for i in range(5):
            record = _receive(real_config, "tenant-a", f"record-{i}")
            _retire(real_config, "tenant-a", record.ingestion_id)
            ids.append(record.ingestion_id)

        first = run_purge_sweep(real_config, batch_size=2)
        assert first.candidates_considered == 2
        assert len(first.purged) == 2

        second = run_purge_sweep(real_config, batch_size=2)
        assert second.candidates_considered == 2
        assert len(second.purged) == 2

        third = run_purge_sweep(real_config, batch_size=2)
        assert third.candidates_considered == 1
        assert len(third.purged) == 1

        purged_ids = {
            ingestion_id for _, ingestion_id in first.purged + second.purged + third.purged
        }
        assert purged_ids == set(ids)

    def test_batch_size_must_be_positive(self, real_config: IngestionApiConfig) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            run_purge_sweep(real_config, batch_size=0)


class TestRepeatedAndConcurrentExecution:
    def test_calling_twice_in_a_row_never_double_counts_a_purge(
        self, real_config: IngestionApiConfig
    ) -> None:
        retired = _receive(real_config, "tenant-a", "once")
        _retire(real_config, "tenant-a", retired.ingestion_id)

        first = run_purge_sweep(real_config)
        second = run_purge_sweep(real_config)

        assert first.purged == (("tenant-a", retired.ingestion_id),)
        assert second.purged == ()
        assert second.candidates_considered == 0

    def test_concurrent_sweeps_purge_each_record_exactly_once(
        self, postgres_conninfo: str, azurite_container_client
    ) -> None:
        """Runs several `run_purge_sweep` calls truly concurrently (real
        threads, each against its own `IngestionDatabasePool`/config
        instance, mirroring how two independent scheduled job executions
        would actually run) against a shared set of retired records,
        proving the underlying exclusive purge-claim protocol makes a
        genuinely concurrent purge sweep safe -- no record is purged by
        more than one caller, and every record is purged exactly once
        across all callers combined.
        """
        client, container_name = azurite_container_client
        setup_pool = IngestionDatabasePool(postgres_conninfo)
        setup_pool.open()
        setup_metadata_store = PostgresMetadataStore(
            setup_pool, idempotency_key_window=dt.timedelta(hours=24)
        )
        setup_blob_store = AzureBlobReportBlobStore(client, container_name=container_name)
        setup_config = IngestionApiConfig(
            metadata_store=setup_metadata_store,
            blob_store=setup_blob_store,
            token_store=_StubTokenStore(),
            lookup_limiter=_StubLimiter(),
            source_limiter=_StubLimiter(),
            token_rate_limiter=_StubRateLimiter(),
            capabilities_rate_limiter=_StubRateLimiter(),
        )
        ids = []
        for i in range(20):
            record = _receive(setup_config, "tenant-a", f"concurrent-{i}")
            _retire(setup_config, "tenant-a", record.ingestion_id)
            ids.append(record.ingestion_id)
        setup_pool.close()

        results: list = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(4)

        def worker() -> None:
            pool = IngestionDatabasePool(postgres_conninfo)
            pool.open()
            try:
                metadata_store = PostgresMetadataStore(
                    pool, idempotency_key_window=dt.timedelta(hours=24)
                )
                blob_store = AzureBlobReportBlobStore(client, container_name=container_name)
                config = IngestionApiConfig(
                    metadata_store=metadata_store,
                    blob_store=blob_store,
                    token_store=_StubTokenStore(),
                    lookup_limiter=_StubLimiter(),
                    source_limiter=_StubLimiter(),
                    token_rate_limiter=_StubRateLimiter(),
                    capabilities_rate_limiter=_StubRateLimiter(),
                )
                barrier.wait()
                result = run_purge_sweep(config, batch_size=100)
                with results_lock:
                    results.append(result)
            finally:
                pool.close()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        all_purged = [key for result in results for key in result.purged]
        assert len(all_purged) == len(set(all_purged)), "a record was purged more than once"
        assert {ingestion_id for _, ingestion_id in all_purged} == set(ids)

        verify_pool = IngestionDatabasePool(postgres_conninfo)
        verify_pool.open()
        try:
            verify_store = PostgresMetadataStore(
                verify_pool, idempotency_key_window=dt.timedelta(hours=24)
            )
            for ingestion_id in ids:
                record = verify_store.get_any_status("tenant-a", ingestion_id)
                assert record is not None
                assert record.status.value == "deleted"
        finally:
            verify_pool.close()


class TestPartialBlobFailureIsolation:
    def test_one_records_blob_deletion_failure_does_not_abort_the_batch(
        self, real_config: IngestionApiConfig
    ) -> None:
        good = _receive(real_config, "tenant-a", "good")
        bad = _receive(real_config, "tenant-a", "bad")
        _retire(real_config, "tenant-a", good.ingestion_id)
        _retire(real_config, "tenant-a", bad.ingestion_id)

        bad_key = derive_storage_key("tenant-a", bad.ingestion_id)
        real_delete = real_config.blob_store.delete

        def flaky_delete(storage_key: str) -> None:
            if storage_key == bad_key:
                raise RuntimeError("simulated blob-store outage")
            real_delete(storage_key)

        object.__setattr__(real_config.blob_store, "delete", flaky_delete)

        result = run_purge_sweep(real_config)

        assert result.purged == (("tenant-a", good.ingestion_id),)
        assert len(result.failures) == 1
        assert result.failures[0].ingestion_id == bad.ingestion_id
        assert result.failures[0].error_type == "RuntimeError"

        # The failed record must still be retired, never left in a
        # half-purged state -- the failing blob delete happens before any
        # metadata mutation (lifecycle.py's own "blob deletion first"
        # ordering), and its purge claim was released so a retry remains
        # possible.
        record = real_config.metadata_store.get_any_status("tenant-a", bad.ingestion_id)
        assert record is not None
        assert record.status.value == "retired"

        # A subsequent sweep, once the outage clears, successfully
        # purges the previously-failed record.
        object.__setattr__(real_config.blob_store, "delete", real_delete)
        retry = run_purge_sweep(real_config)
        assert retry.purged == (("tenant-a", bad.ingestion_id),)


class TestRequiresPostgresMetadataStore:
    def test_rejects_a_non_postgres_metadata_store(self) -> None:
        from cloudops_guard.ingestion.reference import InMemoryMetadataStore

        config = IngestionApiConfig(
            metadata_store=InMemoryMetadataStore(),
            blob_store=object(),  # type: ignore[arg-type]
            token_store=_StubTokenStore(),
            lookup_limiter=_StubLimiter(),
            source_limiter=_StubLimiter(),
            token_rate_limiter=_StubRateLimiter(),
            capabilities_rate_limiter=_StubRateLimiter(),
        )
        with pytest.raises(TypeError, match="PostgresMetadataStore"):
            run_purge_sweep(config)
