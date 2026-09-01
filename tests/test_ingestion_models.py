"""Tests for `cloudops_guard.ingestion.models` domain types."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from cloudops_guard.ingestion.models import (
    IngestionRecord,
    IngestionStatus,
    RetirementReason,
    TokenRecord,
    TokenScope,
    Tombstone,
)

UTC = dt.UTC
NOW = dt.datetime(2026, 1, 1, tzinfo=UTC)
LATER = dt.datetime(2026, 1, 2, tzinfo=UTC)
EVEN_LATER = dt.datetime(2026, 1, 3, tzinfo=UTC)
NAIVE = dt.datetime(2026, 1, 1)


class _NaiveTzinfo(dt.tzinfo):
    """A tzinfo subclass that is attached but behaves naive -- exercises
    the `utcoffset() is None` branch of the timezone-aware validator, not
    just `tzinfo is None`.
    """

    def utcoffset(self, __dt: dt.datetime | None) -> dt.timedelta | None:
        return None

    def dst(self, __dt: dt.datetime | None) -> dt.timedelta | None:
        return None

    def tzname(self, __dt: dt.datetime | None) -> str | None:
        return None


def _received(**overrides: object) -> IngestionRecord:
    fields = dict(
        tenant_id="tenant-a",
        ingestion_id="ing-1",
        report_fingerprint="sha256:abc",
        received_at=NOW,
        status=IngestionStatus.RECEIVED,
    )
    fields.update(overrides)
    return IngestionRecord(**fields)


def _retired(**overrides: object) -> IngestionRecord:
    fields = dict(
        tenant_id="tenant-a",
        ingestion_id="ing-1",
        report_fingerprint="sha256:abc",
        received_at=NOW,
        status=IngestionStatus.RETIRED,
        reason=RetirementReason.CUSTOMER_REQUESTED,
        retired_at=LATER,
    )
    fields.update(overrides)
    return IngestionRecord(**fields)


def _deleted(**overrides: object) -> IngestionRecord:
    fields = dict(
        tenant_id="tenant-a",
        ingestion_id="ing-1",
        report_fingerprint="sha256:abc",
        received_at=NOW,
        status=IngestionStatus.DELETED,
        reason=RetirementReason.RETENTION_EXPIRED,
        retired_at=LATER,
        deleted_at=EVEN_LATER,
    )
    fields.update(overrides)
    return IngestionRecord(**fields)


class TestIngestionRecordTimezoneAwareness:
    def test_naive_received_at_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _received(received_at=NAIVE)

    def test_naive_retired_at_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _retired(retired_at=NAIVE)

    def test_naive_deleted_at_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _deleted(deleted_at=NAIVE)

    def test_attached_but_naive_tzinfo_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _received(received_at=NOW.replace(tzinfo=_NaiveTzinfo()))

    def test_aware_timestamps_accepted(self) -> None:
        _received()
        _retired()
        _deleted()


class TestIngestionRecordStatusConsistency:
    def test_received_must_not_carry_reason(self) -> None:
        with pytest.raises(ValidationError):
            _received(reason=RetirementReason.CUSTOMER_REQUESTED)

    def test_received_must_not_carry_retired_at(self) -> None:
        with pytest.raises(ValidationError):
            _received(retired_at=LATER)

    def test_received_must_not_carry_deleted_at(self) -> None:
        with pytest.raises(ValidationError):
            _received(deleted_at=LATER)

    def test_retired_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            _retired(reason=None)

    def test_retired_requires_retired_at(self) -> None:
        with pytest.raises(ValidationError):
            _retired(retired_at=None)

    def test_retired_must_not_carry_deleted_at(self) -> None:
        with pytest.raises(ValidationError):
            _retired(deleted_at=EVEN_LATER)

    def test_deleted_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            _deleted(reason=None)

    def test_deleted_requires_retired_at(self) -> None:
        with pytest.raises(ValidationError):
            _deleted(retired_at=None)

    def test_deleted_requires_deleted_at(self) -> None:
        with pytest.raises(ValidationError):
            _deleted(deleted_at=None)

    def test_retired_at_must_not_precede_received_at(self) -> None:
        with pytest.raises(ValidationError):
            _retired(received_at=LATER, retired_at=NOW)

    def test_retired_at_equal_to_received_at_is_allowed(self) -> None:
        _retired(received_at=NOW, retired_at=NOW)

    def test_deleted_at_must_not_precede_retired_at(self) -> None:
        with pytest.raises(ValidationError):
            _deleted(retired_at=EVEN_LATER, deleted_at=LATER)

    def test_deleted_at_equal_to_retired_at_is_allowed(self) -> None:
        _deleted(retired_at=LATER, deleted_at=LATER)


class TestIngestionRecordImmutability:
    def test_frozen_record_rejects_attribute_assignment(self) -> None:
        record = _received()
        with pytest.raises(ValidationError):
            record.status = IngestionStatus.RETIRED  # type: ignore[misc]

    def test_model_copy_produces_independent_instance(self) -> None:
        # `model_copy(update=...)` does not validate -- so this update is
        # deliberately chosen to be a *valid* transition on its own terms
        # (a `retired` record needs both `reason` and `retired_at`
        # supplied together, exactly as here), never a shortcut to an
        # internally invalid domain state. `InMemoryMetadataStore`
        # (`reference.py`) never uses `model_copy` for a status
        # transition itself; it always goes through the full, validating
        # constructor instead.
        record = _received()
        copy = record.model_copy(
            update={
                "status": IngestionStatus.RETIRED,
                "reason": RetirementReason.CUSTOMER_REQUESTED,
                "retired_at": LATER,
            }
        )
        assert record.status is IngestionStatus.RECEIVED
        assert copy.status is IngestionStatus.RETIRED
        assert copy.reason is RetirementReason.CUSTOMER_REQUESTED
        assert copy.retired_at == LATER


class TestTombstone:
    def _tombstone(self, **overrides: object) -> Tombstone:
        fields = dict(
            tenant_id="tenant-a",
            ingestion_id="ing-1",
            reason=RetirementReason.CUSTOMER_REQUESTED,
            retired_at=NOW,
            deleted_at=LATER,
        )
        fields.update(overrides)
        return Tombstone(**fields)

    def test_valid_tombstone_accepted(self) -> None:
        self._tombstone()

    def test_naive_retired_at_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._tombstone(retired_at=NAIVE)

    def test_naive_deleted_at_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._tombstone(deleted_at=NAIVE)

    def test_deleted_at_must_not_precede_retired_at(self) -> None:
        with pytest.raises(ValidationError):
            self._tombstone(retired_at=LATER, deleted_at=NOW)

    def test_deleted_at_equal_to_retired_at_is_allowed(self) -> None:
        self._tombstone(retired_at=LATER, deleted_at=LATER)

    def test_frozen(self) -> None:
        tombstone = self._tombstone()
        with pytest.raises(ValidationError):
            tombstone.reason = RetirementReason.RETENTION_EXPIRED  # type: ignore[misc]


class TestTokenRecord:
    def _token(self, **overrides: object) -> TokenRecord:
        fields = dict(
            lookup_id="lookup-1",
            secret_hash="argon2id$fake-hash-value",
            tenant_id="tenant-a",
            scopes={TokenScope.REPORTS_WRITE},
            revoked=False,
            created_at=NOW,
        )
        fields.update(overrides)
        return TokenRecord(**fields)

    def test_valid_token_accepted(self) -> None:
        token = self._token()
        assert token.scopes == frozenset({TokenScope.REPORTS_WRITE})

    def test_naive_created_at_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._token(created_at=NAIVE)

    def test_empty_scopes_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._token(scopes=set())

    def test_scopes_coerced_to_frozenset(self) -> None:
        token = self._token(scopes=[TokenScope.REPORTS_WRITE, TokenScope.REPORTS_READ])
        assert token.scopes == frozenset({TokenScope.REPORTS_WRITE, TokenScope.REPORTS_READ})

    def test_unknown_scope_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._token(scopes={"reports:admin"})

    def test_frozen(self) -> None:
        token = self._token()
        with pytest.raises(ValidationError):
            token.revoked = True  # type: ignore[misc]

    def test_no_plaintext_secret_field_exists(self) -> None:
        field_names = set(TokenRecord.model_fields)
        assert "secret" not in field_names
        assert "plaintext_secret" not in field_names
        assert "token" not in field_names
        assert "secret_hash" in field_names
