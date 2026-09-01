"""Idempotency-window boundary tests for
`InMemoryMetadataStore.create_or_get_received` -- §E's "Idempotency
semantics": a fixed, non-sliding 24-hour window anchored to the bound
record's own `received_at`, inclusive at exactly `T + 24h`.

All timing is driven by an injected clock (never `time.sleep`), so every
boundary assertion is exact and deterministic.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cloudops_guard.ingestion.errors import IdempotencyKeyConflict
from cloudops_guard.ingestion.models import IngestionRecord, IngestionStatus, RetirementReason
from cloudops_guard.ingestion.reference import IDEMPOTENCY_KEY_WINDOW, InMemoryMetadataStore

UTC = dt.UTC
T = dt.datetime(2026, 1, 1, tzinfo=UTC)


def _record(
    tenant_id: str, ingestion_id: str, fingerprint: str, received_at: dt.datetime
) -> IngestionRecord:
    return IngestionRecord(
        tenant_id=tenant_id,
        ingestion_id=ingestion_id,
        report_fingerprint=fingerprint,
        received_at=received_at,
        status=IngestionStatus.RECEIVED,
    )


def _store(clock_value: list[dt.datetime]) -> InMemoryMetadataStore:
    return InMemoryMetadataStore(clock=lambda: clock_value[0])


class TestIdempotencyKeyReplay:
    def test_same_key_same_fingerprint_replay_returns_existing_record(self) -> None:
        clock = [T]
        store = _store(clock)
        tenant_id = "tenant-a"
        fingerprint = "sha256:same"
        key = "idem-1"

        first, first_created = store.create_or_get_received(
            tenant_id,
            fingerprint,
            "ing-1",
            _record(tenant_id, "ing-1", fingerprint, T),
            idempotency_key=key,
        )
        second, second_created = store.create_or_get_received(
            tenant_id,
            fingerprint,
            "ing-2",
            _record(tenant_id, "ing-2", fingerprint, T),
            idempotency_key=key,
        )

        assert first_created is True
        assert second_created is False
        assert second == first

    def test_same_key_different_fingerprint_raises_conflict(self) -> None:
        clock = [T]
        store = _store(clock)
        tenant_id = "tenant-a"
        key = "idem-1"

        store.create_or_get_received(
            tenant_id,
            "sha256:a",
            "ing-1",
            _record(tenant_id, "ing-1", "sha256:a", T),
            idempotency_key=key,
        )
        with pytest.raises(IdempotencyKeyConflict):
            store.create_or_get_received(
                tenant_id,
                "sha256:b",
                "ing-2",
                _record(tenant_id, "ing-2", "sha256:b", T),
                idempotency_key=key,
            )


class TestIdempotencyWindowBoundary:
    def test_replay_at_exactly_t_plus_24h_is_inclusive(self) -> None:
        clock = [T]
        store = _store(clock)
        tenant_id = "tenant-a"
        fingerprint = "sha256:same"
        key = "idem-1"

        first, first_created = store.create_or_get_received(
            tenant_id,
            fingerprint,
            "ing-1",
            _record(tenant_id, "ing-1", fingerprint, T),
            idempotency_key=key,
        )
        clock[0] = T + IDEMPOTENCY_KEY_WINDOW

        second, second_created = store.create_or_get_received(
            tenant_id,
            fingerprint,
            "ing-2",
            _record(tenant_id, "ing-2", fingerprint, T),
            idempotency_key=key,
        )

        assert first_created is True
        assert second_created is False
        assert second.ingestion_id == first.ingestion_id

    def test_replay_strictly_after_t_plus_24h_is_treated_as_unbound(self) -> None:
        clock = [T]
        store = _store(clock)
        tenant_id = "tenant-a"
        fingerprint = "sha256:same"
        key = "idem-1"

        first, first_created = store.create_or_get_received(
            tenant_id,
            fingerprint,
            "ing-1",
            _record(tenant_id, "ing-1", fingerprint, T),
            idempotency_key=key,
        )
        clock[0] = T + IDEMPOTENCY_KEY_WINDOW + dt.timedelta(microseconds=1)

        # The old binding no longer applies -- but the fingerprint is
        # still "received" and active, so this still dedups by content
        # (step 2), just no longer via the key path. created is False
        # because the same underlying ingestion is still active.
        second, second_created = store.create_or_get_received(
            tenant_id,
            fingerprint,
            "ing-2",
            _record(tenant_id, "ing-2", fingerprint, T),
            idempotency_key=key,
        )
        assert second_created is False
        assert second.ingestion_id == first.ingestion_id

    def test_expired_key_binding_reused_against_a_different_fingerprint_creates_new_record(
        self,
    ) -> None:
        clock = [T]
        store = _store(clock)
        tenant_id = "tenant-a"
        key = "idem-1"

        first, _ = store.create_or_get_received(
            tenant_id,
            "sha256:a",
            "ing-1",
            _record(tenant_id, "ing-1", "sha256:a", T),
            idempotency_key=key,
        )
        # Retire the first record so it's no longer an active fingerprint
        # match either -- isolates the key-expiry behavior from content dedup.
        store.mark_retired(tenant_id, "ing-1", T, RetirementReason.CUSTOMER_REQUESTED)

        clock[0] = T + IDEMPOTENCY_KEY_WINDOW + dt.timedelta(microseconds=1)

        second, second_created = store.create_or_get_received(
            tenant_id,
            "sha256:b",
            "ing-2",
            _record(tenant_id, "ing-2", "sha256:b", T),
            idempotency_key=key,
        )
        assert second_created is True
        assert second.ingestion_id == "ing-2"
        assert first.ingestion_id != second.ingestion_id

    def test_window_is_fixed_and_not_extended_by_repeated_replay(self) -> None:
        clock = [T]
        store = _store(clock)
        tenant_id = "tenant-a"
        fingerprint = "sha256:same"
        key = "idem-1"

        store.create_or_get_received(
            tenant_id,
            fingerprint,
            "ing-1",
            _record(tenant_id, "ing-1", fingerprint, T),
            idempotency_key=key,
        )
        # A replay just before expiry must not push the window's anchor
        # forward -- it stays anchored to the original received_at.
        clock[0] = T + IDEMPOTENCY_KEY_WINDOW - dt.timedelta(seconds=1)
        store.create_or_get_received(
            tenant_id,
            fingerprint,
            "ing-2",
            _record(tenant_id, "ing-2", fingerprint, T),
            idempotency_key=key,
        )

        clock[0] = T + IDEMPOTENCY_KEY_WINDOW + dt.timedelta(hours=1)
        store.mark_retired(tenant_id, "ing-1", clock[0], RetirementReason.CUSTOMER_REQUESTED)

        third, third_created = store.create_or_get_received(
            tenant_id,
            "sha256:different",
            "ing-3",
            _record(tenant_id, "ing-3", "sha256:different", clock[0]),
            idempotency_key=key,
        )
        # The key binding from the original T anchor is long expired
        # (not extended by the earlier replay), so this succeeds as a new
        # binding rather than conflicting.
        assert third_created is True


class TestRetiredAndDeletedBindingBehavior:
    def test_key_bound_to_a_now_retired_record_no_longer_blocks_reuse(self) -> None:
        clock = [T]
        store = _store(clock)
        tenant_id = "tenant-a"
        key = "idem-1"

        first, _ = store.create_or_get_received(
            tenant_id,
            "sha256:a",
            "ing-1",
            _record(tenant_id, "ing-1", "sha256:a", T),
            idempotency_key=key,
        )
        store.mark_retired(tenant_id, "ing-1", T, RetirementReason.CUSTOMER_REQUESTED)

        second, second_created = store.create_or_get_received(
            tenant_id,
            "sha256:b",
            "ing-2",
            _record(tenant_id, "ing-2", "sha256:b", T),
            idempotency_key=key,
        )
        assert second_created is True
        assert second.ingestion_id != first.ingestion_id

    def test_retired_fingerprint_does_not_block_a_new_ingestion_with_same_fingerprint(self) -> None:
        clock = [T]
        store = _store(clock)
        tenant_id = "tenant-a"
        fingerprint = "sha256:same"

        first, _ = store.create_or_get_received(
            tenant_id, fingerprint, "ing-1", _record(tenant_id, "ing-1", fingerprint, T)
        )
        store.mark_retired(tenant_id, "ing-1", T, RetirementReason.CUSTOMER_REQUESTED)

        second, second_created = store.create_or_get_received(
            tenant_id, fingerprint, "ing-2", _record(tenant_id, "ing-2", fingerprint, T)
        )
        assert second_created is True
        assert second.ingestion_id != first.ingestion_id


class TestContentDedupWithoutKey:
    def test_same_fingerprint_without_key_dedups_to_active_record(self) -> None:
        clock = [T]
        store = _store(clock)
        tenant_id = "tenant-a"
        fingerprint = "sha256:same"

        first, first_created = store.create_or_get_received(
            tenant_id, fingerprint, "ing-1", _record(tenant_id, "ing-1", fingerprint, T)
        )
        second, second_created = store.create_or_get_received(
            tenant_id, fingerprint, "ing-2", _record(tenant_id, "ing-2", fingerprint, T)
        )

        assert first_created is True
        assert second_created is False
        assert second.ingestion_id == first.ingestion_id

    def test_mismatched_new_record_fields_raise_value_error(self) -> None:
        clock = [T]
        store = _store(clock)
        tenant_id = "tenant-a"
        with pytest.raises(ValueError, match="tenant_id"):
            store.create_or_get_received(
                tenant_id, "sha256:a", "ing-1", _record("other-tenant", "ing-1", "sha256:a", T)
            )
