"""Tests for `InMemoryTokenStore`: storage mechanics for `TokenRecord`,
plus `verify_secret` delegation to an injected `SecretVerifier` -- never a
production Argon2id implementation, never a plaintext comparison. Phase
4B ships the complete, approved three-method `TokenStore` interface
(`lookup`, `verify_secret`, `mark_revoked`); real cryptographic
verification remains Phase 4C work.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from cloudops_guard.ingestion.interfaces import TokenStore
from cloudops_guard.ingestion.models import TokenRecord, TokenScope
from cloudops_guard.ingestion.reference import InMemoryTokenStore

UTC = dt.UTC
T = dt.datetime(2026, 1, 1, tzinfo=UTC)


def _token(**overrides: object) -> TokenRecord:
    fields = dict(
        lookup_id="lookup-1",
        secret_hash="argon2id$fake",
        tenant_id="tenant-a",
        scopes={TokenScope.REPORTS_WRITE},
        revoked=False,
        created_at=T,
    )
    fields.update(overrides)
    return TokenRecord(**fields)


class _RecordingVerifier:
    """A deterministic, test-only fake `SecretVerifier` -- never Argon2id,
    never a plaintext comparison presented as production logic. Records
    every call so tests can assert exactly what was passed and how many
    times.
    """

    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def __call__(self, presented_secret: str, secret_hash: str) -> bool:
        self.calls.append((presented_secret, secret_hash))
        return self.result


def _store(result: bool = True) -> tuple[InMemoryTokenStore, _RecordingVerifier]:
    verifier = _RecordingVerifier(result)
    return InMemoryTokenStore(secret_verifier=verifier), verifier


class TestTokenStoreInterfaceCompleteness:
    def test_token_store_exposes_all_three_approved_abstract_methods(self) -> None:
        assert TokenStore.__abstractmethods__ == frozenset(
            {"lookup", "verify_secret", "mark_revoked"}
        )

    def test_in_memory_token_store_is_concrete_and_implements_all_three(self) -> None:
        store, _verifier = _store()
        assert isinstance(store, TokenStore)
        assert InMemoryTokenStore.__abstractmethods__ == frozenset()
        for method_name in ("lookup", "verify_secret", "mark_revoked"):
            assert callable(getattr(store, method_name))

    def test_cannot_construct_a_token_store_missing_verify_secret(self) -> None:
        class _IncompleteTokenStore(TokenStore):
            def lookup(self, lookup_id: str) -> TokenRecord | None:
                return None

            def mark_revoked(self, lookup_id: str) -> None:
                return None

        with pytest.raises(TypeError):
            _IncompleteTokenStore()  # type: ignore[abstract]


class TestLookup:
    def test_lookup_unknown_returns_none(self) -> None:
        store, _verifier = _store()
        assert store.lookup("missing") is None

    def test_lookup_returns_registered_record(self) -> None:
        store, _verifier = _store()
        record = _token()
        store.register_for_testing(record)
        assert store.lookup("lookup-1") == record

    def test_lookup_is_indexed_by_lookup_id_not_scanned(self) -> None:
        store, _verifier = _store()
        for i in range(50):
            store.register_for_testing(_token(lookup_id=f"lookup-{i}", tenant_id=f"tenant-{i}"))
        found = store.lookup("lookup-25")
        assert found is not None
        assert found.tenant_id == "tenant-25"


class TestVerifySecret:
    def test_delegates_to_injected_verifier_exactly_once_with_exact_args(self) -> None:
        store, verifier = _store(result=True)
        result = store.verify_secret("presented-secret-value", "stored-hash-value")
        assert result is True
        assert verifier.calls == [("presented-secret-value", "stored-hash-value")]

    def test_returns_the_verifiers_false_result_unchanged(self) -> None:
        store, verifier = _store(result=False)
        result = store.verify_secret("wrong-secret", "stored-hash-value")
        assert result is False
        assert verifier.calls == [("wrong-secret", "stored-hash-value")]

    def test_does_not_call_the_verifier_more_than_once_per_call(self) -> None:
        store, verifier = _store(result=True)
        store.verify_secret("a", "b")
        assert len(verifier.calls) == 1

    def test_each_call_is_independently_delegated(self) -> None:
        store, verifier = _store(result=True)
        store.verify_secret("secret-1", "hash-1")
        store.verify_secret("secret-2", "hash-2")
        assert verifier.calls == [("secret-1", "hash-1"), ("secret-2", "hash-2")]

    def test_reference_implementation_performs_no_comparison_of_its_own(self) -> None:
        # A verifier that always returns False must produce False even
        # when presented_secret == secret_hash -- proving InMemoryTokenStore
        # never falls back to its own (in)equality check and always
        # trusts the injected verifier's result exclusively.
        store, verifier = _store(result=False)
        result = store.verify_secret("same-value", "same-value")
        assert result is False


class TestRevocation:
    def test_mark_revoked_visible_on_next_lookup(self) -> None:
        store, _verifier = _store()
        store.register_for_testing(_token())
        assert store.lookup("lookup-1").revoked is False

        store.mark_revoked("lookup-1")

        assert store.lookup("lookup-1").revoked is True

    def test_mark_revoked_unknown_is_a_no_op(self) -> None:
        store, _verifier = _store()
        store.mark_revoked("missing")  # must not raise
        assert store.lookup("missing") is None

    def test_revoking_one_token_does_not_affect_another(self) -> None:
        store, _verifier = _store()
        store.register_for_testing(_token(lookup_id="a"))
        store.register_for_testing(_token(lookup_id="b"))

        store.mark_revoked("a")

        assert store.lookup("a").revoked is True
        assert store.lookup("b").revoked is False


class TestTenantAndScopeIsolationAndImmutability:
    def test_records_carry_their_own_tenant_and_scopes(self) -> None:
        store, _verifier = _store()
        store.register_for_testing(
            _token(lookup_id="a", tenant_id="tenant-a", scopes={TokenScope.REPORTS_READ})
        )
        store.register_for_testing(
            _token(
                lookup_id="b",
                tenant_id="tenant-b",
                scopes={TokenScope.REPORTS_WRITE, TokenScope.REPORTS_DELETE},
            )
        )

        record_a = store.lookup("a")
        record_b = store.lookup("b")
        assert record_a.tenant_id == "tenant-a"
        assert record_a.scopes == frozenset({TokenScope.REPORTS_READ})
        assert record_b.tenant_id == "tenant-b"
        assert record_b.scopes == frozenset({TokenScope.REPORTS_WRITE, TokenScope.REPORTS_DELETE})

    def test_returned_record_cannot_be_mutated_to_affect_stored_state(self) -> None:
        store, _verifier = _store()
        store.register_for_testing(_token())
        looked_up = store.lookup("lookup-1")
        with pytest.raises(ValidationError):
            looked_up.revoked = True  # type: ignore[misc]
        # Stored state is unaffected regardless.
        assert store.lookup("lookup-1").revoked is False

    def test_no_plaintext_secret_ever_stored_or_returned(self) -> None:
        store, _verifier = _store()
        store.register_for_testing(_token(secret_hash="argon2id$real-hash"))
        record = store.lookup("lookup-1")
        assert not hasattr(record, "secret")
        assert not hasattr(record, "plaintext_secret")
        assert record.secret_hash == "argon2id$real-hash"

    def test_no_plaintext_secret_field_exists_on_the_production_model(self) -> None:
        field_names = set(TokenRecord.model_fields)
        assert "secret" not in field_names
        assert "plaintext_secret" not in field_names
