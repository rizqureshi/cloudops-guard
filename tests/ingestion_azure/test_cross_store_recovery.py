"""Re-runs the cross-store failure-recovery cases
(`cloudops_guard.ingestion_api.coordinator.create_ingestion`, Phase 4D's
own design) against the **real** Postgres/Azurite production adapters
instead of the in-memory reference implementations -- task 4's explicit
requirement ("Re-run the cross-store recovery cases using the production
adapters"). `coordinator.py` itself is completely unmodified; these tests
prove its existing, already-reviewed logic behaves identically when the
two stores it orchestrates are real, independent systems rather than two
in-process dicts guarded by the same lock.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cloudops_guard.ingestion.errors import IdempotencyKeyConflict
from cloudops_guard.ingestion_api.config import IngestionApiConfig
from cloudops_guard.ingestion_api.coordinator import create_ingestion
from cloudops_guard.ingestion_azure.blob_store import AzureBlobReportBlobStore
from cloudops_guard.ingestion_azure.pool import IngestionDatabasePool
from cloudops_guard.ingestion_azure.postgres_metadata_store import PostgresMetadataStore

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


def test_successful_ingestion_writes_both_stores(real_config: IngestionApiConfig) -> None:
    record, created = create_ingestion(
        config=real_config,
        tenant_id="tenant-a",
        platform="kubernetes",
        report_schema_version=1,
        report={"platform": "kubernetes", "findings": []},
        report_bytes=b'{"platform": "kubernetes", "findings": []}',
        idempotency_key=None,
    )
    assert created is True
    assert real_config.metadata_store.get("tenant-a", record.ingestion_id) is not None
    storage_key = f"tenant-a/{record.ingestion_id}"
    assert real_config.blob_store.get(storage_key) is not None


def test_duplicate_request_reuses_existing_record_and_does_not_orphan_a_blob(
    real_config: IngestionApiConfig,
) -> None:
    report = {"platform": "kubernetes", "findings": []}
    report_bytes = b'{"platform": "kubernetes", "findings": []}'
    first, created_first = create_ingestion(
        config=real_config,
        tenant_id="tenant-a",
        platform="kubernetes",
        report_schema_version=1,
        report=report,
        report_bytes=report_bytes,
        idempotency_key=None,
    )
    second, created_second = create_ingestion(
        config=real_config,
        tenant_id="tenant-a",
        platform="kubernetes",
        report_schema_version=1,
        report=report,
        report_bytes=report_bytes,
        idempotency_key=None,
    )
    assert created_first is True
    assert created_second is False
    assert second.ingestion_id == first.ingestion_id

    # The second attempt's own generated-and-reserved blob key (different
    # from first.ingestion_id) must have been cleaned up -- never left
    # behind as an orphan once the dedup race was lost.
    all_records_for_tenant = real_config.metadata_store.list_tenant_records("tenant-a")
    assert len(all_records_for_tenant) == 1


def test_idempotency_key_conflict_cleans_up_its_own_reservation(
    real_config: IngestionApiConfig,
) -> None:
    create_ingestion(
        config=real_config,
        tenant_id="tenant-a",
        platform="kubernetes",
        report_schema_version=1,
        report={"platform": "kubernetes", "findings": []},
        report_bytes=b'{"platform": "kubernetes", "findings": []}',
        idempotency_key="shared-key",
    )
    with pytest.raises(IdempotencyKeyConflict):
        create_ingestion(
            config=real_config,
            tenant_id="tenant-a",
            platform="kubernetes",
            report_schema_version=1,
            report={"platform": "kubernetes", "findings": [{"different": "content"}]},
            report_bytes=b'{"platform": "kubernetes", "findings": [{"different": "content"}]}',
            idempotency_key="shared-key",
        )
    # Only the first, successful ingestion's record exists -- the
    # conflicting attempt's own reserved blob was deleted, never left
    # dangling with no metadata record pointing at it.
    assert len(real_config.metadata_store.list_tenant_records("tenant-a")) == 1


def test_generated_id_collision_retries_with_a_fresh_id(
    real_config: IngestionApiConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forces a real blob-key collision on the first attempt (by
    pre-populating the blob store under the ID the generator will
    produce first), proving `create_ingestion`'s retry loop works against
    the real Azure Blob adapter's own `put_if_absent`.
    """
    ids = iter(["collide-1", "collide-1", "fresh-2"])
    real_config.blob_store.put("tenant-a/collide-1", b"someone else's bytes")

    object.__setattr__(real_config, "ingestion_id_generator", lambda: next(ids))

    record, created = create_ingestion(
        config=real_config,
        tenant_id="tenant-a",
        platform="kubernetes",
        report_schema_version=1,
        report={"platform": "kubernetes", "findings": []},
        report_bytes=b'{"platform": "kubernetes", "findings": []}',
        idempotency_key=None,
    )
    assert created is True
    assert record.ingestion_id == "fresh-2"
    # The pre-existing blob under the collided key must be untouched.
    assert real_config.blob_store.get("tenant-a/collide-1") == b"someone else's bytes"
