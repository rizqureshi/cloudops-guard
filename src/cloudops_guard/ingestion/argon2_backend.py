"""Argon2id secret hashing and verification, via `argon2-cffi`
(`docs/milestones/v0.4.0-ingestion-api.md` §F: "the current standard
choice for this exact purpose").

**Dependency justification** (`CLAUDE.md`'s dependency-discipline rule:
"avoid unnecessary dependencies... justify any addition"): Phase 4C's own
authorization requires "Argon2id hashing and verification" and explicitly
names `argon2-cffi` as "the expected choice." No pure-stdlib equivalent
exists -- the standard library has no Argon2 implementation of any kind
(`hashlib` offers PBKDF2/scrypt, not Argon2). `argon2-cffi` is the
long-established reference Python binding to the reference Argon2 C
implementation (the winner of the 2015 Password Hashing Competition), used
here entirely through its documented high-level `PasswordHasher` API --
this module never implements hashing, salting, or comparison itself.

**Parameter policy**: this module uses `argon2.PasswordHasher()`'s
**documented library defaults, unchanged** -- `Type.ID` (Argon2id),
`time_cost=3`, `memory_cost=65536` (64 MiB), `parallelism=4`,
`hash_len=32`, `salt_len=16` (argon2-cffi 25.1.0). This is a deliberate,
documented choice of one of the two policies Phase 4C's own instructions
permit (library defaults, or a documented RFC 9106 profile) -- **not** a
claim that these parameters are production-tuned for any specific
deployment's real hardware/traffic/threat model. Real runtime/memory
parameter validation against production infrastructure is an explicit
later deployment decision, not a Phase 4C one.

**Argon2id-only, enforced, not merely assumed**: `argon2-cffi`'s own
`PasswordHasher.verify()` accepts a well-formed Argon2i or Argon2d
encoded hash exactly as readily as Argon2id -- the encoded hash string
itself names which variant to verify against, and the library honors
whatever it is told. Accepting that would silently violate this project's
Argon2id-only contract, so every encoded hash this module is asked to
verify (`Argon2SecretVerifier.__call__`) or willing to produce
(`Argon2SecretVerifier.hash`, `require_argon2id_hash`) is first parsed
with the library's own supported parameter parser
(`argon2.extract_parameters`) and its algorithm checked to be exactly
`Type.ID` -- never a hand-rolled prefix/substring check on the encoded
text. `Argon2SecretVerifier.__init__` additionally refuses to wrap a
`PasswordHasher` that was not itself configured for `Type.ID`, so an
injected/preconstructed hasher cannot cause `hash()` to emit an
Argon2i/Argon2d hash unnoticed in the first place; `hash()` then
independently re-validates its own output as defense in depth.
"""

from __future__ import annotations

from typing import Protocol

from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import Argon2Error, InvalidHashError
from argon2.low_level import Type

from .errors import InvalidArgon2idHashError


class SecretHasher(Protocol):
    """The hashing-side counterpart to `interfaces.SecretVerifier` --
    anything with a `.hash(secret)` method returning an encoded hash
    string. `Argon2SecretVerifier` is the only production implementation;
    this `Protocol` exists purely as a documentation-level type
    description and is not accepted as a parameter anywhere in this
    package's public API (see `token_issuance.py`'s module docstring for
    why `provision_token` deliberately does not accept an injectable
    hasher of any kind).
    """

    def hash(self, secret: str) -> str: ...


def _is_argon2id_hash(encoded_hash: str) -> bool:
    """`True` only if `encoded_hash` parses as a well-formed Argon2
    encoded hash whose algorithm is exactly Argon2id -- never Argon2i,
    Argon2d, malformed, truncated, or an unrelated scheme (e.g. a
    bcrypt-shaped string). Uses `argon2.extract_parameters`, the
    library's own supported parameter parser, never a hand-rolled prefix
    check on the encoded text.
    """
    try:
        parameters = extract_parameters(encoded_hash)
    except (Argon2Error, ValueError):
        # `InvalidHashError` (raised for anything malformed/truncated/
        # unrelated) is itself a `ValueError` subclass; `Argon2Error`
        # covers the library's other own exception types defensively.
        return False
    return parameters.type is Type.ID


def require_argon2id_hash(encoded_hash: str) -> None:
    """Fail-closed guard: raises `errors.InvalidArgon2idHashError` unless
    `encoded_hash` is a genuine Argon2id encoded hash. Used to validate
    `provision_token`'s own output before it is ever placed in a
    `TokenRecord` (`token_issuance.py`), and by `Argon2SecretVerifier.hash`
    to validate its own output before returning it.
    """
    if not _is_argon2id_hash(encoded_hash):
        raise InvalidArgon2idHashError("encoded hash is not a valid Argon2id hash.")


class Argon2SecretVerifier:
    """Implements both roles Phase 4C needs around one `PasswordHasher`
    instance:

    - `.hash(secret)`, used by `token_issuance.provision_token` at
      issuance time.
    - `__call__(presented_secret, secret_hash)`, satisfying the Phase 4B
      `interfaces.SecretVerifier` protocol exactly (its approved
      signature is unchanged) -- inject an instance of this class into
      `reference.InMemoryTokenStore(secret_verifier=...)` so
      `TokenStore.verify_secret` delegates to real Argon2id.

    Contains no hashing, salting, or comparison logic of its own -- every
    hash and every verification is delegated entirely to `PasswordHasher`.
    In particular, `__call__` never performs a preliminary `==` comparison
    of any kind before invoking `PasswordHasher.verify`; the library's own
    verification operation is authoritative for every outcome a
    genuinely Argon2id-encoded hash can produce. `__call__` does perform
    one check *before* delegating to the library: that `secret_hash`
    parses as Argon2id at all (see this module's own docstring) -- this
    is format/algorithm validation, not secret comparison, and it never
    inspects `presented_secret`.
    """

    def __init__(self, password_hasher: PasswordHasher | None = None) -> None:
        hasher = password_hasher if password_hasher is not None else PasswordHasher()
        if hasher.type is not Type.ID:
            raise ValueError(
                "Argon2SecretVerifier requires a PasswordHasher configured for Argon2id "
                "(Type.ID); refusing to wrap a hasher configured for Argon2i/Argon2d."
            )
        self._hasher = hasher

    def hash(self, secret: str) -> str:
        """A fresh Argon2id hash of `secret`, with a fresh random salt
        (`PasswordHasher.hash` generates one internally on every call --
        hashing the same secret twice never produces the same encoded
        hash). The encoded result identifies Argon2id (`$argon2id$...`),
        which is independently re-validated here (via
        `require_argon2id_hash`) before being returned -- defense in
        depth on top of `__init__`'s own Argon2id-configuration check, so
        no exotic `PasswordHasher` configuration can cause this method to
        silently return a non-Argon2id hash.
        """
        encoded = self._hasher.hash(secret)
        require_argon2id_hash(encoded)
        return encoded

    def __call__(self, presented_secret: str, secret_hash: str) -> bool:
        """Verifies `presented_secret` against the encoded `secret_hash`.

        Returns `True` only if `secret_hash` is a genuine Argon2id
        encoded hash *and* `PasswordHasher.verify` itself succeeds.
        Returns `False` -- never raising, never leaking `presented_secret`
        or the library's own exception text -- for every other outcome: a
        `secret_hash` that is not Argon2id at all (Argon2i, Argon2d,
        malformed, truncated, or an unrelated scheme -- rejected before
        `PasswordHasher.verify` is ever called), a wrong secret
        (`VerifyMismatchError`), a malformed/corrupt encoded hash that
        nonetheless claimed to be Argon2id (`InvalidHashError`; or a
        decode failure raised as a plain `VerificationError`), or any
        other `Argon2Error`. This is the sole `SecretVerifier`
        implementation Phase 4C provides;
        `reference.InMemoryTokenStore.verify_secret` delegates to it (or
        a test's fake) and performs no verification logic of its own.
        """
        if not _is_argon2id_hash(secret_hash):
            return False
        try:
            self._hasher.verify(secret_hash, presented_secret)
        except (Argon2Error, InvalidHashError):
            return False
        return True
