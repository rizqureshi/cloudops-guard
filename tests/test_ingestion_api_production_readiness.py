"""Tests for `cloudops_guard.ingestion_api.production_readiness` (Phase 4F
production-hardening review). Proves the fail-closed guarantee is real:
a fully in-memory configuration -- exactly what every test in this
repository, and any local developer run, legitimately uses -- must be
rejected, and a configuration built from adapters that are *not* this
package's own in-memory reference implementations must be accepted, so
the check is neither vacuous (never rejects anything) nor overreaching
(rejects everything, including a plausible future real adapter).
"""

from __future__ import annotations

import datetime as dt

import pytest

from cloudops_guard.ingestion.reference import (
    InMemoryAttemptLimiter,
    InMemoryMetadataStore,
    InMemoryReportBlobStore,
    InMemoryRequestRateLimiter,
    InMemoryTokenStore,
)
from cloudops_guard.ingestion_api.config import IngestionApiConfig
from cloudops_guard.ingestion_api.production_readiness import (
    ProductionConfigError,
    validate_production_config,
)
from tests.ingestion_api_support import FakeSecretVerifier, IngestionApiTestHarness


class _NotAnInMemoryAdapter:
    """A minimal stand-in shaped like a real, non-in-memory adapter would
    be -- deliberately **not** a subclass of any `InMemory*` reference
    class, so `validate_production_config` must not reject it merely for
    superficially resembling one.
    """


def _real_shaped_config(**overrides: object) -> IngestionApiConfig:
    harness = IngestionApiTestHarness()
    base = {
        "metadata_store": _NotAnInMemoryAdapter(),
        "blob_store": _NotAnInMemoryAdapter(),
        "token_store": _NotAnInMemoryAdapter(),
        "lookup_limiter": _NotAnInMemoryAdapter(),
        "source_limiter": _NotAnInMemoryAdapter(),
        "token_rate_limiter": _NotAnInMemoryAdapter(),
        "capabilities_rate_limiter": _NotAnInMemoryAdapter(),
        "clock": harness.clock,
        "request_id_generator": harness.request_ids,
        "ingestion_id_generator": harness.ingestion_ids,
        "retention_period": dt.timedelta(days=90),
    }
    base.update(overrides)
    return IngestionApiConfig(**base)  # type: ignore[arg-type]


class TestRejectsInMemoryConfigurations:
    def test_the_test_harnesss_own_config_is_rejected(self) -> None:
        # The exact configuration every other test in this repository
        # legitimately uses -- proves the check is not vacuous.
        harness = IngestionApiTestHarness()
        with pytest.raises(ProductionConfigError) as exc_info:
            validate_production_config(harness.config)
        message = str(exc_info.value)
        for field_name in (
            "metadata_store",
            "blob_store",
            "token_store",
            "lookup_limiter",
            "source_limiter",
            "token_rate_limiter",
            "capabilities_rate_limiter",
        ):
            assert field_name in message

    @pytest.mark.parametrize(
        ("field_name", "in_memory_value_factory"),
        [
            ("metadata_store", lambda h: InMemoryMetadataStore(clock=h.clock)),
            ("blob_store", lambda h: InMemoryReportBlobStore()),
            ("token_store", lambda h: InMemoryTokenStore(secret_verifier=FakeSecretVerifier())),
            ("lookup_limiter", lambda h: InMemoryAttemptLimiter(threshold=1000)),
            ("source_limiter", lambda h: InMemoryAttemptLimiter(threshold=1000)),
            ("token_rate_limiter", lambda h: InMemoryRequestRateLimiter(threshold=1000)),
            ("capabilities_rate_limiter", lambda h: InMemoryRequestRateLimiter(threshold=1000)),
        ],
    )
    def test_a_single_in_memory_field_among_otherwise_real_adapters_is_rejected(
        self, field_name: str, in_memory_value_factory
    ) -> None:
        harness = IngestionApiTestHarness()
        config = _real_shaped_config(**{field_name: in_memory_value_factory(harness)})
        with pytest.raises(ProductionConfigError) as exc_info:
            validate_production_config(config)
        assert field_name in str(exc_info.value)


class TestAcceptsNonInMemoryConfigurations:
    def test_a_config_built_from_non_in_memory_adapters_is_accepted(self) -> None:
        # Must not raise -- this is the whole point of the check: it
        # rejects the *known-unsafe* case, not merely everything.
        validate_production_config(_real_shaped_config())


class TestRejectsNonPositiveRetentionPeriod:
    @pytest.mark.parametrize(
        "retention_period",
        [dt.timedelta(0), dt.timedelta(seconds=-1), dt.timedelta(days=-90)],
    )
    def test_non_positive_retention_period_is_rejected(
        self, retention_period: dt.timedelta
    ) -> None:
        config = _real_shaped_config(retention_period=retention_period)
        with pytest.raises(ProductionConfigError, match="retention_period"):
            validate_production_config(config)

    def test_positive_retention_period_is_accepted(self) -> None:
        validate_production_config(_real_shaped_config(retention_period=dt.timedelta(days=1)))


class TestBothViolationCategoriesAreAggregated:
    """Phase 4F correction-pass regression tests: the original
    implementation raised as soon as it found an offending in-memory
    adapter, before ever checking `retention_period` -- so a configuration
    violating both categories at once silently omitted the retention
    problem from its error message. These tests pin the fix: every
    violation is collected before raising, in every combination.
    """

    def test_fully_in_memory_configuration_plus_zero_retention_reports_both(self) -> None:
        harness = IngestionApiTestHarness()
        config = IngestionApiConfig(
            metadata_store=harness.config.metadata_store,
            blob_store=harness.config.blob_store,
            token_store=harness.config.token_store,
            lookup_limiter=harness.config.lookup_limiter,
            source_limiter=harness.config.source_limiter,
            token_rate_limiter=harness.config.token_rate_limiter,
            capabilities_rate_limiter=harness.config.capabilities_rate_limiter,
            clock=harness.clock,
            request_id_generator=harness.request_ids,
            ingestion_id_generator=harness.ingestion_ids,
            retention_period=dt.timedelta(0),
        )
        with pytest.raises(ProductionConfigError) as exc_info:
            validate_production_config(config)
        message = str(exc_info.value)
        for field_name in (
            "metadata_store",
            "blob_store",
            "token_store",
            "lookup_limiter",
            "source_limiter",
            "token_rate_limiter",
            "capabilities_rate_limiter",
        ):
            assert field_name in message
        assert "retention_period" in message

    def test_one_in_memory_adapter_plus_negative_retention_reports_both(self) -> None:
        harness = IngestionApiTestHarness()
        config = _real_shaped_config(
            metadata_store=InMemoryMetadataStore(clock=harness.clock),
            retention_period=dt.timedelta(seconds=-1),
        )
        with pytest.raises(ProductionConfigError) as exc_info:
            validate_production_config(config)
        message = str(exc_info.value)
        assert "metadata_store" in message
        assert "retention_period" in message
        # No other adapter field was made to offend -- must not be
        # spuriously reported alongside the one that does.
        for field_name in (
            "blob_store",
            "token_store",
            "lookup_limiter",
            "source_limiter",
            "token_rate_limiter",
            "capabilities_rate_limiter",
        ):
            assert field_name not in message


class TestMalformedRetentionPeriodFailsClosedNotWithATypeError:
    """Phase 4F correction-pass regression tests: `config.retention_period
    <= dt.timedelta(0)` raised a raw, undocumented `TypeError` for a
    non-`timedelta` value instead of the documented `ProductionConfigError`
    -- these pin that every malformed value is now rejected via the
    documented exception type, and that an arbitrary object's `repr` is
    never surfaced in the error message.
    """

    def test_retention_period_none_raises_production_config_error_not_type_error(
        self,
    ) -> None:
        config = _real_shaped_config(retention_period=None)  # type: ignore[arg-type]
        with pytest.raises(ProductionConfigError, match="retention_period"):
            validate_production_config(config)

    def test_string_retention_period_raises_production_config_error_not_type_error(
        self,
    ) -> None:
        config = _real_shaped_config(retention_period="90 days")  # type: ignore[arg-type]
        with pytest.raises(ProductionConfigError, match="retention_period"):
            validate_production_config(config)

    def test_sentinel_secret_in_retention_periods_repr_never_appears_in_the_error(
        self,
    ) -> None:
        sentinel = "SENTINEL-SECRET-3f9c1b7a"

        class _MalformedRetentionWithLeakyRepr:
            def __repr__(self) -> str:  # pragma: no cover - exercised via str(exc)
                return f"<retention leaking {sentinel}>"

        config = _real_shaped_config(
            retention_period=_MalformedRetentionWithLeakyRepr()  # type: ignore[arg-type]
        )
        with pytest.raises(ProductionConfigError, match="retention_period") as exc_info:
            validate_production_config(config)
        assert sentinel not in str(exc_info.value)
