"""Closed-envelope validation for `POST /api/v1/reports`
(`docs/milestones/v0.4.0-ingestion-api.md` §E.0/§E.2): exactly
`platform`, `report_schema_version`, `report`, and an optional
`idempotency_key` -- nothing else, each with an exact expected JSON type.
Runs strictly after `strict_json.strict_decode_json` and strictly before
platform-specific report-schema validation (§E.0's explicit ordering).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import INVALID_REQUEST, ApiError

_ALLOWED_TOP_LEVEL_FIELDS = frozenset(
    {"platform", "report_schema_version", "report", "idempotency_key"}
)
_ALLOWED_PLATFORMS = frozenset({"kubernetes", "gitlab"})
_MAX_IDEMPOTENCY_KEY_LENGTH = 200


@dataclass(frozen=True, slots=True)
class ReportEnvelope:
    platform: str
    #: `int` for the ordinary case, or a `float` with an exact integer
    #: value (e.g. `1.0`) -- see `parse_envelope`'s own comment on this
    #: field for why a float is accepted and never coerced to `int`.
    report_schema_version: int | float
    report: dict[str, Any]
    idempotency_key: str | None


def parse_envelope(decoded: Any) -> ReportEnvelope:
    """Raises `ApiError(INVALID_REQUEST)` for: a non-object top-level
    body; any top-level field outside the closed set (including,
    explicitly, a `tenant_id`/`customer_id`/any other identity-naming
    field -- never silently ignored or stripped); a missing required
    field; or a wrong JSON type for any of the four fields.
    """
    if not isinstance(decoded, dict):
        raise ApiError(INVALID_REQUEST)

    unknown_fields = set(decoded.keys()) - _ALLOWED_TOP_LEVEL_FIELDS
    if unknown_fields:
        raise ApiError(INVALID_REQUEST)

    if not {"platform", "report_schema_version", "report"} <= decoded.keys():
        raise ApiError(INVALID_REQUEST)

    platform = decoded["platform"]
    if not isinstance(platform, str) or platform not in _ALLOWED_PLATFORMS:
        raise ApiError(INVALID_REQUEST)

    report_schema_version = decoded["report_schema_version"]
    # `bool` is an `int` subclass in Python, and JSON `true`/`false`
    # decode to Python `bool` -- must never be mistaken for a valid
    # integer schema version. The approved contract accepts "a JSON
    # number with an integer value" -- both a genuine integer literal
    # (`1`, decoding to Python `int`) and a JSON number with a fractional
    # `.0` (`1.0`, decoding to Python `float`) satisfy that, and RFC 8785
    # canonicalizes them identically (§E.0), so a float is accepted here
    # exactly when it represents an exact integer value (`1.0`, never
    # `1.5`) -- and passed through UNCHANGED to fingerprinting, never
    # coerced to `int`, so `platform`/`report_schema_version`/`report`
    # remain "exactly as given in the envelope" per §E.0's own algorithm.
    # A non-finite float (`inf`/`nan`) can never reach this point at all:
    # `strict_json.strict_decode_json` already rejects every non-finite
    # number anywhere in the document, including this field, before
    # `parse_envelope` is ever called. A numeric STRING (e.g. `"1"`)
    # remains rejected, never coerced -- the explicit, unrelated rule
    # this field has always enforced.
    if isinstance(report_schema_version, bool):
        raise ApiError(INVALID_REQUEST)
    elif isinstance(report_schema_version, float):
        if not report_schema_version.is_integer():
            raise ApiError(INVALID_REQUEST)
    elif not isinstance(report_schema_version, int):
        raise ApiError(INVALID_REQUEST)

    report = decoded["report"]
    if not isinstance(report, dict):
        raise ApiError(INVALID_REQUEST)

    idempotency_key: str | None = decoded.get("idempotency_key")
    if idempotency_key is not None:
        if not isinstance(idempotency_key, str):
            raise ApiError(INVALID_REQUEST)
        if len(idempotency_key) > _MAX_IDEMPOTENCY_KEY_LENGTH:
            raise ApiError(INVALID_REQUEST)

    return ReportEnvelope(
        platform=platform,
        report_schema_version=report_schema_version,
        report=report,
        idempotency_key=idempotency_key,
    )
