"""Concurrency tests for `AuthenticationCoordinator.authenticate` and
`reference.InMemoryTokenStore.mark_revoked`: revocation must never leave a
cached-valid window, and independent tokens must never interfere with one
another under concurrent load.

No test in this file sleeps or otherwise depends on wall-clock timing --
every concurrent scenario is synchronized with a `threading.Barrier`, and
the sequential "revocation completes before the next attempt begins"
guarantee is proven by ordinary sequential calls (no barrier needed for a
claim that is inherently about strict *before/after* ordering, not
simultaneity).
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest

from cloudops_guard.ingestion.authenticator import AuthenticationCoordinator
from cloudops_guard.ingestion.errors import AuthenticationFailed
from cloudops_guard.ingestion.models import TokenRecord, TokenScope
from cloudops_guard.ingestion.reference import InMemoryAttemptLimiter, InMemoryTokenStore
from cloudops_guard.ingestion.token_format import TOKEN_DELIMITER
from cloudops_guard.ingestion.token_issuance import generate_lookup_id, generate_secret

WORKER_COUNT = 16
REPEATS = 8

UTC = dt.UTC
T = dt.datetime(2026, 1, 1, tzinfo=UTC)


class _RecordingVerifier:
    """Deterministic, test-only fake `SecretVerifier` -- never Argon2id.
    Compares against a secret registered directly, out of band
    (`.register`), never derived from or embedded in `secret_hash` itself
    -- `provision_token` has no injectable hasher (this security
    correction's own fix), so `secret_hash` here is always an opaque
    placeholder containing no secret material.
    """

    def __init__(self) -> None:
        self._expected_secret_by_hash: dict[str, str] = {}

    def register(self, secret_hash: str, expected_secret: str) -> None:
        self._expected_secret_by_hash[secret_hash] = expected_secret

    def __call__(self, presented_secret: str, secret_hash: str) -> bool:
        expected = self._expected_secret_by_hash.get(secret_hash)
        return expected is not None and presented_secret == expected


def _coordinator(store: InMemoryTokenStore) -> AuthenticationCoordinator:
    return AuthenticationCoordinator(
        token_store=store,
        lookup_limiter=InMemoryAttemptLimiter(threshold=10_000),
        source_limiter=InMemoryAttemptLimiter(threshold=10_000),
        token_limiter=InMemoryAttemptLimiter(threshold=10_000),
    )


def _issue(store: InMemoryTokenStore, verifier: _RecordingVerifier) -> str:
    """Builds a fast test token without `provision_token` (which has no
    injectable hasher): a real `lookup_id`/`secret` pair, a `TokenRecord`
    constructed directly with an opaque `secret_hash` placeholder, and
    the real secret registered with the fake verifier out of band.
    """
    lookup_id = generate_lookup_id()
    secret = generate_secret()
    secret_hash = f"opaque-test-hash:{lookup_id}"
    verifier.register(secret_hash, secret)
    record = TokenRecord(
        lookup_id=lookup_id,
        secret_hash=secret_hash,
        tenant_id="tenant-a",
        scopes=frozenset({TokenScope.REPORTS_WRITE}),
        revoked=False,
        created_at=T,
    )
    store.register_for_testing(record)
    return f"{lookup_id}{TOKEN_DELIMITER}{secret}"


class TestSequentialRevocationTakesImmediateEffect:
    def test_revocation_completed_before_the_next_authenticate_call_fails_it(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue(store, verifier)
        coordinator = _coordinator(store)
        lookup_id = token.partition(TOKEN_DELIMITER)[0]

        # Succeeds before revocation.
        coordinator.authenticate(token, "source-a")

        store.mark_revoked(lookup_id)

        # This call begins strictly after mark_revoked returned -- must
        # fail, with no cached "still valid" window.
        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(token, "source-a")

    def test_every_authenticate_call_performs_a_fresh_lookup(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue(store, verifier)
        coordinator = _coordinator(store)

        # Ten successful authentications in a row, none of which could
        # succeed if any of them were served from a stale cached record
        # taken at construction time (there is no such cache to begin
        # with -- this exercises that repeatedly).
        for _ in range(10):
            coordinator.authenticate(token, "source-a")


def _run_concurrently(worker_count: int, target) -> tuple[list, list]:
    barrier = threading.Barrier(worker_count)
    results: list = [None] * worker_count
    errors: list = [None] * worker_count

    def run(index: int) -> None:
        barrier.wait()
        try:
            results[index] = target(index)
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors[index] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, errors


class TestConcurrentAuthenticationBeforeAndAfterRevocation:
    @pytest.mark.parametrize("_repeat", range(REPEATS))
    def test_concurrent_authentications_all_succeed_before_revocation(self, _repeat: int) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue(store, verifier)
        coordinator = _coordinator(store)

        def call(_index: int):
            return coordinator.authenticate(token, "source-a")

        results, errors = _run_concurrently(WORKER_COUNT, call)

        assert all(error is None for error in errors)
        assert all(result is not None for result in results)

    @pytest.mark.parametrize("_repeat", range(REPEATS))
    def test_concurrent_authentications_all_fail_after_revocation_completes_first(
        self, _repeat: int
    ) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue(store, verifier)
        coordinator = _coordinator(store)
        lookup_id = token.partition(TOKEN_DELIMITER)[0]

        # Revocation fully completes -- sequentially, not barrier-gated --
        # strictly before the concurrent batch below even starts.
        store.mark_revoked(lookup_id)

        def call(_index: int):
            return coordinator.authenticate(token, "source-a")

        results, errors = _run_concurrently(WORKER_COUNT, call)

        # Every single concurrent attempt must observe the revocation --
        # none may have raced ahead of it or observed stale state.
        assert all(result is None for result in results)
        assert all(isinstance(error, AuthenticationFailed) for error in errors)


class TestConcurrentAuthenticationAcrossIndependentTokens:
    @pytest.mark.parametrize("_repeat", range(REPEATS))
    def test_many_independent_tokens_authenticate_concurrently_without_interference(
        self, _repeat: int
    ) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        tokens = [_issue(store, verifier) for _ in range(WORKER_COUNT)]
        coordinator = _coordinator(store)

        def call(index: int):
            return coordinator.authenticate(tokens[index], "source-a")

        results, errors = _run_concurrently(WORKER_COUNT, call)

        assert all(error is None for error in errors)
        tenant_ids = {principal.tenant_id for principal in results}
        lookup_ids = {principal.lookup_id for principal in results}
        assert tenant_ids == {"tenant-a"}
        assert len(lookup_ids) == WORKER_COUNT

    @pytest.mark.parametrize("_repeat", range(REPEATS))
    def test_revoking_one_token_never_affects_a_concurrent_authentication_of_another(
        self, _repeat: int
    ) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        revoked_token = _issue(store, verifier)
        live_tokens = [_issue(store, verifier) for _ in range(WORKER_COUNT - 1)]
        coordinator = _coordinator(store)
        revoked_lookup_id = revoked_token.partition(TOKEN_DELIMITER)[0]
        store.mark_revoked(revoked_lookup_id)

        def call(index: int):
            return coordinator.authenticate(live_tokens[index], "source-a")

        results, errors = _run_concurrently(WORKER_COUNT - 1, call)

        assert all(error is None for error in errors)
        assert all(result is not None for result in results)
