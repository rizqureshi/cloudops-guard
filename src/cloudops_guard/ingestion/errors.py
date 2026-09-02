"""Typed exceptions for the v0.4.0 ingestion storage and authentication
layers (Phases 4B and 4C).

None of these carry an HTTP status code -- neither phase implements an
HTTP layer. A future Phase 4D translates them into the fixed error
envelope `docs/milestones/v0.4.0-ingestion-api.md` §E defines; that
mapping belongs to Phase 4D, not here.
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


class TokenFormatError(IngestionStorageError):
    """Raised by `token_format.parse_token` when a presented token string
    does not match the approved `<lookup_id>.<secret>` structure --
    missing delimiter, an empty component, or a component of the wrong
    length/character set. Never includes the presented token or any
    substring of it in its message. `AuthenticationCoordinator.authenticate`
    catches this internally and converts it to the same generic
    `AuthenticationFailed` every other authentication failure produces --
    a caller driving `parse_token` directly (e.g. a test) sees this typed
    exception itself.
    """


class AuthenticationFailed(IngestionStorageError):
    """The one, generic, externally-safe authentication-failure result
    (`docs/milestones/v0.4.0-ingestion-api.md` §F/§G) -- raised
    identically for a malformed token, an unknown `lookup_id`, a revoked
    token, a wrong `secret`, a Layer 1 (`AttemptLimiter`, per-`lookup_id`)
    block, and a Layer 2 (per-source) block. The message is a fixed
    constant (`authenticator.GENERIC_AUTHENTICATION_FAILURE_MESSAGE`),
    never varying by cause, so a caller can never distinguish which of
    those conditions occurred. A future Phase 4D HTTP layer maps this to
    `401 unauthorized`.
    """


class AuthorizationFailed(IngestionStorageError):
    """Raised by `authenticator.authorize` when an otherwise-successfully-
    authenticated principal lacks the `TokenScope` a operation requires.
    Distinct from `AuthenticationFailed`: this can only be raised *after*
    authentication has already succeeded. A future Phase 4D HTTP layer
    maps this to `403 forbidden`.
    """


class RateLimited(IngestionStorageError):
    """Raised when an `AttemptLimiter`-backed rate limit is exceeded, in
    either of two framework-independent contexts: `authenticator.
    check_capabilities_allowed`'s public, credential-free Layer 2 (source)
    check, and `AuthenticationCoordinator.authenticate`'s Layer 3
    (per-authenticated-token) check, checked only after a successful
    authentication. Distinct from `AuthenticationFailed` in both cases: no
    credential was rejected, a request is simply being throttled. A future
    Phase 4D HTTP layer maps this to `429 rate_limited`.
    """


class InvalidArgon2idHashError(IngestionStorageError):
    """Raised by `argon2_backend.require_argon2id_hash` when an encoded
    hash fails to parse as Argon2id, or parses as a different Argon2
    variant (Argon2i/Argon2d) or an unrelated scheme entirely. Used to
    fail closed at provisioning/hashing time (`token_issuance.
    provision_token`, `argon2_backend.Argon2SecretVerifier.hash`) --
    never a live customer flow, since it can only be reached if this
    package's own Argon2id-only guarantee has somehow already been
    violated. Never raised by *verification*
    (`Argon2SecretVerifier.__call__` returns `False` instead, per §F's
    fail-safe, no-exception contract for presented credentials) --
    exclusively a production-code-invariant guard, not a
    caller/credential-facing error.
    """
