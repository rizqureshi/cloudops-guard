"""Lifecycle and retention tests for `InMemoryMetadataStore`: retirement
(both reasons), purge/tombstone creation, tombstone-retention expiry,
idempotent repeated transitions, retention-sweep candidate selection, and
unknown/cross-tenant isolation.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

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


def _store(clock_value: list[dt.datetime], **kwargs) -> InMemoryMetadataStore:
    return InMemoryMetadataStore(clock=lambda: clock_value[0], **kwargs)


class TestRetirement:
    @pytest.mark.parametrize(
        "reason", [RetirementReason.CUSTOMER_REQUESTED, RetirementReason.RETENTION_EXPIRED]
    )
    def test_received_transitions_to_retired(self, reason: RetirementReason) -> None:
        clock = [T]
        store = _store(clock)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )

        retired_at = T + dt.timedelta(hours=1)
        retired = store.mark_retired("tenant-a", "ing-1", retired_at, reason)

        assert retired is not None
        assert retired.status is IngestionStatus.RETIRED
        assert retired.reason is reason
        assert retired.retired_at == retired_at
        # get() only ever returns "received" records
        assert store.get("tenant-a", "ing-1") is None

    def test_repeated_mark_retired_is_idempotent(self) -> None:
        clock = [T]
        store = _store(clock)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )

        first = store.mark_retired(
            "tenant-a", "ing-1", T + dt.timedelta(hours=1), RetirementReason.CUSTOMER_REQUESTED
        )
        second = store.mark_retired(
            "tenant-a", "ing-1", T + dt.timedelta(hours=5), RetirementReason.RETENTION_EXPIRED
        )

        # The later call never overwrites the original reason or retired_at.
        assert second == first
        assert second.reason is RetirementReason.CUSTOMER_REQUESTED
        assert second.retired_at == T + dt.timedelta(hours=1)

    def test_mark_retired_unknown_record_returns_none(self) -> None:
        clock = [T]
        store = _store(clock)
        assert (
            store.mark_retired("tenant-a", "missing", T, RetirementReason.CUSTOMER_REQUESTED)
            is None
        )

    def test_retiring_frees_the_fingerprint_for_reuse(self) -> None:
        clock = [T]
        store = _store(clock)
        fingerprint = "sha256:a"
        store.create_or_get_received(
            "tenant-a", fingerprint, "ing-1", _record("tenant-a", "ing-1", fingerprint)
        )
        store.mark_retired("tenant-a", "ing-1", T, RetirementReason.CUSTOMER_REQUESTED)

        _, created = store.create_or_get_received(
            "tenant-a", fingerprint, "ing-2", _record("tenant-a", "ing-2", fingerprint)
        )
        assert created is True


class TestPurgeAndTombstone:
    def test_purging_a_received_record_raises(self) -> None:
        clock = [T]
        store = _store(clock)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )

        with pytest.raises(ValueError):
            store.mark_purged("tenant-a", "ing-1", T)

    def test_purge_transitions_retired_to_deleted_and_creates_tombstone(self) -> None:
        clock = [T]
        store = _store(clock)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )
        retired_at = T + dt.timedelta(hours=1)
        store.mark_retired("tenant-a", "ing-1", retired_at, RetirementReason.CUSTOMER_REQUESTED)

        deleted_at = T + dt.timedelta(hours=2)
        purged = store.mark_purged("tenant-a", "ing-1", deleted_at)

        assert purged is not None
        assert purged.status is IngestionStatus.DELETED
        assert purged.deleted_at == deleted_at
        assert purged.reason is RetirementReason.CUSTOMER_REQUESTED

        tombstone = store.get_tombstone("tenant-a", "ing-1")
        assert tombstone is not None
        assert tombstone.reason is RetirementReason.CUSTOMER_REQUESTED
        assert tombstone.retired_at == retired_at
        assert tombstone.deleted_at == deleted_at

    def test_repeated_mark_purged_is_idempotent(self) -> None:
        clock = [T]
        store = _store(clock)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )
        store.mark_retired("tenant-a", "ing-1", T, RetirementReason.CUSTOMER_REQUESTED)

        first = store.mark_purged("tenant-a", "ing-1", T + dt.timedelta(hours=1))
        second = store.mark_purged("tenant-a", "ing-1", T + dt.timedelta(hours=5))

        assert second == first
        assert second.deleted_at == T + dt.timedelta(hours=1)

    def test_mark_purged_unknown_record_returns_none(self) -> None:
        clock = [T]
        store = _store(clock)
        assert store.mark_purged("tenant-a", "missing", T) is None


class TestTombstoneRetentionExpiry:
    def test_tombstone_disappears_after_retention_window(self) -> None:
        clock = [T]
        retention = dt.timedelta(days=90)
        store = _store(clock, tombstone_retention=retention)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )
        store.mark_retired("tenant-a", "ing-1", T, RetirementReason.CUSTOMER_REQUESTED)
        store.mark_purged("tenant-a", "ing-1", T)

        clock[0] = T + retention
        assert store.get_tombstone("tenant-a", "ing-1") is not None

        clock[0] = T + retention + dt.timedelta(microseconds=1)
        assert store.get_tombstone("tenant-a", "ing-1") is None

    def test_expired_tombstone_lookup_is_idempotent(self) -> None:
        clock = [T]
        retention = dt.timedelta(days=1)
        store = _store(clock, tombstone_retention=retention)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )
        store.mark_retired("tenant-a", "ing-1", T, RetirementReason.CUSTOMER_REQUESTED)
        store.mark_purged("tenant-a", "ing-1", T)

        clock[0] = T + retention + dt.timedelta(days=1)
        assert store.get_tombstone("tenant-a", "ing-1") is None
        assert store.get_tombstone("tenant-a", "ing-1") is None  # still None, no error on repeat

    def test_expired_tombstone_also_removes_the_underlying_record(self) -> None:
        clock = [T]
        retention = dt.timedelta(days=1)
        store = _store(clock, tombstone_retention=retention)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )
        store.mark_retired("tenant-a", "ing-1", T, RetirementReason.CUSTOMER_REQUESTED)
        store.mark_purged("tenant-a", "ing-1", T)

        clock[0] = T + retention + dt.timedelta(days=1)
        store.get_tombstone("tenant-a", "ing-1")  # triggers lazy expiry

        # After expiry, a brand-new ingestion may reuse the same fingerprint.
        _, created = store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-2", _record("tenant-a", "ing-2", "sha256:a", clock[0])
        )
        assert created is True


class TestRetentionSweepCandidates:
    def test_returns_only_received_records_older_than_cutoff(self) -> None:
        clock = [T]
        store = _store(clock)
        old = T - dt.timedelta(days=100)
        recent = T - dt.timedelta(days=1)

        store.create_or_get_received(
            "tenant-a", "sha256:old", "ing-old", _record("tenant-a", "ing-old", "sha256:old", old)
        )
        store.create_or_get_received(
            "tenant-a",
            "sha256:recent",
            "ing-recent",
            _record("tenant-a", "ing-recent", "sha256:recent", recent),
        )
        store.create_or_get_received(
            "tenant-a",
            "sha256:retired",
            "ing-retired",
            _record("tenant-a", "ing-retired", "sha256:retired", old),
        )
        store.mark_retired("tenant-a", "ing-retired", T, RetirementReason.CUSTOMER_REQUESTED)

        cutoff = T - dt.timedelta(days=90)
        candidates = list(store.list_expired_for_retention_sweep(cutoff))

        ids = {record.ingestion_id for record in candidates}
        assert ids == {"ing-old"}

    def test_results_are_deterministically_ordered(self) -> None:
        clock = [T]
        store = _store(clock)
        for i in range(5):
            received_at = T - dt.timedelta(days=200 - i)
            record = _record("tenant-a", f"ing-{i}", f"sha256:{i}", received_at)
            store.create_or_get_received("tenant-a", f"sha256:{i}", f"ing-{i}", record)

        cutoff = T
        first_pass = [r.ingestion_id for r in store.list_expired_for_retention_sweep(cutoff)]
        second_pass = [r.ingestion_id for r in store.list_expired_for_retention_sweep(cutoff)]
        assert first_pass == second_pass

        def _index(ingestion_id: str) -> int:
            return int(ingestion_id.split("-")[1])

        assert first_pass == sorted(first_pass, key=_index)


class TestUnknownAndCrossTenantIsolation:
    def test_get_unknown_ingestion_returns_none(self) -> None:
        clock = [T]
        store = _store(clock)
        assert store.get("tenant-a", "missing") is None

    def test_get_does_not_see_other_tenants_record(self) -> None:
        clock = [T]
        store = _store(clock)
        record = _record("tenant-a", "ing-1", "sha256:a")
        store.create_or_get_received("tenant-a", "sha256:a", "ing-1", record)
        assert store.get("tenant-b", "ing-1") is None

    def test_get_tombstone_unknown_returns_none(self) -> None:
        clock = [T]
        store = _store(clock)
        assert store.get_tombstone("tenant-a", "missing") is None

    def test_get_tombstone_does_not_see_other_tenants_tombstone(self) -> None:
        clock = [T]
        store = _store(clock)
        record = _record("tenant-a", "ing-1", "sha256:a")
        store.create_or_get_received("tenant-a", "sha256:a", "ing-1", record)
        store.mark_retired("tenant-a", "ing-1", T, RetirementReason.CUSTOMER_REQUESTED)
        store.mark_purged("tenant-a", "ing-1", T)

        assert store.get_tombstone("tenant-b", "ing-1") is None


class TestLifecycleValidationIsExceptionSafe:
    """`mark_retired`/`mark_purged` must construct and validate the
    complete candidate `IngestionRecord` (and, for purge, the `Tombstone`
    too) *before* mutating any store state -- `model_copy(update=...)`
    never validates, so building a lifecycle transition that way could
    silently commit an internally invalid record. Every test here proves
    both the rejection itself and that the store is left completely
    unchanged by it.
    """

    def test_retired_at_before_received_at_is_rejected_and_record_remains_received(
        self,
    ) -> None:
        clock = [T]
        store = _store(clock)
        received_at = T + dt.timedelta(hours=5)
        record = _record("tenant-a", "ing-1", "sha256:a", received_at=received_at)
        store.create_or_get_received("tenant-a", "sha256:a", "ing-1", record)

        with pytest.raises(ValidationError):
            # T precedes received_at.
            store.mark_retired("tenant-a", "ing-1", T, RetirementReason.CUSTOMER_REQUESTED)

        current = store.get("tenant-a", "ing-1")
        assert current is not None
        assert current.status is IngestionStatus.RECEIVED
        assert current.retired_at is None

    def test_invalid_runtime_retirement_reason_is_rejected_and_state_unchanged(self) -> None:
        clock = [T]
        store = _store(clock)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )

        with pytest.raises(ValidationError):
            store.mark_retired("tenant-a", "ing-1", T, "not_a_real_reason")  # type: ignore[arg-type]

        current = store.get("tenant-a", "ing-1")
        assert current is not None
        assert current.status is IngestionStatus.RECEIVED
        assert current.reason is None

    def test_deleted_at_before_retired_at_is_rejected(self) -> None:
        clock = [T]
        store = _store(clock)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )
        retired_at = T + dt.timedelta(hours=5)
        store.mark_retired("tenant-a", "ing-1", retired_at, RetirementReason.CUSTOMER_REQUESTED)

        with pytest.raises(ValidationError):
            # T precedes retired_at.
            store.mark_purged("tenant-a", "ing-1", T)

    def test_after_a_rejected_purge_the_record_remains_retired_and_no_tombstone_exists(
        self,
    ) -> None:
        clock = [T]
        store = _store(clock)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )
        retired_at = T + dt.timedelta(hours=5)
        store.mark_retired("tenant-a", "ing-1", retired_at, RetirementReason.CUSTOMER_REQUESTED)

        with pytest.raises(ValidationError):
            store.mark_purged("tenant-a", "ing-1", T)

        assert store.get_tombstone("tenant-a", "ing-1") is None
        # get() only ever returns "received" records -- re-derive current
        # state via mark_retired's own idempotent behavior, which returns
        # the existing record unchanged rather than re-retiring it.
        still_retired = store.mark_retired(
            "tenant-a", "ing-1", retired_at, RetirementReason.CUSTOMER_REQUESTED
        )
        assert still_retired is not None
        assert still_retired.status is IngestionStatus.RETIRED
        assert still_retired.retired_at == retired_at
        assert still_retired.reason is RetirementReason.CUSTOMER_REQUESTED

    def test_a_valid_purge_after_a_rejected_one_succeeds_and_is_consistent(self) -> None:
        clock = [T]
        store = _store(clock)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )
        retired_at = T + dt.timedelta(hours=5)
        store.mark_retired("tenant-a", "ing-1", retired_at, RetirementReason.CUSTOMER_REQUESTED)

        with pytest.raises(ValidationError):
            store.mark_purged("tenant-a", "ing-1", T)  # rejected: precedes retired_at

        valid_deleted_at = retired_at + dt.timedelta(hours=1)
        purged = store.mark_purged("tenant-a", "ing-1", valid_deleted_at)

        assert purged is not None
        assert purged.status is IngestionStatus.DELETED
        assert purged.deleted_at == valid_deleted_at
        assert purged.retired_at == retired_at
        assert purged.reason is RetirementReason.CUSTOMER_REQUESTED

        tombstone = store.get_tombstone("tenant-a", "ing-1")
        assert tombstone is not None
        assert tombstone.deleted_at == valid_deleted_at
        assert tombstone.retired_at == retired_at
        assert tombstone.reason is RetirementReason.CUSTOMER_REQUESTED

    def test_no_failure_path_leaves_a_deleted_record_without_its_tombstone(self) -> None:
        clock = [T]
        store = _store(clock)
        store.create_or_get_received(
            "tenant-a", "sha256:a", "ing-1", _record("tenant-a", "ing-1", "sha256:a")
        )
        retired_at = T + dt.timedelta(hours=5)
        store.mark_retired("tenant-a", "ing-1", retired_at, RetirementReason.CUSTOMER_REQUESTED)

        # A rejected attempt first, to prove it leaves no partial trace...
        with pytest.raises(ValidationError):
            store.mark_purged("tenant-a", "ing-1", T)
        assert store.get_tombstone("tenant-a", "ing-1") is None

        # ...then a valid purge must produce a deleted record *and* its
        # tombstone together, never one without the other.
        valid_deleted_at = retired_at + dt.timedelta(hours=1)
        purged = store.mark_purged("tenant-a", "ing-1", valid_deleted_at)
        assert purged is not None
        assert purged.status is IngestionStatus.DELETED
        assert store.get_tombstone("tenant-a", "ing-1") is not None
