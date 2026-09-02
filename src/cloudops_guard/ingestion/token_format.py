"""Bearer-token structure for the v0.4.0 ingestion API
(`docs/milestones/v0.4.0-ingestion-api.md` §F): `<lookup_id>.<secret>`.

`lookup_id` and `secret` are both URL-safe-base64 text (RFC 4648 §5
alphabet: `A-Z`, `a-z`, `0-9`, `-`, `_`, produced by `secrets.token_urlsafe`
in `token_issuance.py`) -- that alphabet never contains `.`, so splitting a
well-formed token on its single `.` is unambiguous, and no character-level
normalization (case-folding, whitespace-stripping, padding-restoration) is
ever applied to a presented token; a value that does not already match
exactly is rejected outright, never coerced into an accepted one.

This module only parses and structurally validates -- it never looks up a
`lookup_id`, never touches `TokenStore`, and never invokes Argon2id.
Validating structure first (cheap) before any of that expensive/stateful
work happens is what lets a malformed token be rejected without forcing a
store lookup or a hash computation (`docs/milestones/
v0.4.0-ingestion-api.md` §F Layer 1/2 rationale).
"""

from __future__ import annotations

import re

from ._secure_value import ImmutableRedactedValue
from .errors import TokenFormatError

TOKEN_DELIMITER = "."

# Matches token_issuance.py's LOOKUP_ID_BYTES/SECRET_BYTES exactly: both
# lengths are a direct, fixed function of a fixed input byte count fed to
# `secrets.token_urlsafe`, so a real, freshly-issued token's components
# always have exactly these lengths -- anything else is rejected before
# any other check.
LOOKUP_ID_LENGTH = 22
SECRET_LENGTH = 43

_URL_SAFE_BASE64_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ParsedToken(ImmutableRedactedValue):
    """The two components of a structurally-valid presented token.

    **Not** a `dataclasses.dataclass` -- see `_secure_value.py` for why.
    `secret` is excluded from `__repr__`/`__str__` so printing, logging,
    or an uncaught-exception traceback showing a `ParsedToken` local
    variable never displays the plaintext secret -- callers must access
    `.secret` explicitly. Immutable (`object.__setattr__` is the only way
    to set either attribute, done once here in `__init__`), has no
    instance `__dict__` (only `lookup_id`/`secret` exist, via
    `__slots__`), is not a dataclass (`dataclasses.is_dataclass` is
    `False`; `dataclasses.asdict` raises `TypeError`), is not JSON-
    serializable (`json.dumps` raises `TypeError`), and is not picklable
    (inherited from `ImmutableRedactedValue`).
    """

    __slots__ = ("lookup_id", "secret")

    def __init__(self, lookup_id: str, secret: str) -> None:
        object.__setattr__(self, "lookup_id", lookup_id)
        object.__setattr__(self, "secret", secret)

    def __repr__(self) -> str:
        return f"ParsedToken(lookup_id={self.lookup_id!r}, secret=<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ParsedToken):
            return NotImplemented
        return self.lookup_id == other.lookup_id and self.secret == other.secret

    def __hash__(self) -> int:
        return hash((self.lookup_id, self.secret))


def parse_token(token: str) -> ParsedToken:
    """Splits and validates a presented token's structure.

    Raises `TokenFormatError` (never revealing the presented value or any
    substring of it in the message) if: `token` is not a string, contains
    zero or more than one `TOKEN_DELIMITER`, either resulting component is
    empty, either component's length does not match the fixed expected
    length, or either component contains a character outside the
    URL-safe-base64 alphabet.
    """
    if not isinstance(token, str):
        raise TokenFormatError("presented token must be a string.")

    parts = token.split(TOKEN_DELIMITER)
    if len(parts) != 2:
        raise TokenFormatError(
            "presented token must contain exactly one delimiter between lookup_id and secret."
        )

    lookup_id, secret = parts
    if lookup_id == "" or secret == "":
        raise TokenFormatError("presented token's lookup_id and secret must both be non-empty.")
    if len(lookup_id) != LOOKUP_ID_LENGTH:
        raise TokenFormatError("presented token's lookup_id has an unexpected length.")
    if len(secret) != SECRET_LENGTH:
        raise TokenFormatError("presented token's secret has an unexpected length.")
    if not _URL_SAFE_BASE64_RE.match(lookup_id):
        raise TokenFormatError("presented token's lookup_id contains an invalid character.")
    if not _URL_SAFE_BASE64_RE.match(secret):
        raise TokenFormatError("presented token's secret contains an invalid character.")

    return ParsedToken(lookup_id=lookup_id, secret=secret)
