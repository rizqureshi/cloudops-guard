"""RFC 8785 report-fingerprint computation
(`docs/milestones/v0.4.0-ingestion-api.md` §E.0's exact algorithm) -- a
pure function of exactly three already-validated values, computed
identically by a future uploader (Phase 4E) and this server, with no
network round-trip and no coordination required between them. Never
includes tenant ID, idempotency key, request ID, ingestion ID, or any
timestamp -- only `platform`, `report_schema_version`, and `report`
together.

**Correction pass, item 2**: `rfc8785.dumps` itself enforces RFC 8785's
I-JSON numeric domain (raising `FloatDomainError` for a non-finite float,
`IntegerDomainError` for an integer outside the safe-integer bound) --
`strict_json._validate_decoded_document` already rejects both cases
earlier, over the entire decoded request body, so this call should never
actually raise in ordinary operation. This function still catches
`rfc8785.CanonicalizationError` defensively (never assuming the earlier
layer is the only path that can reach this function, and never letting a
`ValueError` subclass escape as an uncaught `500 internal_error` for
input this contract's own numeric-domain rule was designed to reject) --
kept deliberately independent from, not a replacement for, that earlier,
more specific check.

**Second correction pass, item 4**: also catches `RecursionError`
defensively -- `rfc8785.dumps` serializes nested containers recursively
internally, so a sufficiently deep `report` value could exhaust it. In
the normal HTTP path this is unreachable in practice (`strict_json.
strict_decode_json`'s own `_MAX_NESTING_DEPTH` ceiling always runs first
and rejects anything deep enough to matter, long before this function is
reached), but this function is also `compute_report_fingerprint`'s public
contract -- callable directly, on an arbitrary Python dict that never
passed through `strict_decode_json` at all (e.g. a future Phase 4E
uploader computing this same fingerprint locally from its own parsed
report file). This is the "direct fingerprint backstop":
`RecursionError` is mapped to the same sanitized `ApiError` as
`CanonicalizationError`, never left to escape uncaught, regardless of how
a caller reached this function.
"""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785

from .errors import INVALID_REQUEST, ApiError


def compute_report_fingerprint(
    platform: str, report_schema_version: int | float, report: dict[str, Any]
) -> str:
    """§E.0's exact algorithm: (1) construct
    `{"platform", "report_schema_version", "report"}` using the values
    exactly as given -- `report` is the parsed value as received, before
    any server-side normalization such as summary recomputation; (2)
    serialize with RFC 8785 JCS; (3) SHA-256 the canonical bytes; (4)
    `"sha256:" + <lowercase hex digest>`. `report_schema_version` may be
    `int` or an integer-valued `float` (e.g. `1.0`) -- RFC 8785
    canonicalizes both identically, so this never needs to special-case
    which one it was given.

    Raises `ApiError(INVALID_REQUEST)` -- never lets a `rfc8785.
    CanonicalizationError`, a `RecursionError` (second correction pass,
    item 4), or any other exception escape as an uncaught `500` -- if
    `report` still somehow contains a numeric value outside RFC 8785's
    representable domain, or is nested deeply enough to risk exhausting
    the canonicalizer's own internal recursion.
    """
    try:
        canonical_bytes = rfc8785.dumps(
            {
                "platform": platform,
                "report_schema_version": report_schema_version,
                "report": report,
            }
        )
    except rfc8785.CanonicalizationError as exc:
        raise ApiError(INVALID_REQUEST) from exc
    except RecursionError as exc:
        raise ApiError(INVALID_REQUEST) from exc
    digest = hashlib.sha256(canonical_bytes).hexdigest()
    return f"sha256:{digest}"
