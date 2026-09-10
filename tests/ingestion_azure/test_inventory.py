"""Tests for the operator-only tenant inventory/offboarding mechanism
(task 11, `docs/pilots/ingestion-pilot-runbook.md` §16's own hard
blocker). Includes the exact tabletop scenario that runbook section
describes: one tenant ingestion ID missing from the customer's own
records must still be found and handled.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cloudops_guard.ingestion.argon2_backend import Argon2SecretVerifier
from cloudops_guard.ingestion.models import (
    IngestionRecord,
    IngestionStatus,
)
from cloudops_guard.ingestion_api.config import IngestionApiConfig
from cloudops_guard.ingestion_azure import inventory
from cloudops_guard.ingestion_azure.pool import IngestionDatabasePool
from cloudops_guard.ingestion_azure.postgres_attempt_limiter import PostgresAttemptLimiter
from cloudops_guard.ingestion_azure.postgres_metadata_store import PostgresMetadataStore
from cloudops_guard.ingestion_azure.postgres_request_rate_limiter import (
    PostgresRequestRateLimiter,
)
from cloudops_guard.ingestion_azure.postgres_token_store import PostgresTokenStore

pytestmark = pytest.mark.postgres

HMAC_KEY = b"x" * 32


class _FakeBlobStore:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put(self, storage_key: str, data: bytes) -> None:
        self._blobs[storage_key] = data

    def put_if_absent(self, storage_key: str, data: bytes) -> bool:
        if storage_key in self._blobs:
            return False
        self._blobs[storage_key] = data
        return True

    def get(self, storage_key: str) -> bytes | None:
        return self._blobs.get(storage_key)

    def delete(self, storage_key: str) -> None:
        self._blobs.pop(storage_key, None)


def _record(
    tenant_id: str,
    ingestion_id: str,
    fingerprint: str,
    status: IngestionStatus,
    at: dt.datetime,
    **kw,
) -> IngestionRecord:
    return IngestionRecord(
        tenant_id=tenant_id,
        ingestion_id=ingestion_id,
        report_fingerprint=fingerprint,
        received_at=at,
        status=status,
        **kw,
    )


@pytest.fixture()
def metadata_store(postgres_conninfo: str) -> PostgresMetadataStore:
    pool = IngestionDatabasePool(postgres_conninfo)
    pool.open()
    yield PostgresMetadataStore(pool, idempotency_key_window=dt.timedelta(hours=24))
    pool.close()


@pytest.fixture()
def full_config(postgres_conninfo: str):
    pool = IngestionDatabasePool(postgres_conninfo)
    pool.open()
    metadata_store = PostgresMetadataStore(pool, idempotency_key_window=dt.timedelta(hours=24))
    blob_store = _FakeBlobStore()
    token_store = PostgresTokenStore(pool, Argon2SecretVerifier())
    lookup_limiter = PostgresAttemptLimiter(
        pool, threshold=100, window=dt.timedelta(minutes=15), hmac_key=HMAC_KEY
    )
    source_limiter = PostgresAttemptLimiter(
        pool, threshold=100, window=dt.timedelta(minutes=15), hmac_key=HMAC_KEY
    )
    token_rate_limiter = PostgresRequestRateLimiter(
        pool, threshold=1000, window=dt.timedelta(minutes=1), hmac_key=HMAC_KEY
    )
    capabilities_rate_limiter = PostgresRequestRateLimiter(
        pool, threshold=1000, window=dt.timedelta(minutes=1), hmac_key=HMAC_KEY
    )
    config = IngestionApiConfig(
        metadata_store=metadata_store,
        blob_store=blob_store,
        token_store=token_store,
        lookup_limiter=lookup_limiter,
        source_limiter=source_limiter,
        token_rate_limiter=token_rate_limiter,
        capabilities_rate_limiter=capabilities_rate_limiter,
    )
    yield config, pool, blob_store
    pool.close()


class TestTenantIdentityDoubleEntry:
    def test_mismatched_confirm_tenant_id_raises(
        self, metadata_store: PostgresMetadataStore
    ) -> None:
        with pytest.raises(inventory.TenantIdentityMismatchError):
            inventory.build_tenant_inventory(
                metadata_store, tenant_id="tenant-a", confirm_tenant_id="tenant-b"
            )

    def test_matched_confirm_tenant_id_succeeds(
        self, metadata_store: PostgresMetadataStore
    ) -> None:
        entries = inventory.build_tenant_inventory(
            metadata_store, tenant_id="tenant-a", confirm_tenant_id="tenant-a"
        )
        assert entries == []


class TestTenantIsolation:
    def test_inventory_never_returns_another_tenants_records(
        self, metadata_store: PostgresMetadataStore
    ) -> None:
        now = dt.datetime.now(dt.UTC)
        metadata_store.create_or_get_received(
            "tenant-a",
            "fp1",
            "ing1",
            _record("tenant-a", "ing1", "fp1", IngestionStatus.RECEIVED, now),
        )
        metadata_store.create_or_get_received(
            "tenant-b",
            "fp2",
            "ing2",
            _record("tenant-b", "ing2", "fp2", IngestionStatus.RECEIVED, now),
        )
        entries = inventory.build_tenant_inventory(
            metadata_store, tenant_id="tenant-a", confirm_tenant_id="tenant-a"
        )
        assert [e.ingestion_id for e in entries] == ["ing1"]


class TestTabletopScenarioIncompleteCustomerInventory:
    """Reproduces `docs/pilots/ingestion-pilot-runbook.md` §16's own
    required tabletop test: a pilot customer requests full offboarding
    and provides their own list of `ingestion_id`s, but one real
    ingestion for their tenant is missing from that list (e.g. created by
    an automated job the customer never reviewed, or via a compromised
    token). The corrected offboarding procedure must still identify and
    retire/purge it, because it queries the operator-only tenant-scoped
    inventory directly rather than relying on the customer's own list.
    """

    def test_offboarding_finds_and_retires_the_id_missing_from_customer_records(
        self, full_config
    ) -> None:
        config, pool, blob_store = full_config
        metadata_store = config.metadata_store
        now = dt.datetime.now(dt.UTC)

        # The customer's own retained record: only ing-known.
        metadata_store.create_or_get_received(
            "tenant-a",
            "fp-known",
            "ing-known",
            _record("tenant-a", "ing-known", "fp-known", IngestionStatus.RECEIVED, now),
        )
        blob_store.put("tenant-a/ing-known", b"report bytes for the known ingestion")

        # An ingestion the customer never knew about (e.g. a compromised-
        # token upload) -- deliberately absent from any "customer-
        # provided" list this test constructs.
        metadata_store.create_or_get_received(
            "tenant-a",
            "fp-unknown-to-customer",
            "ing-unknown-to-customer",
            _record(
                "tenant-a",
                "ing-unknown-to-customer",
                "fp-unknown-to-customer",
                IngestionStatus.RECEIVED,
                now,
            ),
        )
        blob_store.put(
            "tenant-a/ing-unknown-to-customer", b"report bytes the customer never knew existed"
        )

        customer_provided_ids = {"ing-known"}  # what the customer THINKS exists

        # The old, incorrect procedure this pass replaced: iterate only
        # over the customer's own list. This demonstrates the exact gap.
        old_procedure_would_retire = customer_provided_ids
        assert "ing-unknown-to-customer" not in old_procedure_would_retire

        # The corrected procedure: query the real, operator-only,
        # tenant-scoped inventory -- never the customer's list.
        plan = inventory.plan_tenant_offboarding(
            metadata_store, tenant_id="tenant-a", confirm_tenant_id="tenant-a"
        )
        assert set(plan.to_retire) == {"ing-known", "ing-unknown-to-customer"}

        result = inventory.execute_tenant_offboarding(
            config,
            pool,
            tenant_id="tenant-a",
            confirm_tenant_id="tenant-a",
            confirmation_phrase=inventory.required_offboarding_confirmation_phrase("tenant-a"),
            operator="test-operator",
        )
        assert set(result.retired) == {"ing-known", "ing-unknown-to-customer"}
        assert set(result.purged) == {"ing-known", "ing-unknown-to-customer"}
        # The report bytes for the ID the customer never knew about were
        # genuinely purged too -- not merely marked retired.
        assert blob_store.get("tenant-a/ing-unknown-to-customer") is None
        assert blob_store.get("tenant-a/ing-known") is None


class TestConfirmationPhraseEnforcement:
    def test_wrong_phrase_raises_and_mutates_nothing(self, full_config) -> None:
        config, pool, blob_store = full_config
        metadata_store = config.metadata_store
        now = dt.datetime.now(dt.UTC)
        metadata_store.create_or_get_received(
            "tenant-a",
            "fp1",
            "ing1",
            _record("tenant-a", "ing1", "fp1", IngestionStatus.RECEIVED, now),
        )
        with pytest.raises(inventory.OffboardingConfirmationError):
            inventory.execute_tenant_offboarding(
                config,
                pool,
                tenant_id="tenant-a",
                confirm_tenant_id="tenant-a",
                confirmation_phrase="wrong-phrase",
                operator="test-operator",
            )
        record = metadata_store.get("tenant-a", "ing1")
        assert record is not None
        assert record.status is IngestionStatus.RECEIVED

    def test_mismatched_tenant_identity_raises_before_confirmation_check(self, full_config) -> None:
        config, pool, blob_store = full_config
        with pytest.raises(inventory.TenantIdentityMismatchError):
            inventory.execute_tenant_offboarding(
                config,
                pool,
                tenant_id="tenant-a",
                confirm_tenant_id="tenant-b",
                confirmation_phrase=inventory.required_offboarding_confirmation_phrase("tenant-a"),
                operator="test-operator",
            )


class TestAuditTrail:
    def test_offboarding_writes_append_only_audit_events(self, full_config) -> None:
        config, pool, blob_store = full_config
        metadata_store = config.metadata_store
        now = dt.datetime.now(dt.UTC)
        metadata_store.create_or_get_received(
            "tenant-a",
            "fp1",
            "ing1",
            _record("tenant-a", "ing1", "fp1", IngestionStatus.RECEIVED, now),
        )
        inventory.execute_tenant_offboarding(
            config,
            pool,
            tenant_id="tenant-a",
            confirm_tenant_id="tenant-a",
            confirmation_phrase=inventory.required_offboarding_confirmation_phrase("tenant-a"),
            operator="test-operator",
        )
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT action, ingestion_id, outcome FROM operator_audit_events "
                "WHERE tenant_id = %s ORDER BY id",
                ("tenant-a",),
            ).fetchall()
        actions = [r[0] for r in rows]
        assert "offboarding_requested" in actions
        assert "retire" in actions
        assert "purge" in actions
        # Never any report content or token value in the audit trail.
        full_text = str(rows)
        assert "report bytes" not in full_text
