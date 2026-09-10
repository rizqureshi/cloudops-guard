"""Behavioral tests for `PostgresTokenStore` against a real PostgreSQL
instance -- mirrors `tests/test_ingestion_authenticator.py`'s own
coverage of the `TokenStore` contract for the in-memory reference
implementation.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cloudops_guard.ingestion.models import TokenScope
from cloudops_guard.ingestion_azure.pool import IngestionDatabasePool
from cloudops_guard.ingestion_azure.postgres_token_store import PostgresTokenStore

pytestmark = pytest.mark.postgres


class _FakeSecretVerifier:
    """A deterministic stand-in, never real Argon2id -- mirrors
    `tests/ingestion_api_support.FakeSecretVerifier` exactly, so this
    test exercises `PostgresTokenStore`'s own storage/lookup mechanics in
    isolation from Argon2id's own (already exhaustively tested)
    correctness.
    """

    def __call__(self, presented_secret: str, secret_hash: str) -> bool:
        return f"hash:{presented_secret}" == secret_hash


@pytest.fixture()
def store(postgres_conninfo: str) -> PostgresTokenStore:
    pool = IngestionDatabasePool(postgres_conninfo)
    pool.open()
    yield PostgresTokenStore(pool, _FakeSecretVerifier())
    pool.close()


def test_lookup_unknown_returns_none(store: PostgresTokenStore) -> None:
    assert store.lookup("unknown") is None


def test_provision_then_lookup(store: PostgresTokenStore) -> None:
    store.provision_for_operator(
        lookup_id="lookup-1",
        secret_hash="hash:secret-1",
        tenant_id="tenant-a",
        scopes=frozenset({TokenScope.REPORTS_WRITE}),
        created_at=dt.datetime.now(dt.UTC),
    )
    record = store.lookup("lookup-1")
    assert record is not None
    assert record.tenant_id == "tenant-a"
    assert record.revoked is False
    assert TokenScope.REPORTS_WRITE in record.scopes


def test_verify_secret_pure_delegation_no_storage_access(store: PostgresTokenStore) -> None:
    assert store.verify_secret("secret-1", "hash:secret-1") is True
    assert store.verify_secret("wrong", "hash:secret-1") is False


def test_mark_revoked_takes_effect_on_next_lookup(store: PostgresTokenStore) -> None:
    store.provision_for_operator(
        lookup_id="lookup-1",
        secret_hash="hash:secret-1",
        tenant_id="tenant-a",
        scopes=frozenset({TokenScope.REPORTS_WRITE}),
        created_at=dt.datetime.now(dt.UTC),
    )
    store.mark_revoked("lookup-1")
    record = store.lookup("lookup-1")
    assert record.revoked is True


def test_mark_revoked_unknown_is_a_noop(store: PostgresTokenStore) -> None:
    store.mark_revoked("unknown")  # must not raise


def test_multi_scope_round_trip(store: PostgresTokenStore) -> None:
    scopes = frozenset(
        {TokenScope.REPORTS_WRITE, TokenScope.REPORTS_READ, TokenScope.REPORTS_DELETE}
    )
    store.provision_for_operator(
        lookup_id="lookup-2",
        secret_hash="hash:secret-2",
        tenant_id="tenant-b",
        scopes=scopes,
        created_at=dt.datetime.now(dt.UTC),
    )
    record = store.lookup("lookup-2")
    assert record.scopes == scopes
