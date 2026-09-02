"""Tests for `argon2_backend.Argon2SecretVerifier`: real Argon2id hashing
and verification via `argon2-cffi`, delegated through the library's own
high-level API only -- no manual hashing, salting, or comparison anywhere
in this module or its production counterpart.
"""

from __future__ import annotations

import inspect

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type

from cloudops_guard.ingestion.argon2_backend import Argon2SecretVerifier, require_argon2id_hash
from cloudops_guard.ingestion.errors import InvalidArgon2idHashError
from cloudops_guard.ingestion.interfaces import SecretVerifier


class TestEncodedHashIdentity:
    def test_hash_identifies_argon2id(self) -> None:
        verifier = Argon2SecretVerifier()
        encoded = verifier.hash("a-secret-value")
        assert encoded.startswith("$argon2id$")

    def test_hash_result_is_a_string(self) -> None:
        verifier = Argon2SecretVerifier()
        assert isinstance(verifier.hash("a-secret-value"), str)


class TestFreshSaltPerHash:
    def test_same_secret_hashed_twice_produces_different_encoded_hashes(self) -> None:
        verifier = Argon2SecretVerifier()
        first = verifier.hash("same-secret")
        second = verifier.hash("same-secret")
        assert first != second

    def test_both_independently_salted_hashes_verify_correctly(self) -> None:
        verifier = Argon2SecretVerifier()
        first = verifier.hash("same-secret")
        second = verifier.hash("same-secret")
        assert verifier("same-secret", first) is True
        assert verifier("same-secret", second) is True


class TestVerification:
    def test_correct_secret_succeeds(self) -> None:
        verifier = Argon2SecretVerifier()
        encoded = verifier.hash("correct-secret")
        assert verifier("correct-secret", encoded) is True

    def test_wrong_secret_fails(self) -> None:
        verifier = Argon2SecretVerifier()
        encoded = verifier.hash("correct-secret")
        assert verifier("wrong-secret", encoded) is False

    def test_empty_presented_secret_fails_against_a_real_hash(self) -> None:
        verifier = Argon2SecretVerifier()
        encoded = verifier.hash("correct-secret")
        assert verifier("", encoded) is False

    def test_case_sensitive(self) -> None:
        verifier = Argon2SecretVerifier()
        encoded = verifier.hash("Correct-Secret")
        assert verifier("correct-secret", encoded) is False


class TestMalformedStoredHashFailsSafely:
    def test_completely_invalid_hash_string_returns_false_not_raise(self) -> None:
        verifier = Argon2SecretVerifier()
        assert verifier("any-secret", "not-an-argon2-hash-at-all") is False

    def test_empty_stored_hash_returns_false_not_raise(self) -> None:
        verifier = Argon2SecretVerifier()
        assert verifier("any-secret", "") is False

    def test_truncated_hash_returns_false_not_raise(self) -> None:
        verifier = Argon2SecretVerifier()
        encoded = verifier.hash("correct-secret")
        truncated = encoded[: len(encoded) // 2]
        assert verifier("correct-secret", truncated) is False

    def test_hash_with_corrupted_parameters_returns_false_not_raise(self) -> None:
        verifier = Argon2SecretVerifier()
        corrupted = "$argon2id$v=19$m=65536,t=3,p=4$not-valid-base64$also-not-valid"
        assert verifier("any-secret", corrupted) is False

    def test_hash_from_a_different_algorithm_returns_false_not_raise(self) -> None:
        verifier = Argon2SecretVerifier()
        # A well-formed bcrypt-shaped string -- structurally different
        # from any argon2 variant.
        bcrypt_shaped = "$2b$12$KIXQ9m5v5v5v5v5v5v5v5uYQ9m5v5v5v5v5v5v5v5v5v5v5v5v5"
        assert verifier("any-secret", bcrypt_shaped) is False

    def test_malformed_hash_error_never_leaks_the_presented_secret(self) -> None:
        verifier = Argon2SecretVerifier()
        # The call itself must not raise; if it somehow did, the test
        # would fail here rather than silently passing.
        result = verifier("a-very-distinctive-presented-secret-value", "garbage")
        assert result is False


class TestNoManualHashingOrComparison:
    def test_hash_delegates_to_the_libraries_password_hasher(self) -> None:
        source = inspect.getsource(Argon2SecretVerifier.hash)
        assert "self._hasher.hash(" in source

    def test_call_delegates_to_the_libraries_verify_and_adds_no_preliminary_check(self) -> None:
        source = inspect.getsource(Argon2SecretVerifier.__call__)
        assert "self._hasher.verify(" in source
        # No `==`/`!=` comparison of presented_secret or secret_hash
        # anywhere in the verification path -- the library's own
        # constant-time verification is the sole authority.
        assert "presented_secret ==" not in source
        assert "presented_secret !=" not in source
        assert "== secret_hash" not in source
        assert "!= secret_hash" not in source


class TestSatisfiesTheApprovedSecretVerifierProtocol:
    def test_call_signature_matches_the_approved_protocol(self) -> None:
        coordinator_signature = inspect.signature(SecretVerifier.__call__)
        verifier_signature = inspect.signature(Argon2SecretVerifier.__call__)
        # Compare parameter names/order excluding `self`.
        expected_params = list(coordinator_signature.parameters)[1:]
        actual_params = list(verifier_signature.parameters)[1:]
        assert actual_params == expected_params

    def test_instance_is_usable_wherever_a_secret_verifier_is_expected(self) -> None:
        verifier: SecretVerifier = Argon2SecretVerifier()
        encoded = verifier.hash("x")  # type: ignore[attr-defined]
        assert verifier("x", encoded) is True


class TestInjectablePasswordHasher:
    def test_accepts_a_pre_constructed_password_hasher(self) -> None:
        hasher = PasswordHasher()
        verifier = Argon2SecretVerifier(password_hasher=hasher)
        encoded = verifier.hash("secret")
        assert verifier("secret", encoded) is True

    def test_defaults_to_a_fresh_password_hasher_when_none_given(self) -> None:
        verifier = Argon2SecretVerifier()
        encoded = verifier.hash("secret")
        assert encoded.startswith("$argon2id$")

    def test_rejects_a_password_hasher_configured_for_argon2i(self) -> None:
        # An injected/preconstructed PasswordHasher cannot cause hash()
        # to emit Argon2i unnoticed -- the non-ID configuration is
        # rejected outright, at construction time.
        with pytest.raises(ValueError, match="Argon2id"):
            Argon2SecretVerifier(password_hasher=PasswordHasher(type=Type.I))

    def test_rejects_a_password_hasher_configured_for_argon2d(self) -> None:
        with pytest.raises(ValueError, match="Argon2id"):
            Argon2SecretVerifier(password_hasher=PasswordHasher(type=Type.D))

    def test_accepts_a_password_hasher_explicitly_configured_for_argon2id(self) -> None:
        verifier = Argon2SecretVerifier(password_hasher=PasswordHasher(type=Type.ID))
        encoded = verifier.hash("secret")
        assert encoded.startswith("$argon2id$")


class TestRequireArgon2idHash:
    def test_accepts_a_genuine_argon2id_hash(self) -> None:
        encoded = Argon2SecretVerifier().hash("secret")
        require_argon2id_hash(encoded)  # must not raise

    def test_rejects_an_argon2i_hash(self) -> None:
        encoded = PasswordHasher(type=Type.I).hash("secret")
        with pytest.raises(InvalidArgon2idHashError):
            require_argon2id_hash(encoded)

    def test_rejects_an_argon2d_hash(self) -> None:
        encoded = PasswordHasher(type=Type.D).hash("secret")
        with pytest.raises(InvalidArgon2idHashError):
            require_argon2id_hash(encoded)

    def test_rejects_a_malformed_hash(self) -> None:
        with pytest.raises(InvalidArgon2idHashError):
            require_argon2id_hash("not-a-hash-at-all")

    def test_rejects_an_empty_string(self) -> None:
        with pytest.raises(InvalidArgon2idHashError):
            require_argon2id_hash("")


class TestArgon2idOnlyVerificationRejectsOtherVariants:
    """`PasswordHasher.verify()` alone accepts a well-formed Argon2i or
    Argon2d hash exactly as readily as Argon2id -- these tests prove
    `Argon2SecretVerifier` rejects both *before* ever delegating to it,
    even when the presented secret is genuinely correct for that hash.
    """

    def test_argon2i_hash_rejected_even_with_the_correct_secret(self) -> None:
        verifier = Argon2SecretVerifier()
        argon2i_hasher = PasswordHasher(type=Type.I)
        encoded = argon2i_hasher.hash("correct-secret")
        assert verifier("correct-secret", encoded) is False

    def test_argon2d_hash_rejected_even_with_the_correct_secret(self) -> None:
        verifier = Argon2SecretVerifier()
        argon2d_hasher = PasswordHasher(type=Type.D)
        encoded = argon2d_hasher.hash("correct-secret")
        assert verifier("correct-secret", encoded) is False

    def test_the_librarys_own_verify_would_have_accepted_the_argon2i_hash(self) -> None:
        # Proves the rejection above is a deliberate Argon2id-only guard,
        # not an accidental side effect of some other check -- the
        # library's own verify() genuinely does accept this hash.
        argon2i_hasher = PasswordHasher(type=Type.I)
        encoded = argon2i_hasher.hash("correct-secret")
        assert argon2i_hasher.verify(encoded, "correct-secret") is True

    def test_the_librarys_own_verify_would_have_accepted_the_argon2d_hash(self) -> None:
        argon2d_hasher = PasswordHasher(type=Type.D)
        encoded = argon2d_hasher.hash("correct-secret")
        assert argon2d_hasher.verify(encoded, "correct-secret") is True

    def test_argon2i_rejection_does_not_raise(self) -> None:
        verifier = Argon2SecretVerifier()
        encoded = PasswordHasher(type=Type.I).hash("secret")
        result = verifier("secret", encoded)  # must not raise
        assert result is False

    def test_uses_the_librarys_own_parameter_parser_not_a_hand_rolled_check(self) -> None:
        from cloudops_guard.ingestion import argon2_backend

        source = inspect.getsource(argon2_backend._is_argon2id_hash)
        assert "extract_parameters" in source
        assert "startswith" not in source
        assert "$argon2id$" not in source
