"""RFC 8785 report-fingerprint computation
(`docs/milestones/v0.4.0-ingestion-api.md` §E.0's exact algorithm) -- a
pure function of exactly three already-validated values, computed
identically by the CLI uploader (Phase 4E) and the ingestion API, with no
network round-trip and no coordination required between them. Never
includes tenant ID, idempotency key, request ID, ingestion ID, or any
timestamp -- only `platform`, `report_schema_version`, and `report`
together.

**Phase 4E relocation**: this is the authoritative implementation,
shared, unduplicated, between the ingestion API (`cloudops_guard.
ingestion_api.fingerprint`, now a thin compatibility shim over this
module) and the CLI uploader (`cloudops_guard.uploader`) -- moved here,
into the dependency-free `cloudops_guard.ingestion` package, alongside
`strict_json.py`, for the same reason: the base CLI must compute this
fingerprint without requiring the `api` optional-dependency group.
`rfc8785` itself moved from that group into the base runtime
dependencies as part of this same relocation (see `pyproject.toml`).
Raises `errors.ReportFingerprintError` (a plain `IngestionStorageError`)
rather than an HTTP-flavored exception; the ingestion API's own
compatibility shim translates that into its `ApiError(INVALID_REQUEST)`
HTTP-boundary contract, preserving that package's existing observable
behavior unchanged.

`rfc8785.dumps` itself enforces RFC 8785's I-JSON numeric domain (raising
`FloatDomainError` for a non-finite float, `IntegerDomainError` for an
integer outside the safe-integer bound) -- `strict_json.
_validate_decoded_document` already rejects both cases earlier, over the
entire decoded request body, so this call should never actually raise in
ordinary operation. This function still catches `rfc8785.
CanonicalizationError` defensively (never assuming the earlier layer is
the only path that can reach this function, and never letting a
`ValueError` subclass escape as an uncaught error for input this
contract's own numeric-domain rule was designed to reject) -- kept
deliberately independent from, not a replacement for, that earlier, more
specific check. Also catches `RecursionError` defensively --
`rfc8785.dumps` serializes nested containers recursively internally, so
a sufficiently deep `report` value could exhaust it; unreachable in
practice via `strict_json.strict_decode_json`'s own `_MAX_NESTING_DEPTH`
ceiling, but this function is also callable directly on an arbitrary
Python dict that never passed through that ceiling (exactly the CLI
uploader's own call site, on a locally-loaded report file).
"""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785

from .errors import ReportFingerprintError


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

    Raises `errors.ReportFingerprintError` -- never lets a `rfc8785.
    CanonicalizationError`, a `RecursionError`, or any other exception
    escape -- if `report` still somehow contains a numeric value outside
    RFC 8785's representable domain, or is nested deeply enough to risk
    exhausting the canonicalizer's own internal recursion.
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
        raise ReportFingerprintError(str(exc)) from exc
    except RecursionError as exc:
        raise ReportFingerprintError("report nesting exhausted the canonicalizer.") from exc
    digest = hashlib.sha256(canonical_bytes).hexdigest()
    return f"sha256:{digest}"
