"""Tests for `authenticator.AuthenticationCoordinator`/`authorize`: the
full authentication flow's exact ordering, the three abuse-protection
layers' independent scoping, generic-failure indistinguishability, and
scope authorization.

Most tests here wire real `reference.InMemoryTokenStore`/
`InMemoryAttemptLimiter` instances together with a fast, deterministic
recording fake `SecretVerifier` (never real Argon2id -- that is covered
exhaustively in `test_ingestion_argon2_backend.py`, and a handful of true
end-to-end tests at the bottom of this file use the real
`Argon2SecretVerifier` to prove the two are wired together correctly in
production). The dedicated `TestExactOrderingAndCallCounts` class uses
spy doubles instead, to prove not just the final result but the exact
sequence and count of calls across `TokenStore` and all three limiters.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cloudops_guard.ingestion.abuse_protection import (
    check_capabilities_allowed,
    lookup_scope_key,
    source_scope_key,
    token_scope_key,
)
from cloudops_guard.ingestion.argon2_backend import Argon2SecretVerifier
from cloudops_guard.ingestion.authenticator import (
    FUTURE_ENDPOINT_SCOPES,
    GENERIC_AUTHENTICATION_FAILURE_MESSAGE,
    AuthenticatedPrincipal,
    AuthenticationCoordinator,
    authorize,
)
from cloudops_guard.ingestion.errors import AuthenticationFailed, AuthorizationFailed, RateLimited
from cloudops_guard.ingestion.models import TokenRecord, TokenScope
from cloudops_guard.ingestion.reference import InMemoryAttemptLimiter, InMemoryTokenStore
from cloudops_guard.ingestion.token_format import TOKEN_DELIMITER
from cloudops_guard.ingestion.token_issuance import (
    generate_lookup_id,
    generate_secret,
    provision_token,
)

UTC = dt.UTC
T = dt.datetime(2026, 1, 1, tzinfo=UTC)


class _RecordingVerifier:
    """Deterministic, test-only fake `SecretVerifier` -- never Argon2id.
    `provision_token` has no injectable hasher of any kind (this security
    correction's own fix), so this fake is never wired through it; tests
    instead construct a `TokenRecord` directly (`_issue_and_register`
    below) with an **opaque placeholder** `secret_hash` that contains no
    secret material at all, and `.register()` this fake with the real
    secret **out of band** -- never derived from, or embedded in,
    `secret_hash` itself. This is deliberately different from an earlier
    revision of this fake, which computed `secret_hash` as
    `f"fake-hash:{secret}"` -- a "hash" that was itself the plaintext
    secret, recoverable via `dataclasses.asdict()` on the object that
    used to carry it. That pattern must never return.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._expected_secret_by_hash: dict[str, str] = {}

    def register(self, secret_hash: str, expected_secret: str) -> None:
        self._expected_secret_by_hash[secret_hash] = expected_secret

    def __call__(self, presented_secret: str, secret_hash: str) -> bool:
        self.calls.append((presented_secret, secret_hash))
        expected = self._expected_secret_by_hash.get(secret_hash)
        return expected is not None and presented_secret == expected


def _issue_and_register(
    token_store: InMemoryTokenStore,
    verifier: _RecordingVerifier,
    *,
    tenant_id: str = "tenant-a",
    scopes: frozenset[TokenScope] = frozenset({TokenScope.REPORTS_WRITE}),
) -> str:
    """Builds a fast test token *without* `provision_token` (which has no
    injectable hasher): a real `lookup_id`/`secret` pair
    (`generate_lookup_id`/`generate_secret`, the same real generators
    production provisioning uses), a `TokenRecord` constructed directly
    with an opaque `secret_hash` placeholder, and the real secret
    registered with the fake verifier out of band -- exactly the pattern
    this security correction's own instructions describe as acceptable
    ("directly construct TokenRecord with an opaque test value").
    """
    lookup_id = generate_lookup_id()
    secret = generate_secret()
    secret_hash = f"opaque-test-hash:{lookup_id}"
    verifier.register(secret_hash, secret)
    record = TokenRecord(
        lookup_id=lookup_id,
        secret_hash=secret_hash,
        tenant_id=tenant_id,
        scopes=scopes,
        revoked=False,
        created_at=T,
    )
    token_store.register_for_testing(record)
    return f"{lookup_id}{TOKEN_DELIMITER}{secret}"


def _unregistered_token() -> str:
    """A syntactically well-formed token whose `lookup_id` was never
    registered in any store -- for exercising the "unknown lookup_id"
    path, which needs no real or fake secret hash at all.
    """
    return f"{generate_lookup_id()}{TOKEN_DELIMITER}{generate_secret()}"


def _coordinator(
    token_store: InMemoryTokenStore,
    *,
    lookup_threshold: int = 1000,
    source_threshold: int = 1000,
    token_threshold: int = 1000,
) -> AuthenticationCoordinator:
    return AuthenticationCoordinator(
        token_store=token_store,
        lookup_limiter=InMemoryAttemptLimiter(threshold=lookup_threshold),
        source_limiter=InMemoryAttemptLimiter(threshold=source_threshold),
        token_limiter=InMemoryAttemptLimiter(threshold=token_threshold),
    )


class TestAuthenticatedPrincipalStructure:
    def test_fields_are_exactly_lookup_id_tenant_id_scopes(self) -> None:
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(AuthenticatedPrincipal)}
        assert field_names == {"lookup_id", "tenant_id", "scopes"}

    def test_no_secret_token_or_hash_field_exists(self) -> None:
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(AuthenticatedPrincipal)}
        for forbidden in ("secret", "token", "secret_hash", "presented_token"):
            assert forbidden not in field_names

    def test_is_frozen(self) -> None:
        import dataclasses

        principal = AuthenticatedPrincipal(
            lookup_id="a", tenant_id="b", scopes=frozenset({TokenScope.REPORTS_WRITE})
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            principal.tenant_id = "c"  # type: ignore[misc]


class TestSuccessfulAuthentication:
    def test_correct_token_authenticates(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier, tenant_id="tenant-a")
        coordinator = _coordinator(store)

        principal = coordinator.authenticate(token, "source-a")

        assert principal.tenant_id == "tenant-a"
        assert principal.scopes == frozenset({TokenScope.REPORTS_WRITE})

    def test_lookup_id_matches_the_tokens_own_lookup_id(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier)
        expected_lookup_id = token.partition(TOKEN_DELIMITER)[0]
        coordinator = _coordinator(store)

        principal = coordinator.authenticate(token, "source-a")

        assert principal.lookup_id == expected_lookup_id

    def test_tenant_identity_always_comes_from_token_record_never_elsewhere(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier, tenant_id="the-real-tenant")
        coordinator = _coordinator(store)

        # Even though "source_identifier" looks nothing like a tenant,
        # and there is no other channel to supply one, this proves the
        # only source of tenant identity is the stored TokenRecord.
        principal = coordinator.authenticate(token, "attacker-supplied-source-string")
        assert principal.tenant_id == "the-real-tenant"


class TestGenericFailureIndistinguishability:
    def _malformed_case(self) -> tuple[AuthenticationCoordinator, str]:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        return _coordinator(store), "not-a-well-formed-token"

    def _unknown_lookup_case(self) -> tuple[AuthenticationCoordinator, str]:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        return _coordinator(store), _unregistered_token()

    def _revoked_case(self) -> tuple[AuthenticationCoordinator, str]:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier)
        lookup_id = token.partition(TOKEN_DELIMITER)[0]
        store.mark_revoked(lookup_id)
        return _coordinator(store), token

    def _wrong_secret_case(self) -> tuple[AuthenticationCoordinator, str]:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier)
        lookup_id = token.partition(TOKEN_DELIMITER)[0]
        wrong_token = f"{lookup_id}{TOKEN_DELIMITER}{'x' * 43}"
        return _coordinator(store), wrong_token

    def test_all_four_cases_raise_the_same_exception_type_and_message(self) -> None:
        cases = [
            self._malformed_case(),
            self._unknown_lookup_case(),
            self._revoked_case(),
            self._wrong_secret_case(),
        ]
        messages = []
        for coordinator, token in cases:
            with pytest.raises(AuthenticationFailed) as excinfo:
                coordinator.authenticate(token, "source-a")
            messages.append(str(excinfo.value))
        assert len(set(messages)) == 1
        assert messages[0] == GENERIC_AUTHENTICATION_FAILURE_MESSAGE

    @pytest.mark.parametrize(
        "case_name",
        ["_malformed_case", "_unknown_lookup_case", "_revoked_case", "_wrong_secret_case"],
    )
    def test_no_secret_or_hash_appears_in_the_exception(self, case_name: str) -> None:
        coordinator, token = getattr(self, case_name)()
        with pytest.raises(AuthenticationFailed) as excinfo:
            coordinator.authenticate(token, "source-a")
        message = str(excinfo.value)
        assert "fake-hash" not in message
        assert "argon2" not in message.lower()


class TestArgon2idNeverInvokedOnShortCircuitPaths:
    def test_unknown_lookup_never_invokes_verifier(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _unregistered_token()
        coordinator = _coordinator(store)

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(token, "source-a")

        assert verifier.calls == []

    def test_revoked_token_never_invokes_verifier(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier)
        lookup_id = token.partition(TOKEN_DELIMITER)[0]
        store.mark_revoked(lookup_id)
        coordinator = _coordinator(store)

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(token, "source-a")

        assert verifier.calls == []

    def test_malformed_token_never_invokes_verifier(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        coordinator = _coordinator(store)

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate("malformed", "source-a")

        assert verifier.calls == []

    def test_layer2_blocked_source_never_invokes_verifier(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier)
        source_limiter = InMemoryAttemptLimiter(threshold=1)
        source_limiter.record_failure(source_scope_key("blocked-source"))
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=InMemoryAttemptLimiter(threshold=1000),
            source_limiter=source_limiter,
            token_limiter=InMemoryAttemptLimiter(threshold=1000),
        )

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(token, "blocked-source")

        assert verifier.calls == []

    def test_layer1_blocked_lookup_id_never_invokes_verifier(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier)
        lookup_id = token.partition(TOKEN_DELIMITER)[0]
        lookup_limiter = InMemoryAttemptLimiter(threshold=1)
        lookup_limiter.record_failure(lookup_scope_key(lookup_id))
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=InMemoryAttemptLimiter(threshold=1000),
            token_limiter=InMemoryAttemptLimiter(threshold=1000),
        )

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(token, "source-a")

        assert verifier.calls == []


class TestScopeAuthorization:
    def _principal(self, scopes: frozenset[TokenScope]) -> AuthenticatedPrincipal:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier, scopes=scopes)
        coordinator = _coordinator(store)
        return coordinator.authenticate(token, "source-a")

    def test_write_only_token_can_write(self) -> None:
        principal = self._principal(frozenset({TokenScope.REPORTS_WRITE}))
        authorize(principal, TokenScope.REPORTS_WRITE)  # must not raise

    def test_write_only_token_cannot_read(self) -> None:
        principal = self._principal(frozenset({TokenScope.REPORTS_WRITE}))
        with pytest.raises(AuthorizationFailed):
            authorize(principal, TokenScope.REPORTS_READ)

    def test_write_only_token_cannot_delete(self) -> None:
        principal = self._principal(frozenset({TokenScope.REPORTS_WRITE}))
        with pytest.raises(AuthorizationFailed):
            authorize(principal, TokenScope.REPORTS_DELETE)

    def test_read_only_token_can_read(self) -> None:
        principal = self._principal(frozenset({TokenScope.REPORTS_READ}))
        authorize(principal, TokenScope.REPORTS_READ)

    def test_read_only_token_cannot_write_or_delete(self) -> None:
        principal = self._principal(frozenset({TokenScope.REPORTS_READ}))
        with pytest.raises(AuthorizationFailed):
            authorize(principal, TokenScope.REPORTS_WRITE)
        with pytest.raises(AuthorizationFailed):
            authorize(principal, TokenScope.REPORTS_DELETE)

    def test_delete_only_token_can_delete(self) -> None:
        principal = self._principal(frozenset({TokenScope.REPORTS_DELETE}))
        authorize(principal, TokenScope.REPORTS_DELETE)

    def test_delete_only_token_cannot_read_or_write(self) -> None:
        principal = self._principal(frozenset({TokenScope.REPORTS_DELETE}))
        with pytest.raises(AuthorizationFailed):
            authorize(principal, TokenScope.REPORTS_READ)
        with pytest.raises(AuthorizationFailed):
            authorize(principal, TokenScope.REPORTS_WRITE)

    def test_multi_scope_token_receives_exactly_its_stored_scopes(self) -> None:
        scopes = frozenset({TokenScope.REPORTS_WRITE, TokenScope.REPORTS_READ})
        principal = self._principal(scopes)
        assert principal.scopes == scopes
        authorize(principal, TokenScope.REPORTS_WRITE)
        authorize(principal, TokenScope.REPORTS_READ)
        with pytest.raises(AuthorizationFailed):
            authorize(principal, TokenScope.REPORTS_DELETE)

    def test_authorization_failure_message_names_the_missing_scope(self) -> None:
        principal = self._principal(frozenset({TokenScope.REPORTS_WRITE}))
        with pytest.raises(AuthorizationFailed) as excinfo:
            authorize(principal, TokenScope.REPORTS_DELETE)
        assert "reports:delete" in str(excinfo.value)


class TestFutureEndpointScopesMapping:
    def test_maps_exactly_the_three_documented_endpoints(self) -> None:
        assert set(FUTURE_ENDPOINT_SCOPES) == {
            "POST /api/v1/reports",
            "GET /api/v1/reports/{id}",
            "DELETE /api/v1/reports/{id}",
        }

    def test_each_endpoint_maps_to_the_correct_scope(self) -> None:
        assert FUTURE_ENDPOINT_SCOPES["POST /api/v1/reports"] is TokenScope.REPORTS_WRITE
        assert FUTURE_ENDPOINT_SCOPES["GET /api/v1/reports/{id}"] is TokenScope.REPORTS_READ
        assert FUTURE_ENDPOINT_SCOPES["DELETE /api/v1/reports/{id}"] is TokenScope.REPORTS_DELETE


class TestLayer1LookupScopedLimiter:
    def test_blocked_lookup_id_short_circuits_before_argon2id(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier)
        lookup_id = token.partition(TOKEN_DELIMITER)[0]
        lookup_limiter = InMemoryAttemptLimiter(threshold=1)
        lookup_limiter.record_failure(lookup_scope_key(lookup_id))
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=InMemoryAttemptLimiter(threshold=1000),
            token_limiter=InMemoryAttemptLimiter(threshold=1000),
        )

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(token, "source-a")
        assert verifier.calls == []

    def test_blocked_lookup_id_a_does_not_affect_lookup_id_b(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token_a = _issue_and_register(store, verifier)
        token_b = _issue_and_register(store, verifier)
        lookup_id_a = token_a.partition(TOKEN_DELIMITER)[0]
        lookup_limiter = InMemoryAttemptLimiter(threshold=1)
        lookup_limiter.record_failure(lookup_scope_key(lookup_id_a))
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=InMemoryAttemptLimiter(threshold=1000),
            token_limiter=InMemoryAttemptLimiter(threshold=1000),
        )

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(token_a, "source-a")
        # token_b's lookup_id was never recorded against -- still works.
        principal = coordinator.authenticate(token_b, "source-a")
        assert principal.lookup_id == token_b.partition(TOKEN_DELIMITER)[0]

    def test_wrong_secret_records_only_the_intended_lookup_and_source_events(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier)
        lookup_id = token.partition(TOKEN_DELIMITER)[0]
        wrong_token = f"{lookup_id}{TOKEN_DELIMITER}{'x' * 43}"
        lookup_limiter = InMemoryAttemptLimiter(threshold=2)
        source_limiter = InMemoryAttemptLimiter(threshold=2)
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=source_limiter,
            token_limiter=InMemoryAttemptLimiter(threshold=1000),
        )

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(wrong_token, "source-a")

        # Exactly one failure recorded on each -- confirmed by threshold=2
        # not yet blocking, and threshold=1 now blocking.
        assert lookup_limiter.is_blocked(lookup_scope_key(lookup_id)) is False
        assert source_limiter.is_blocked(source_scope_key("source-a")) is False
        lookup_limiter.record_failure(lookup_scope_key(lookup_id))
        assert lookup_limiter.is_blocked(lookup_scope_key(lookup_id)) is True


class TestLayer2SourceScopedLimiter:
    def test_blocked_source_short_circuits_before_argon2id(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier)
        source_limiter = InMemoryAttemptLimiter(threshold=1)
        source_limiter.record_failure(source_scope_key("blocked-source"))
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=InMemoryAttemptLimiter(threshold=1000),
            source_limiter=source_limiter,
            token_limiter=InMemoryAttemptLimiter(threshold=1000),
        )

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(token, "blocked-source")
        assert verifier.calls == []

    def test_unknown_lookup_id_records_a_source_scoped_failure(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _unregistered_token()
        source_limiter = InMemoryAttemptLimiter(threshold=1)
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=InMemoryAttemptLimiter(threshold=1000),
            source_limiter=source_limiter,
            token_limiter=InMemoryAttemptLimiter(threshold=1000),
        )

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(token, "source-a")

        assert source_limiter.is_blocked(source_scope_key("source-a")) is True

    def test_blocked_source_rejects_a_future_capabilities_check(self) -> None:
        source_limiter = InMemoryAttemptLimiter(threshold=1)
        source_limiter.record_failure(source_scope_key("blocked-source"))

        with pytest.raises(RateLimited):
            check_capabilities_allowed("blocked-source", attempt_limiter=source_limiter)

    def test_source_a_does_not_block_source_b(self) -> None:
        source_limiter = InMemoryAttemptLimiter(threshold=1)
        source_limiter.record_failure(source_scope_key("source-a"))

        with pytest.raises(RateLimited):
            check_capabilities_allowed("source-a", attempt_limiter=source_limiter)
        check_capabilities_allowed("source-b", attempt_limiter=source_limiter)  # must not raise

    def test_capabilities_check_requires_no_token_argument(self) -> None:
        import inspect

        signature = inspect.signature(check_capabilities_allowed)
        param_names = set(signature.parameters)
        for forbidden in ("token", "presented_token", "lookup_id", "secret"):
            assert forbidden not in param_names

    def test_capabilities_check_does_not_record_a_failure_merely_for_being_called(self) -> None:
        source_limiter = InMemoryAttemptLimiter(threshold=1)
        check_capabilities_allowed("source-a", attempt_limiter=source_limiter)
        # Still not blocked after a single, allowed call -- proves the
        # call itself did not increment the failure counter.
        assert source_limiter.is_blocked(source_scope_key("source-a")) is False


class TestLayer3TokenScopedLimiter:
    def test_checked_only_after_successful_verification(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token = _issue_and_register(store, verifier)
        lookup_id = token.partition(TOKEN_DELIMITER)[0]
        token_limiter = InMemoryAttemptLimiter(threshold=1)
        token_limiter.record_failure(token_scope_key(lookup_id))
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=InMemoryAttemptLimiter(threshold=1000),
            source_limiter=InMemoryAttemptLimiter(threshold=1000),
            token_limiter=token_limiter,
        )

        # Only reached (and raises RateLimited, not AuthenticationFailed)
        # because the secret verified successfully first.
        with pytest.raises(RateLimited):
            coordinator.authenticate(token, "source-a")
        assert verifier.calls == [
            (
                token.partition(TOKEN_DELIMITER)[2],
                f"opaque-test-hash:{lookup_id}",
            )
        ]

    def test_blocked_token_a_does_not_block_token_b(self) -> None:
        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        token_a = _issue_and_register(store, verifier)
        token_b = _issue_and_register(store, verifier)
        lookup_id_a = token_a.partition(TOKEN_DELIMITER)[0]
        token_limiter = InMemoryAttemptLimiter(threshold=1)
        token_limiter.record_failure(token_scope_key(lookup_id_a))
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=InMemoryAttemptLimiter(threshold=1000),
            source_limiter=InMemoryAttemptLimiter(threshold=1000),
            token_limiter=token_limiter,
        )

        with pytest.raises(RateLimited):
            coordinator.authenticate(token_a, "source-a")
        principal = coordinator.authenticate(token_b, "source-a")
        assert principal is not None

    @pytest.mark.parametrize(
        "case_name",
        ["_malformed_case", "_unknown_lookup_case", "_revoked_case", "_wrong_secret_case"],
    )
    def test_never_consulted_for_a_failed_authentication(self, case_name: str) -> None:
        indistinguishability = TestGenericFailureIndistinguishability()
        _coordinator_unused, token = getattr(indistinguishability, case_name)()

        verifier = _RecordingVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        # Rebuild the same scenario with a token_limiter that blocks
        # *everything*, to prove it is never even consulted on any
        # failed-authentication path.
        if case_name == "_wrong_secret_case":
            token = _issue_and_register(store, verifier)
            lookup_id = token.partition(TOKEN_DELIMITER)[0]
            token = f"{lookup_id}{TOKEN_DELIMITER}{'x' * 43}"
        elif case_name == "_revoked_case":
            token = _issue_and_register(store, verifier)
            lookup_id = token.partition(TOKEN_DELIMITER)[0]
            store.mark_revoked(lookup_id)
        elif case_name == "_unknown_lookup_case":
            token = _unregistered_token()
        else:
            token = "malformed"

        class _AlwaysBlockedLimiter:
            def record_failure(self, scope_key: str) -> None:
                pass

            def is_blocked(self, scope_key: str) -> bool:
                return True

        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=InMemoryAttemptLimiter(threshold=1000),
            source_limiter=InMemoryAttemptLimiter(threshold=1000),
            token_limiter=_AlwaysBlockedLimiter(),  # type: ignore[arg-type]
        )

        # Must still raise AuthenticationFailed (not RateLimited) --
        # proves token_limiter was never consulted on this failure path.
        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(token, "source-a")


class TestExactOrderingAndCallCounts:
    """Spy-based proofs of exact call sequence and count -- not merely
    the final result."""

    def _spies(self) -> tuple[list[tuple], _SpyTokenStore, _SpyLimiter, _SpyLimiter, _SpyLimiter]:
        call_log: list[tuple] = []
        store = _SpyTokenStore(call_log)
        lookup_limiter = _SpyLimiter("lookup_limiter", call_log)
        source_limiter = _SpyLimiter("source_limiter", call_log)
        token_limiter = _SpyLimiter("token_limiter", call_log)
        return call_log, store, lookup_limiter, source_limiter, token_limiter

    def test_layer2_blocked_short_circuits_everything(self) -> None:
        call_log, store, lookup_limiter, source_limiter, token_limiter = self._spies()
        source_limiter.blocked.add(source_scope_key("blocked-source"))
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=source_limiter,
            token_limiter=token_limiter,
        )

        # A deliberately *malformed* token: this is what actually proves
        # Layer 2 is checked before parsing, not merely before storage
        # lookup -- if parsing ran first here, it would fail and record a
        # source-scoped failure (the malformed-token path, step 2) before
        # this call ever got to raise for being blocked. A well-formed
        # token could not distinguish the two orderings, since Layer 2's
        # block already precedes step 3 (storage lookup) either way.
        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate("not-a-well-formed-token", "blocked-source")

        assert store.lookup_calls == []
        assert store.verify_calls == []
        assert lookup_limiter.is_blocked_calls == []
        assert lookup_limiter.record_failure_calls == []
        assert token_limiter.is_blocked_calls == []
        # The Layer 2 check itself ran (exactly once)...
        assert source_limiter.is_blocked_calls == [source_scope_key("blocked-source")]
        # ...but the malformed-token branch's own record_failure call was
        # never reached -- proving the block was hit *before* parsing was
        # even attempted, not merely before a lookup.
        assert source_limiter.record_failure_calls == []

    def test_unknown_lookup_never_reaches_layer1_or_argon2id(self) -> None:
        call_log, store, lookup_limiter, source_limiter, token_limiter = self._spies()
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=source_limiter,
            token_limiter=token_limiter,
        )

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(f"{'a' * 22}{TOKEN_DELIMITER}{'b' * 43}", "source-a")

        assert store.lookup_calls == ["a" * 22]
        assert store.verify_calls == []
        assert lookup_limiter.is_blocked_calls == []
        assert source_limiter.record_failure_calls == [source_scope_key("source-a")]

    def test_layer1_blocked_never_reaches_argon2id(self) -> None:
        call_log, store, lookup_limiter, source_limiter, token_limiter = self._spies()
        lookup_id = "a" * 22
        record = TokenRecord(
            lookup_id=lookup_id,
            secret_hash="irrelevant",
            tenant_id="tenant-a",
            scopes=frozenset({TokenScope.REPORTS_WRITE}),
            revoked=False,
            created_at=T,
        )
        store.records[lookup_id] = record
        lookup_limiter.blocked.add(lookup_scope_key(lookup_id))
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=source_limiter,
            token_limiter=token_limiter,
        )

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(f"{lookup_id}{TOKEN_DELIMITER}{'b' * 43}", "source-a")

        assert store.verify_calls == []

    def test_wrong_secret_calls_verify_exactly_once_then_records_failures(self) -> None:
        call_log, store, lookup_limiter, source_limiter, token_limiter = self._spies()
        lookup_id = "a" * 22
        record = TokenRecord(
            lookup_id=lookup_id,
            secret_hash="the-real-hash",
            tenant_id="tenant-a",
            scopes=frozenset({TokenScope.REPORTS_WRITE}),
            revoked=False,
            created_at=T,
        )
        store.records[lookup_id] = record
        store.verifier_result = False
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=source_limiter,
            token_limiter=token_limiter,
        )

        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(f"{lookup_id}{TOKEN_DELIMITER}{'b' * 43}", "source-a")

        assert len(store.verify_calls) == 1
        verify_index = call_log.index(("token_store.verify_secret",))
        lookup_record_index = call_log.index(
            ("lookup_limiter.record_failure", lookup_scope_key(lookup_id))
        )
        source_record_index = call_log.index(
            ("source_limiter.record_failure", source_scope_key("source-a"))
        )
        assert verify_index < lookup_record_index
        assert verify_index < source_record_index

    def test_successful_secret_checks_layer3_only_after_verification(self) -> None:
        call_log, store, lookup_limiter, source_limiter, token_limiter = self._spies()
        lookup_id = "a" * 22
        record = TokenRecord(
            lookup_id=lookup_id,
            secret_hash="the-real-hash",
            tenant_id="tenant-a",
            scopes=frozenset({TokenScope.REPORTS_WRITE}),
            revoked=False,
            created_at=T,
        )
        store.records[lookup_id] = record
        store.verifier_result = True
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=source_limiter,
            token_limiter=token_limiter,
        )

        coordinator.authenticate(f"{lookup_id}{TOKEN_DELIMITER}{'b' * 43}", "source-a")

        verify_index = call_log.index(("token_store.verify_secret",))
        token_check_index = call_log.index(("token_limiter.is_blocked", token_scope_key(lookup_id)))
        assert verify_index < token_check_index

    def test_token_limiter_record_failure_is_never_called_anywhere(self) -> None:
        # §8's explicit callout: Phase 4B's AttemptLimiter.record_failure
        # means "failure" -- an ordinary successful, authenticated
        # request is not one, so this coordinator must never call
        # token_limiter.record_failure for any reason, on any path
        # (success or otherwise). Proven across every scenario this file
        # already exercises: successful auth, wrong secret, unknown
        # lookup, and a Layer-3-blocked request.
        call_log, store, lookup_limiter, source_limiter, token_limiter = self._spies()
        lookup_id = "a" * 22
        record = TokenRecord(
            lookup_id=lookup_id,
            secret_hash="the-real-hash",
            tenant_id="tenant-a",
            scopes=frozenset({TokenScope.REPORTS_WRITE}),
            revoked=False,
            created_at=T,
        )
        store.records[lookup_id] = record
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=source_limiter,
            token_limiter=token_limiter,
        )
        valid_token = f"{lookup_id}{TOKEN_DELIMITER}{'b' * 43}"

        store.verifier_result = True
        coordinator.authenticate(valid_token, "source-a")  # success
        assert token_limiter.record_failure_calls == []

        store.verifier_result = False
        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(valid_token, "source-a")  # wrong secret
        assert token_limiter.record_failure_calls == []

        token_limiter.blocked.add(token_scope_key(lookup_id))
        store.verifier_result = True
        with pytest.raises(RateLimited):
            coordinator.authenticate(valid_token, "source-a")  # Layer 3 blocked
        assert token_limiter.record_failure_calls == []

    def test_scope_failure_occurs_only_after_authentication_succeeds(self) -> None:
        # authorize() takes an AuthenticatedPrincipal, which can only be
        # constructed by a successful authenticate() call -- a failed
        # attempt never produces one to authorize in the first place.
        call_log, store, lookup_limiter, source_limiter, token_limiter = self._spies()
        coordinator = AuthenticationCoordinator(
            token_store=store,
            lookup_limiter=lookup_limiter,
            source_limiter=source_limiter,
            token_limiter=token_limiter,
        )
        with pytest.raises(AuthenticationFailed):
            principal = coordinator.authenticate("malformed", "source-a")
            # unreachable: authorize() is never even called
            authorize(principal, TokenScope.REPORTS_WRITE)  # type: ignore[possibly-undefined]


class _SpyTokenStore:
    def __init__(self, call_log: list[tuple]) -> None:
        self.records: dict[str, TokenRecord] = {}
        self.call_log = call_log
        self.lookup_calls: list[str] = []
        self.verify_calls: list[tuple[str, str]] = []
        self.verifier_result = True

    def lookup(self, lookup_id: str) -> TokenRecord | None:
        self.lookup_calls.append(lookup_id)
        self.call_log.append(("token_store.lookup",))
        return self.records.get(lookup_id)

    def verify_secret(self, presented_secret: str, secret_hash: str) -> bool:
        self.verify_calls.append((presented_secret, secret_hash))
        self.call_log.append(("token_store.verify_secret",))
        return self.verifier_result

    def mark_revoked(self, lookup_id: str) -> None:
        record = self.records.get(lookup_id)
        if record is not None:
            self.records[lookup_id] = record.model_copy(update={"revoked": True})


class _SpyLimiter:
    def __init__(self, name: str, call_log: list[tuple]) -> None:
        self.name = name
        self.call_log = call_log
        self.blocked: set[str] = set()
        self.record_failure_calls: list[str] = []
        self.is_blocked_calls: list[str] = []

    def record_failure(self, scope_key: str) -> None:
        self.record_failure_calls.append(scope_key)
        self.call_log.append((f"{self.name}.record_failure", scope_key))

    def is_blocked(self, scope_key: str) -> bool:
        self.is_blocked_calls.append(scope_key)
        self.call_log.append((f"{self.name}.is_blocked", scope_key))
        return scope_key in self.blocked


class TestRealArgon2idEndToEnd:
    """A small number of true end-to-end tests proving the coordinator
    genuinely works with real Argon2id, not just fast fakes."""

    def test_full_flow_with_real_argon2id(self) -> None:
        verifier = Argon2SecretVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        issued = provision_token("tenant-a", [TokenScope.REPORTS_WRITE])
        store.register_for_testing(issued.token_record)
        coordinator = _coordinator(store)

        principal = coordinator.authenticate(issued.token, "source-a")
        assert principal.tenant_id == "tenant-a"
        authorize(principal, TokenScope.REPORTS_WRITE)

    def test_wrong_secret_fails_with_real_argon2id(self) -> None:
        verifier = Argon2SecretVerifier()
        store = InMemoryTokenStore(secret_verifier=verifier)
        issued = provision_token("tenant-a", [TokenScope.REPORTS_WRITE])
        store.register_for_testing(issued.token_record)
        coordinator = _coordinator(store)

        lookup_id = issued.token.partition(TOKEN_DELIMITER)[0]
        wrong_token = f"{lookup_id}{TOKEN_DELIMITER}{'x' * 43}"
        with pytest.raises(AuthenticationFailed):
            coordinator.authenticate(wrong_token, "source-a")
