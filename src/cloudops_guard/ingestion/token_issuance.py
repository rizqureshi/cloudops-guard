"""Token generation for the manual, out-of-band provisioning procedure
(`docs/milestones/v0.4.0-ingestion-api.md` §F; see this package's
`docs/manual-token-provisioning.md` for the full operator-facing
procedure).

This module only *generates* values -- it never inserts anything into a
`TokenStore`. Phase 4B's `TokenStore` interface (`interfaces.py`) exposes
no creation/insert method (only `lookup`/`verify_secret`/`mark_revoked`),
and this phase does not add one: storage insertion for a real deployment
is an explicit later production-store responsibility
(`docs/milestones/v0.4.0-ingestion-api.md` §I, Phase 4C's own exclusions).
`reference.InMemoryTokenStore.register_for_testing` remains a
test-only seeding hook, never repurposed here as a disguised production
provisioning API.

**`provision_token` accepts no injectable hasher of any kind.** An
earlier revision of this module accepted a `hasher: SecretHasher`
keyword argument, letting any caller substitute an object whose `.hash()`
method returned anything at all -- including recoverable plaintext (e.g.
`f"plaintext:{secret}"`) -- which would then be stored verbatim in
`TokenRecord.secret_hash`. That seam has been removed outright: the
public production provisioning path always uses the real
`argon2_backend.Argon2SecretVerifier`, and `provision_token` additionally
validates its own output as a genuine Argon2id hash
(`argon2_backend.require_argon2id_hash`) before ever constructing a
`TokenRecord` around it -- failing closed (raising, never silently
storing something else) if that invariant is somehow violated. Tests that
need a fast, non-cryptographic stand-in construct a `TokenRecord`
directly with an opaque placeholder `secret_hash` (never one containing
or derived from the plaintext secret) instead of calling this function --
see `tests/test_ingestion_authenticator.py` for the established pattern.
"""

from __future__ import annotations

import datetime as dt
import secrets
from collections.abc import Callable, Iterable

from ._secure_value import ImmutableRedactedValue
from .argon2_backend import Argon2SecretVerifier, require_argon2id_hash
from .models import TokenRecord, TokenScope
from .token_format import TOKEN_DELIMITER

# Byte counts fed to `secrets.token_urlsafe` -- the source of
# `token_format.LOOKUP_ID_LENGTH`/`SECRET_LENGTH`'s fixed character
# lengths. `SECRET_BYTES = 32` is exactly the 256 random bits
# `docs/milestones/v0.4.0-ingestion-api.md` §F proposes.
LOOKUP_ID_BYTES = 16
SECRET_BYTES = 32


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def generate_lookup_id() -> str:
    """A fresh, server-generated, fixed-length, high-entropy identifier --
    deliberately not secret (`docs/milestones/v0.4.0-ingestion-api.md`
    §F: "not itself secret ... the actual lookup mechanism, not a
    cosmetic label"). Always drawn from `secrets` (the stdlib
    cryptographically secure RNG) -- there is no parameter to substitute
    a different, insecure source, in tests or otherwise.
    """
    return secrets.token_urlsafe(LOOKUP_ID_BYTES)


def generate_secret() -> str:
    """A fresh, independently-random 256-bit secret
    (`docs/milestones/v0.4.0-ingestion-api.md` §F), generated
    independently of any `lookup_id`. Always drawn from `secrets` -- there
    is no parameter to substitute a different, insecure source, in tests
    or otherwise. Never stored anywhere in recoverable form; only its
    Argon2id hash is ever retained (see `argon2_backend.py`).
    """
    return secrets.token_urlsafe(SECRET_BYTES)


class ProvisionedToken(ImmutableRedactedValue):
    """The one-time output of `provision_token`: the complete plaintext
    token, for immediate, out-of-band delivery to the operator, plus the
    `TokenRecord` a caller is responsible for inserting into whichever
    concrete `TokenStore` is in use (for local reference-store testing,
    `reference.InMemoryTokenStore.register_for_testing`).

    **Not** a `dataclasses.dataclass` -- see `_secure_value.py` for why.
    `token` is excluded from `__repr__`/`__str__` so printing, logging, or
    an uncaught-exception traceback showing a `ProvisionedToken` local
    variable never displays the plaintext token -- callers must access
    `.token` explicitly, and only long enough to hand it to the operator
    once. Immutable, has no instance `__dict__`, is not a dataclass
    (`dataclasses.asdict` raises `TypeError`), is not JSON-serializable,
    and is not picklable (inherited from `ImmutableRedactedValue`).
    """

    __slots__ = ("token", "token_record")

    def __init__(self, token: str, token_record: TokenRecord) -> None:
        object.__setattr__(self, "token", token)
        object.__setattr__(self, "token_record", token_record)

    def __repr__(self) -> str:
        return f"ProvisionedToken(token=<redacted>, token_record={self.token_record!r})"

    def __str__(self) -> str:
        return self.__repr__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProvisionedToken):
            return NotImplemented
        return self.token == other.token and self.token_record == other.token_record

    def __hash__(self) -> int:
        return hash((self.token, self.token_record))


def provision_token(
    tenant_id: str,
    scopes: Iterable[TokenScope],
    *,
    clock: Callable[[], dt.datetime] | None = None,
) -> ProvisionedToken:
    """Implements the manual, out-of-band provisioning procedure's
    generation step (see `docs/manual-token-provisioning.md` for the full
    operator procedure this function is one step of):

    1. An authorized operator supplies `tenant_id` and explicit `scopes`
       (this function's own two required arguments -- there is no default
       tenant or default scope set).
    2. `lookup_id` and `secret` are generated independently
       (`generate_lookup_id`/`generate_secret`).
    3. Only `lookup_id`, the Argon2id hash of `secret`, `tenant_id`,
       `scopes`, `revoked=False`, and `created_at` are captured in the
       returned `TokenRecord` -- `secret` itself is never included.
    4. The complete plaintext token (`f"{lookup_id}.{secret}"`) is
       returned exactly once, via `ProvisionedToken.token`, for secure,
       out-of-band delivery -- this function itself never logs, prints,
       or persists it anywhere.

    Always hashes `secret` with a fresh `Argon2SecretVerifier()` -- real
    Argon2id, unconditionally. There is no `hasher` parameter of any kind
    on this public function (see this module's own docstring for why).
    `clock` defaults to the real current UTC time; injectable for
    deterministic `created_at` values in tests, exactly like `reference.py`'s
    existing `clock` parameters -- this affects only the timestamp, never
    the cryptography.
    """
    active_clock = clock if clock is not None else _utc_now

    lookup_id = generate_lookup_id()
    secret = generate_secret()
    secret_hash = Argon2SecretVerifier().hash(secret)
    # Fail closed: never construct a TokenRecord around anything but a
    # genuine Argon2id hash. Argon2SecretVerifier.hash() already
    # guarantees this internally -- this is deliberate defense in depth,
    # so a future refactor that loosens that guarantee cannot silently
    # cause this function to store a non-Argon2id (or non-hash) value.
    require_argon2id_hash(secret_hash)

    token_record = TokenRecord(
        lookup_id=lookup_id,
        secret_hash=secret_hash,
        tenant_id=tenant_id,
        scopes=frozenset(scopes),
        revoked=False,
        created_at=active_clock(),
    )
    token = f"{lookup_id}{TOKEN_DELIMITER}{secret}"
    return ProvisionedToken(token=token, token_record=token_record)
