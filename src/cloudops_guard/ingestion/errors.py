"""Typed exceptions for the v0.4.0 ingestion storage layer (Phase 4B).

None of these carry an HTTP status code -- Phase 4B implements no HTTP
layer. A future Phase 4D translates them into the fixed error envelope
`docs/milestones/v0.4.0-ingestion-api.md` §E defines; that mapping belongs
to Phase 4D, not here.
"""

from __future__ import annotations


class IngestionStorageError(Exception):
    """Base class for every exception this package raises."""


class IdempotencyKeyConflict(IngestionStorageError):
    """Raised by `MetadataStore.create_or_get_received` when an active
    `idempotency_key` binding exists for a report_fingerprint different
    from the one this call supplied (`docs/milestones/
    v0.4.0-ingestion-api.md` §E's idempotency semantics, step 3). A future
    Phase 4D HTTP layer maps this to `400 invalid_request`.
    """


class IngestionIdConflict(IngestionStorageError):
    """Raised by `MetadataStore.create_or_get_received` when the create
    step (step 3) is reached with a `new_ingestion_id` that already
    identifies a different record for this tenant -- live, retired,
    deleted, or still within its tombstone-retention window. A caller
    must never observe this: `ingestion_id` values are expected to be
    freshly server-generated (e.g. a UUID) for every genuinely new
    ingestion, so a real collision indicates a caller-side ID-generation
    bug, not a legitimate retry. Raising here, rather than silently
    overwriting the existing identity's record and index entries,
    prevents `_active_fingerprints`/`_idempotency_bindings` from being
    left pointing at a record with the wrong `report_fingerprint`.
    """


class InvalidIdentifierError(IngestionStorageError):
    """Raised by `storage_keys.derive_storage_key` when a supplied
    `tenant_id` or `ingestion_id` fails conservative validation -- a path
    separator, a `..` traversal component, a NUL byte, or an empty string.
    Invalid input is always rejected outright, never normalized into an
    accepted value.
    """
