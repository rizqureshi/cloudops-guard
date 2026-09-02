"""Deterministic, machine-readable HTTP error codes
(`docs/milestones/v0.4.0-ingestion-api.md` §E's complete list) and the
one exception type this package uses to short-circuit straight to the
fixed error envelope. None of these codes is ever a free-text message, an
HTTP reason phrase, or a value that varies between otherwise-identical
failures.
"""

from __future__ import annotations

from typing import Final

INVALID_REQUEST: Final[str] = "invalid_request"
INVALID_REPORT: Final[str] = "invalid_report"
UNSUPPORTED_REPORT_SCHEMA_VERSION: Final[str] = "unsupported_report_schema_version"
UNSUPPORTED_API_VERSION: Final[str] = "unsupported_api_version"
UNAUTHORIZED: Final[str] = "unauthorized"
FORBIDDEN: Final[str] = "forbidden"
NOT_FOUND: Final[str] = "not_found"
METHOD_NOT_ALLOWED: Final[str] = "method_not_allowed"
UNSUPPORTED_CONTENT_TYPE: Final[str] = "unsupported_content_type"
UNSUPPORTED_CONTENT_ENCODING: Final[str] = "unsupported_content_encoding"
PAYLOAD_TOO_LARGE: Final[str] = "payload_too_large"
RATE_LIMITED: Final[str] = "rate_limited"
INTERNAL_ERROR: Final[str] = "internal_error"

_HTTP_STATUS_BY_CODE: Final[dict[str, int]] = {
    INVALID_REQUEST: 400,
    INVALID_REPORT: 400,
    UNSUPPORTED_REPORT_SCHEMA_VERSION: 400,
    UNSUPPORTED_API_VERSION: 404,
    UNAUTHORIZED: 401,
    FORBIDDEN: 403,
    NOT_FOUND: 404,
    METHOD_NOT_ALLOWED: 405,
    UNSUPPORTED_CONTENT_TYPE: 415,
    UNSUPPORTED_CONTENT_ENCODING: 415,
    PAYLOAD_TOO_LARGE: 413,
    RATE_LIMITED: 429,
    INTERNAL_ERROR: 500,
}


class ApiError(Exception):
    """Raised anywhere in the transport/validation layer to short-circuit
    straight to the fixed error envelope (`responses.error_response`).
    Carries only a stable `code` -- never a message, wrapped-exception
    detail, or any other field a handler might be tempted to surface (§E:
    "never an echoed input value... or any other infrastructure detail").
    `allow`, when given, becomes the response's exact `Allow` header value
    -- only ever set for `METHOD_NOT_ALLOWED`.
    """

    def __init__(self, code: str, *, allow: str | None = None) -> None:
        if code not in _HTTP_STATUS_BY_CODE:
            raise ValueError(f"unknown error code: {code!r}")
        super().__init__(code)
        self.code = code
        self.http_status = _HTTP_STATUS_BY_CODE[code]
        self.allow = allow
