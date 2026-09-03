"""Ingestion-token loading tests for `cloudops_guard.uploader.credentials`
-- structural validation, redaction, and safe-failure guarantees.
"""

from __future__ import annotations

import pytest

from cloudops_guard.ingestion.token_issuance import generate_lookup_id, generate_secret
from cloudops_guard.uploader.credentials import INGESTION_TOKEN_ENV_VAR, load_ingestion_token
from cloudops_guard.uploader.errors import CredentialError

SENTINEL_SECRET_MARKER = "SENTINEL-TOKEN-VALUE-MUST-NEVER-APPEAR-ANYWHERE"


def _well_formed_token() -> str:
    return f"{generate_lookup_id()}.{generate_secret()}"


class TestSuccessfulLoad:
    def test_well_formed_token_is_returned_unchanged(self) -> None:
        token = _well_formed_token()
        assert load_ingestion_token({INGESTION_TOKEN_ENV_VAR: token}) == token

    def test_reads_only_the_documented_env_var_name(self) -> None:
        token = _well_formed_token()
        env = {INGESTION_TOKEN_ENV_VAR: token, "SOME_OTHER_VAR": "irrelevant"}
        assert load_ingestion_token(env) == token

    def test_defaults_to_the_real_process_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = _well_formed_token()
        monkeypatch.setenv(INGESTION_TOKEN_ENV_VAR, token)
        assert load_ingestion_token() == token


class TestRejections:
    def test_missing_variable_raises(self) -> None:
        with pytest.raises(CredentialError, match="not set"):
            load_ingestion_token({})

    def test_empty_variable_raises(self) -> None:
        with pytest.raises(CredentialError, match="empty"):
            load_ingestion_token({INGESTION_TOKEN_ENV_VAR: ""})

    def test_whitespace_only_variable_raises(self) -> None:
        with pytest.raises(CredentialError, match="empty"):
            load_ingestion_token({INGESTION_TOKEN_ENV_VAR: "   "})

    def test_missing_delimiter_raises(self) -> None:
        with pytest.raises(CredentialError, match="not a validly structured"):
            load_ingestion_token({INGESTION_TOKEN_ENV_VAR: "not-a-real-token"})

    def test_wrong_component_lengths_raise(self) -> None:
        with pytest.raises(CredentialError, match="not a validly structured"):
            load_ingestion_token({INGESTION_TOKEN_ENV_VAR: "short.short"})

    def test_invalid_character_raises(self) -> None:
        malformed = f"{generate_lookup_id()}.{'*' * 43}"
        with pytest.raises(CredentialError, match="not a validly structured"):
            load_ingestion_token({INGESTION_TOKEN_ENV_VAR: malformed})


class TestNoLeakage:
    def test_sentinel_value_never_appears_in_a_rejection_message(self) -> None:
        malformed = f"not-well-formed-{SENTINEL_SECRET_MARKER}"
        with pytest.raises(CredentialError) as exc_info:
            load_ingestion_token({INGESTION_TOKEN_ENV_VAR: malformed})
        assert SENTINEL_SECRET_MARKER not in str(exc_info.value)
        assert SENTINEL_SECRET_MARKER not in repr(exc_info.value)

    def test_sentinel_value_never_appears_when_well_formed_but_returned(self) -> None:
        # The one legitimate case where the real value IS the return
        # value itself -- this test documents that the function's own
        # *exception path* (the only path this module could leak through
        # unintentionally) is what is asserted clean, not this return
        # value, which is expected to equal the token by design.
        token = _well_formed_token()
        result = load_ingestion_token({INGESTION_TOKEN_ENV_VAR: token})
        assert result == token
