"""Fail-closed strict environment-variable validation
(`production_config.load_settings_from_environment`, task 7/9's own
requirement) -- every required variable missing, empty, malformed, or
naming a disapproved region must raise, never silently fall back to a
default.
"""

from __future__ import annotations

import pytest

from cloudops_guard.ingestion_azure.errors import ProductionEnvironmentError
from cloudops_guard.ingestion_azure.production_config import (
    EXPECTED_REGION,
    load_settings_from_environment,
)

_ALL_REQUIRED_VARS = {
    "COG_INGESTION_REGION": "canadacentral",
    "COG_METADATA_DB_CONNINFO": "postgresql://user:pass@host/db",
    "COG_TOKEN_DB_CONNINFO": "postgresql://user:pass@host/db",
    "COG_BLOB_ACCOUNT_URL": "https://example.blob.core.windows.net",
    "COG_BLOB_CONTAINER_NAME": "reports",
    "COG_LIMITER_DB_CONNINFO": "postgresql://user:pass@host/db",
    "COG_LIMITER_HMAC_KEY": "x" * 32,
    "COG_RETENTION_PERIOD_SECONDS": "7776000",
    "COG_LOOKUP_LIMITER_THRESHOLD": "10",
    "COG_LOOKUP_LIMITER_WINDOW_SECONDS": "900",
    "COG_SOURCE_LIMITER_THRESHOLD": "30",
    "COG_SOURCE_LIMITER_WINDOW_SECONDS": "300",
    "COG_TOKEN_RATE_LIMITER_THRESHOLD": "60",
    "COG_TOKEN_RATE_LIMITER_WINDOW_SECONDS": "60",
    "COG_CAPABILITIES_RATE_LIMITER_THRESHOLD": "60",
    "COG_CAPABILITIES_RATE_LIMITER_WINDOW_SECONDS": "60",
}


@pytest.fixture()
def complete_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _ALL_REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)


def test_valid_complete_environment_loads_successfully(complete_environment: None) -> None:
    settings = load_settings_from_environment()
    assert settings.region == EXPECTED_REGION
    assert settings.lookup_threshold == 10


@pytest.mark.parametrize("missing_var", sorted(_ALL_REQUIRED_VARS))
def test_missing_variable_fails_closed(
    complete_environment: None, monkeypatch: pytest.MonkeyPatch, missing_var: str
) -> None:
    monkeypatch.delenv(missing_var, raising=False)
    with pytest.raises(ProductionEnvironmentError):
        load_settings_from_environment()


@pytest.mark.parametrize("empty_var", sorted(_ALL_REQUIRED_VARS))
def test_empty_variable_fails_closed(
    complete_environment: None, monkeypatch: pytest.MonkeyPatch, empty_var: str
) -> None:
    monkeypatch.setenv(empty_var, "")
    with pytest.raises(ProductionEnvironmentError):
        load_settings_from_environment()


class TestRegionIsHardEnforced:
    @pytest.mark.parametrize(
        "region", ["eastus", "westeurope", "CanadaCentral", "canada-central", "", "canadaeast"]
    )
    def test_any_non_canadacentral_region_is_refused(
        self, complete_environment: None, monkeypatch: pytest.MonkeyPatch, region: str
    ) -> None:
        monkeypatch.setenv("COG_INGESTION_REGION", region)
        with pytest.raises(ProductionEnvironmentError):
            load_settings_from_environment()


class TestNumericFieldsFailClosed:
    @pytest.mark.parametrize(
        "var", ["COG_LOOKUP_LIMITER_THRESHOLD", "COG_RETENTION_PERIOD_SECONDS"]
    )
    @pytest.mark.parametrize("bad_value", ["not-a-number", "1.5", "-1", "0", "  ", "NaN"])
    def test_malformed_or_non_positive_numeric_value_raises(
        self,
        complete_environment: None,
        monkeypatch: pytest.MonkeyPatch,
        var: str,
        bad_value: str,
    ) -> None:
        monkeypatch.setenv(var, bad_value)
        with pytest.raises(ProductionEnvironmentError):
            load_settings_from_environment()


class TestHmacKeyMinimumLength:
    def test_short_hmac_key_is_refused(
        self, complete_environment: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COG_LIMITER_HMAC_KEY", "too-short")
        with pytest.raises(ProductionEnvironmentError):
            load_settings_from_environment()

    def test_exactly_32_bytes_is_accepted(
        self, complete_environment: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COG_LIMITER_HMAC_KEY", "a" * 32)
        settings = load_settings_from_environment()
        assert len(settings.limiter_hmac_key) == 32


def test_no_secret_value_appears_in_error_message(
    complete_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 7: "Does not expose secrets in configuration errors" -- a
    malformed value that happens to embed a secret-shaped sentinel (e.g.
    an operator pasting a connection string or key into the wrong
    variable) must never have that sentinel echoed back in the raised
    exception's message, only the offending *variable's name*.
    """
    sentinel = "SENTINEL-DB-PASSWORD-3f9c1b7a"
    monkeypatch.setenv("COG_RETENTION_PERIOD_SECONDS", f"not-a-number-{sentinel}")
    with pytest.raises(ProductionEnvironmentError) as exc_info:
        load_settings_from_environment()
    message = str(exc_info.value)
    assert sentinel not in message
    assert "COG_RETENTION_PERIOD_SECONDS" in message
