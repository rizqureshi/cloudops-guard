"""Fail-closed validation for the two command-specific, narrow
configuration loaders (`production_config.load_migration_settings_from_
environment`/`load_limiter_cleanup_settings_from_environment`,
correction-pass item 3) -- mirrors `test_production_config.py`'s own
coverage of the full 15-variable `load_settings_from_environment`, but
proves the key structural guarantee that motivated adding these loaders
in the first place: each one requires *only* its own small variable set,
never the other 12-13 variables `load_settings_from_environment` needs.
"""

from __future__ import annotations

import os

import pytest

from cloudops_guard.ingestion_azure.errors import ProductionEnvironmentError
from cloudops_guard.ingestion_azure.production_config import (
    EXPECTED_REGION,
    load_limiter_cleanup_settings_from_environment,
    load_migration_settings_from_environment,
)

_MIGRATION_VARS = {
    "COG_INGESTION_REGION": "canadacentral",
    "COG_METADATA_DB_CONNINFO": "postgresql://user:pass@host/db",
}

_LIMITER_CLEANUP_VARS = {
    "COG_INGESTION_REGION": "canadacentral",
    "COG_LIMITER_DB_CONNINFO": "postgresql://user:pass@host/db",
    "COG_LIMITER_HMAC_KEY": "x" * 32,
}

#: Variables `load_settings_from_environment` requires that neither
#: narrow loader may ever need -- if setting one of these were ever
#: required, that would mean a narrow loader had regressed into needing
#: the full 15-variable set.
_VARS_NEITHER_NARROW_LOADER_NEEDS = (
    "COG_TOKEN_DB_CONNINFO",
    "COG_BLOB_ACCOUNT_URL",
    "COG_BLOB_CONTAINER_NAME",
    "COG_RETENTION_PERIOD_SECONDS",
    "COG_LOOKUP_LIMITER_THRESHOLD",
    "COG_LOOKUP_LIMITER_WINDOW_SECONDS",
    "COG_SOURCE_LIMITER_THRESHOLD",
    "COG_SOURCE_LIMITER_WINDOW_SECONDS",
    "COG_TOKEN_RATE_LIMITER_THRESHOLD",
    "COG_TOKEN_RATE_LIMITER_WINDOW_SECONDS",
    "COG_CAPABILITIES_RATE_LIMITER_THRESHOLD",
    "COG_CAPABILITIES_RATE_LIMITER_WINDOW_SECONDS",
)


@pytest.fixture()
def migration_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _MIGRATION_VARS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture()
def limiter_cleanup_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _LIMITER_CLEANUP_VARS.items():
        monkeypatch.setenv(key, value)


class TestMigrationSettingsLoader:
    def test_valid_minimal_environment_loads_successfully(
        self, migration_environment: None
    ) -> None:
        settings = load_migration_settings_from_environment()
        assert settings.region == EXPECTED_REGION
        assert settings.metadata_db_conninfo == _MIGRATION_VARS["COG_METADATA_DB_CONNINFO"]

    @pytest.mark.parametrize("missing_var", sorted(_MIGRATION_VARS))
    def test_missing_required_variable_fails_closed(
        self, migration_environment: None, monkeypatch: pytest.MonkeyPatch, missing_var: str
    ) -> None:
        monkeypatch.delenv(missing_var, raising=False)
        with pytest.raises(ProductionEnvironmentError):
            load_migration_settings_from_environment()

    @pytest.mark.parametrize("empty_var", sorted(_MIGRATION_VARS))
    def test_empty_required_variable_fails_closed(
        self, migration_environment: None, monkeypatch: pytest.MonkeyPatch, empty_var: str
    ) -> None:
        monkeypatch.setenv(empty_var, "")
        with pytest.raises(ProductionEnvironmentError):
            load_migration_settings_from_environment()

    @pytest.mark.parametrize(
        "region", ["eastus", "westeurope", "CanadaCentral", "canada-central", ""]
    )
    def test_non_canadacentral_region_is_refused(
        self, migration_environment: None, monkeypatch: pytest.MonkeyPatch, region: str
    ) -> None:
        monkeypatch.setenv("COG_INGESTION_REGION", region)
        with pytest.raises(ProductionEnvironmentError):
            load_migration_settings_from_environment()

    @pytest.mark.parametrize("unrelated_var", _VARS_NEITHER_NARROW_LOADER_NEEDS)
    def test_never_requires_a_variable_the_full_loader_alone_needs(
        self, migration_environment: None, unrelated_var: str
    ) -> None:
        """The structural guarantee this loader exists for: an operator
        wiring only the migration identity's own two secrets (never the
        blob/token/limiter configuration the migration job structurally
        cannot use) must be sufficient -- this must succeed with *none*
        of the full loader's other 12 variables set at all.
        """
        assert unrelated_var not in os.environ  # sanity: fixture never sets these
        settings = load_migration_settings_from_environment()
        assert settings.metadata_db_conninfo


class TestLimiterCleanupSettingsLoader:
    def test_valid_minimal_environment_loads_successfully(
        self, limiter_cleanup_environment: None
    ) -> None:
        settings = load_limiter_cleanup_settings_from_environment()
        assert settings.region == EXPECTED_REGION
        assert settings.limiter_db_conninfo == _LIMITER_CLEANUP_VARS["COG_LIMITER_DB_CONNINFO"]
        assert settings.limiter_hmac_key == _LIMITER_CLEANUP_VARS["COG_LIMITER_HMAC_KEY"].encode()

    @pytest.mark.parametrize("missing_var", sorted(_LIMITER_CLEANUP_VARS))
    def test_missing_required_variable_fails_closed(
        self,
        limiter_cleanup_environment: None,
        monkeypatch: pytest.MonkeyPatch,
        missing_var: str,
    ) -> None:
        monkeypatch.delenv(missing_var, raising=False)
        with pytest.raises(ProductionEnvironmentError):
            load_limiter_cleanup_settings_from_environment()

    @pytest.mark.parametrize("empty_var", sorted(_LIMITER_CLEANUP_VARS))
    def test_empty_required_variable_fails_closed(
        self, limiter_cleanup_environment: None, monkeypatch: pytest.MonkeyPatch, empty_var: str
    ) -> None:
        monkeypatch.setenv(empty_var, "")
        with pytest.raises(ProductionEnvironmentError):
            load_limiter_cleanup_settings_from_environment()

    @pytest.mark.parametrize(
        "region", ["eastus", "westeurope", "CanadaCentral", "canada-central", ""]
    )
    def test_non_canadacentral_region_is_refused(
        self, limiter_cleanup_environment: None, monkeypatch: pytest.MonkeyPatch, region: str
    ) -> None:
        monkeypatch.setenv("COG_INGESTION_REGION", region)
        with pytest.raises(ProductionEnvironmentError):
            load_limiter_cleanup_settings_from_environment()

    def test_short_hmac_key_is_refused(
        self, limiter_cleanup_environment: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COG_LIMITER_HMAC_KEY", "too-short")
        with pytest.raises(ProductionEnvironmentError):
            load_limiter_cleanup_settings_from_environment()

    @pytest.mark.parametrize("unrelated_var", _VARS_NEITHER_NARROW_LOADER_NEEDS)
    def test_never_requires_a_variable_the_full_loader_alone_needs(
        self, limiter_cleanup_environment: None, unrelated_var: str
    ) -> None:
        assert unrelated_var not in os.environ  # sanity: fixture never sets these
        settings = load_limiter_cleanup_settings_from_environment()
        assert settings.limiter_db_conninfo

    def test_never_requires_the_metadata_db_conninfo_either(
        self, limiter_cleanup_environment: None
    ) -> None:
        """`COG_METADATA_DB_CONNINFO` is the migration loader's own
        variable, not one of the 12 shared "full-loader-only" variables
        above -- checked separately since limiter-cleanup must not need
        it either.
        """
        settings = load_limiter_cleanup_settings_from_environment()
        assert settings.limiter_db_conninfo
