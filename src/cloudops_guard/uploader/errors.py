"""Typed exceptions for the `cloudops-guard upload` command. None of
these ever carries the plaintext ingestion token, the report's own
content, or a raw, unsanitized transport exception -- `service.py`'s
CLI-facing message construction stays strictly within what each
exception's own docstring says it may contain.
"""

from __future__ import annotations


class UploaderError(Exception):
    """Base class for every exception this package raises."""


class LocalReportError(UploaderError):
    """Raised for any local report-loading/validation failure: a missing
    or unreadable `report.json`, a file exceeding the local size ceiling,
    a strict-JSON decode violation, an unsupported/inconsistent platform
    marker, or a report failing `AuditReport`/`GitLabAuditReport`
    validation (including a findings-count or report-byte-size ceiling).
    Never includes report content -- only a description of which check
    failed.
    """


class EndpointValidationError(UploaderError):
    """Raised when `--endpoint`/`CLOUDOPS_GUARD_INGESTION_URL` fails
    local validation: missing scheme/authority, embedded username/
    password, query parameters, a fragment, a path other than exactly
    `/api/v1/reports`, or a non-loopback `http://` URL (HTTPS is required
    except for a syntactically-recognized loopback address). Never
    performs DNS resolution to decide this.
    """


class ConfirmationAborted(UploaderError):
    """Raised when interactive confirmation is rejected: the typed
    response was not the exact, case-sensitive string `UPLOAD`, or the
    prompt was interrupted by EOF or Ctrl-C. No network request is ever
    made when this is raised.
    """


class NonInteractiveConfirmationRequired(UploaderError):
    """Raised immediately, without reading from stdin at all, when stdin
    is not interactive and neither `--yes` nor `--dry-run` was supplied.
    Fails closed rather than risking a hang waiting for input that will
    never arrive.
    """


class CredentialError(UploaderError):
    """Raised when `CLOUDOPS_GUARD_INGESTION_TOKEN` is unset, empty, or
    structurally malformed. Never includes the presented value or any
    substring of it.
    """


class UploadTransportError(UploaderError):
    """Raised for any transport-layer failure: a connection, DNS, TLS,
    or timeout failure; a redirect response (never followed); a
    malformed or oversized HTTP response; or a documented API error
    status. Carries only a sanitized category/summary -- never a raw
    native transport exception, request headers, or response body
    content that could reveal infrastructure detail.
    """


class FingerprintMismatchError(UploaderError):
    """Raised when a successful response's `report_fingerprint` does not
    exactly equal the fingerprint computed locally before the request was
    sent -- treated as a failure, never reported as success, even though
    the server accepted the request.
    """
