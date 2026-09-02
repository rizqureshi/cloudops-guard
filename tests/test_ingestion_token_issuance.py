"""Tests for `token_issuance`: `generate_lookup_id`/`generate_secret` and
`provision_token`, the manual, out-of-band provisioning procedure's
generation step (`docs/manual-token-provisioning.md`).

`provision_token` always uses real Argon2id (no injectable hasher of any
kind -- see the module's own docstring for the security correction this
enforces) -- every call in this file pays real Argon2id cost, which is
why this file's runtime is a little higher than a pure-fake-based one
would be, in exchange for every test here proving something true of the
actual production path, never a stand-in.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import json
import pickle
import re

import pytest

from cloudops_guard.ingestion.argon2_backend import Argon2SecretVerifier
from cloudops_guard.ingestion.models import TokenRecord, TokenScope
from cloudops_guard.ingestion.token_format import (
    LOOKUP_ID_LENGTH,
    SECRET_LENGTH,
    TOKEN_DELIMITER,
    parse_token,
)
from cloudops_guard.ingestion.token_issuance import (
    LOOKUP_ID_BYTES,
    SECRET_BYTES,
    ProvisionedToken,
    generate_lookup_id,
    generate_secret,
    provision_token,
)

_URL_SAFE_BASE64_RE = re.compile(r"^[A-Za-z0-9_-]+$")
UTC = dt.UTC
T = dt.datetime(2026, 1, 1, tzinfo=UTC)


class TestGenerateLookupId:
    def test_length_matches_token_format_expectation(self) -> None:
        assert len(generate_lookup_id()) == LOOKUP_ID_LENGTH

    def test_byte_source_is_the_documented_constant(self) -> None:
        assert LOOKUP_ID_BYTES == 16

    def test_only_url_safe_base64_characters(self) -> None:
        assert _URL_SAFE_BASE64_RE.match(generate_lookup_id())

    def test_repeated_calls_produce_different_values(self) -> None:
        values = {generate_lookup_id() for _ in range(50)}
        assert len(values) == 50

    def test_output_round_trips_through_parse_token_length_checks(self) -> None:
        # A generated lookup_id must itself satisfy the exact length
        # token_format.parse_token enforces -- proves the two modules
        # agree on the fixed length, not just that they happen to.
        lookup_id = generate_lookup_id()
        secret = generate_secret()
        parsed = parse_token(f"{lookup_id}{TOKEN_DELIMITER}{secret}")
        assert parsed.lookup_id == lookup_id


class TestGenerateSecret:
    def test_length_matches_token_format_expectation(self) -> None:
        assert len(generate_secret()) == SECRET_LENGTH

    def test_byte_source_is_256_bits(self) -> None:
        assert SECRET_BYTES == 32  # 32 bytes * 8 bits/byte == 256 bits

    def test_only_url_safe_base64_characters(self) -> None:
        assert _URL_SAFE_BASE64_RE.match(generate_secret())

    def test_repeated_calls_produce_different_values(self) -> None:
        values = {generate_secret() for _ in range(50)}
        assert len(values) == 50

    def test_independent_from_lookup_id(self) -> None:
        # Generating many of each and cross-checking that no secret ever
        # equals a lookup_id generated alongside it (would be
        # astronomically unlikely by chance, but proves they are drawn
        # from independent calls, not derived from one another).
        for _ in range(20):
            lookup_id = generate_lookup_id()
            secret = generate_secret()
            assert lookup_id != secret


class TestPublicProvisioningHasNoInjectableHasher:
    """The security correction this pass makes: `provision_token`'s
    public signature accepts no `hasher` (or any other secret-hashing
    override) parameter of any kind -- confirmed both by signature
    inspection and by a live call proving a `hasher=` keyword is rejected
    outright, never silently accepted and ignored.
    """

    def test_hasher_parameter_does_not_exist_on_the_public_signature(self) -> None:
        import inspect

        signature = inspect.signature(provision_token)
        assert "hasher" not in signature.parameters

    def test_passing_hasher_keyword_raises_type_error(self) -> None:
        class PlaintextHasher:
            def hash(self, secret: str) -> str:
                return f"plaintext:{secret}"

        with pytest.raises(TypeError):
            provision_token(
                "tenant-a",
                [TokenScope.REPORTS_WRITE],
                hasher=PlaintextHasher(),  # type: ignore[call-arg]
            )

    def test_plaintext_returning_hasher_can_never_reach_stored_state(self) -> None:
        # There is no code path by which a caller-supplied hasher's
        # output can become TokenRecord.secret_hash at all -- proven
        # structurally (previous test) and behaviorally: every real
        # provision_token() call's stored hash is genuine Argon2id,
        # never a caller-influenced string.
        issued = provision_token("tenant-a", [TokenScope.REPORTS_WRITE])
        assert "plaintext:" not in issued.token_record.secret_hash
        assert issued.token_record.secret_hash.startswith("$argon2id$")


class TestProvisionToken:
    def _issue(self, **overrides: object) -> ProvisionedToken:
        fields = dict(
            tenant_id="tenant-a",
            scopes=[TokenScope.REPORTS_WRITE],
            clock=lambda: T,
        )
        fields.update(overrides)
        return provision_token(**fields)

    def test_returns_the_complete_token_for_one_time_delivery(self) -> None:
        issued = self._issue()
        lookup_id, delimiter, secret = issued.token.partition(TOKEN_DELIMITER)
        assert delimiter == TOKEN_DELIMITER
        assert len(lookup_id) == LOOKUP_ID_LENGTH
        assert len(secret) == SECRET_LENGTH

    def test_token_record_contains_only_the_approved_fields(self) -> None:
        field_names = set(TokenRecord.model_fields)
        assert field_names == {
            "lookup_id",
            "secret_hash",
            "tenant_id",
            "scopes",
            "revoked",
            "created_at",
        }

    def test_token_record_lookup_id_matches_the_token(self) -> None:
        issued = self._issue()
        lookup_id, _delim, _secret = issued.token.partition(TOKEN_DELIMITER)
        assert issued.token_record.lookup_id == lookup_id

    def test_every_provisioned_record_contains_a_genuine_argon2id_hash(self) -> None:
        issued = self._issue()
        assert issued.token_record.secret_hash.startswith("$argon2id$")

    def test_token_record_starts_unrevoked(self) -> None:
        issued = self._issue()
        assert issued.token_record.revoked is False

    def test_token_record_tenant_and_scopes_match_input(self) -> None:
        issued = self._issue(
            tenant_id="tenant-b", scopes=[TokenScope.REPORTS_READ, TokenScope.REPORTS_DELETE]
        )
        assert issued.token_record.tenant_id == "tenant-b"
        assert issued.token_record.scopes == frozenset(
            {TokenScope.REPORTS_READ, TokenScope.REPORTS_DELETE}
        )

    def test_created_at_uses_the_injected_clock(self) -> None:
        issued = self._issue()
        assert issued.token_record.created_at == T

    def test_repeated_issuance_produces_different_lookup_ids_and_secrets(self) -> None:
        tokens = [self._issue().token for _ in range(10)]
        lookup_ids = {t.partition(TOKEN_DELIMITER)[0] for t in tokens}
        secrets = {t.partition(TOKEN_DELIMITER)[2] for t in tokens}
        assert len(lookup_ids) == 10
        assert len(secrets) == 10

    def test_tenant_id_never_appears_in_the_token_text(self) -> None:
        issued = self._issue(tenant_id="unmistakable-tenant-marker")
        assert "unmistakable-tenant-marker" not in issued.token

    def test_scope_values_never_appear_in_the_token_text(self) -> None:
        issued = self._issue(scopes=[TokenScope.REPORTS_DELETE])
        assert TokenScope.REPORTS_DELETE.value not in issued.token

    def test_secret_never_appears_as_a_substring_of_the_stored_hash(self) -> None:
        issued = self._issue()
        _lookup_id, _delim, secret = issued.token.partition(TOKEN_DELIMITER)
        assert secret not in issued.token_record.secret_hash

    def test_issued_token_authenticates_against_its_own_record(self) -> None:
        issued = self._issue()
        verifier = Argon2SecretVerifier()
        _lookup_id, _delim, secret = issued.token.partition(TOKEN_DELIMITER)
        assert verifier(secret, issued.token_record.secret_hash) is True

    def test_wrong_secret_does_not_authenticate(self) -> None:
        issued = self._issue()
        verifier = Argon2SecretVerifier()
        assert verifier("definitely-the-wrong-secret", issued.token_record.secret_hash) is False


class TestProvisionedTokenRedaction:
    def _issue(self) -> ProvisionedToken:
        return provision_token("tenant-a", [TokenScope.REPORTS_WRITE], clock=lambda: T)

    def test_token_excluded_from_repr(self) -> None:
        issued = self._issue()
        _lookup_id, _delim, secret = issued.token.partition(TOKEN_DELIMITER)
        assert secret not in repr(issued)
        assert issued.token not in repr(issued)

    def test_token_excluded_from_str(self) -> None:
        issued = self._issue()
        assert issued.token not in str(issued)

    def test_token_still_accessible_via_attribute(self) -> None:
        issued = self._issue()
        assert isinstance(issued.token, str)
        assert len(issued.token) > 0

    def test_is_immutable(self) -> None:
        issued = self._issue()
        with pytest.raises(AttributeError):
            issued.token = "different"  # type: ignore[misc]

    def test_has_exactly_token_and_token_record_attributes(self) -> None:
        issued = self._issue()
        assert issued.token is not None
        assert issued.token_record is not None
        with pytest.raises(AttributeError):
            _ = issued.some_other_field  # type: ignore[attr-defined]


class TestProvisionedTokenResistsGenericSerialization:
    """Adversarial tests: every generic-serialization pathway that would
    otherwise expose the plaintext token must fail closed, not merely
    "not be used." Mirrors `test_ingestion_token_format.py`'s equivalent
    class for `ParsedToken`.
    """

    def _issue(self) -> ProvisionedToken:
        return provision_token("tenant-a", [TokenScope.REPORTS_WRITE], clock=lambda: T)

    def test_is_not_a_dataclass(self) -> None:
        issued = self._issue()
        assert dataclasses.is_dataclass(issued) is False

    def test_dataclasses_asdict_raises(self) -> None:
        issued = self._issue()
        with pytest.raises(TypeError):
            dataclasses.asdict(issued)  # type: ignore[arg-type]

    def test_vars_raises(self) -> None:
        issued = self._issue()
        with pytest.raises(TypeError):
            vars(issued)

    def test_has_no_instance_dict(self) -> None:
        issued = self._issue()
        assert not hasattr(issued, "__dict__")

    def test_json_dumps_raises(self) -> None:
        issued = self._issue()
        with pytest.raises(TypeError):
            json.dumps(issued)

    def test_pickle_dumps_raises(self) -> None:
        issued = self._issue()
        with pytest.raises(TypeError):
            pickle.dumps(issued)

    def test_deepcopy_raises_rather_than_silently_succeeding(self) -> None:
        issued = self._issue()
        with pytest.raises(TypeError):
            copy.deepcopy(issued)

    def test_repr_never_contains_the_plaintext_token(self) -> None:
        issued = self._issue()
        assert issued.token not in repr(issued)

    def test_str_never_contains_the_plaintext_token(self) -> None:
        issued = self._issue()
        assert issued.token not in str(issued)

    def test_not_iterable_like_a_tuple_or_namedtuple(self) -> None:
        # A NamedTuple-based design would allow `tuple(issued)` /
        # `list(issued)` to reproduce every field, including the secret,
        # via ordinary iteration -- confirm this object supports none of
        # that.
        issued = self._issue()
        with pytest.raises(TypeError):
            iter(issued)
