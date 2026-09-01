"""Storage-key derivation for `ReportBlobStore`
(`docs/milestones/v0.4.0-ingestion-api.md` §H).

A `storage_key` is always `f"{tenant_id}/{ingestion_id}"`, composed only
from already-validated, server-generated identifiers -- never a
caller-supplied filename or a report field. This module's job is exactly
that validation: reject anything that could turn the composed key into a
path-traversal or cross-tenant collision vector, rather than trying to
sanitize or normalize a dangerous value into an accepted one. `tenant_id`
and `ingestion_id` are expected to be server-generated in real use (a real
customer's chosen tenant name or an uploader-supplied value never reaches
this function directly), but this validation is conservative regardless of
who calls it, and is tested against traversal/collision inputs even though
current callers are expected to supply server-generated identifiers.
"""

from __future__ import annotations

from .errors import InvalidIdentifierError

_MAX_IDENTIFIER_LENGTH = 256


def _validate_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise InvalidIdentifierError(f"{field_name} must be a string.")
    if value == "":
        raise InvalidIdentifierError(f"{field_name} must not be empty.")
    if len(value) > _MAX_IDENTIFIER_LENGTH:
        raise InvalidIdentifierError(f"{field_name} exceeds the maximum allowed length.")
    if "\x00" in value:
        raise InvalidIdentifierError(f"{field_name} must not contain a NUL byte.")
    if "/" in value or "\\" in value:
        # Rejects both a path separator *and* an absolute path (which
        # necessarily contains a leading separator) in one check -- never
        # stripped or normalized into an accepted relative value.
        raise InvalidIdentifierError(f"{field_name} must not contain a path separator.")
    if ".." in value:
        raise InvalidIdentifierError(f"{field_name} must not contain '..'.")
    if value in (".",):
        raise InvalidIdentifierError(f"{field_name} must not be '.'.")
    if value != value.strip():
        raise InvalidIdentifierError(f"{field_name} must not have leading or trailing whitespace.")
    return value


def derive_storage_key(tenant_id: str, ingestion_id: str) -> str:
    """Returns the exact `f"{tenant_id}/{ingestion_id}"` storage key §H
    specifies, after conservatively validating both identifiers.

    Raises `InvalidIdentifierError` rather than normalizing a dangerous
    input (e.g. stripping a leading slash, collapsing a `..` component)
    into an accepted one -- a rejected identifier is always rejected
    outright.
    """
    validated_tenant_id = _validate_identifier(tenant_id, "tenant_id")
    validated_ingestion_id = _validate_identifier(ingestion_id, "ingestion_id")
    return f"{validated_tenant_id}/{validated_ingestion_id}"
