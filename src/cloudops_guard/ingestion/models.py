"""Internal domain types for the v0.4.0 ingestion API's storage layer.

Phase 4B implements these types plus local, in-memory reference storage
interfaces (`interfaces.py`, `reference.py`) for the design
`docs/milestones/v0.4.0-ingestion-api.md` §H describes. These are internal
records the storage layer manages -- not the wire-level JSON request/
response shapes §E defines (no HTTP layer exists yet; that is Phase 4D).
Field names mirror §E.4's own truthful-naming requirement exactly:
`retired_at`/`reason` never imply a customer action for an
automatically-triggered retention-expiry retirement.

All datetimes are timezone-aware UTC; a naive datetime is rejected at
construction (a `pydantic.ValidationError`), mirroring the existing
`GitLabProjectSnapshot.collected_at` validator in `cloudops_guard.models`
exactly.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class IngestionStatus(StrEnum):
    """The complete status enum `docs/milestones/v0.4.0-ingestion-api.md`
    §E.4 defines -- and the only one. There is no `pending`/`processing`/
    `rejected` status: a request that fails validation never creates a
    record at all (§E.2), and this milestone defines no processing
    pipeline beyond durable receipt (§E.4).
    """

    RECEIVED = "received"
    RETIRED = "retired"
    DELETED = "deleted"


class RetirementReason(StrEnum):
    """Why a record was retired -- distinct from *whether*/*when*, so the
    status and timestamp fields never have to lie about which trigger
    caused retirement (§E.4).
    """

    CUSTOMER_REQUESTED = "customer_requested"
    RETENTION_EXPIRED = "retention_expired"


class TokenScope(StrEnum):
    """§F's minimum required scopes. Phase 4B stores scope membership on a
    `TokenRecord`; it never evaluates or enforces authorization against a
    request -- that is a later phase's job.
    """

    REPORTS_WRITE = "reports:write"
    REPORTS_READ = "reports:read"
    REPORTS_DELETE = "reports:delete"


def _require_timezone_aware(value: dt.datetime, field_name: str) -> dt.datetime:
    # Mirrors `cloudops_guard.models.GitLabProjectSnapshot`'s own validator
    # exactly: `tzinfo is not None` alone is insufficient, since a custom
    # `tzinfo` subclass can be attached yet still return `None` from
    # `utcoffset()`, which behaves like a naive datetime for arithmetic/
    # comparison purposes.
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value


class IngestionRecord(BaseModel):
    """One ingestion's full metadata record, as `MetadataStore` holds it.

    Frozen (immutable): a caller holding a reference returned from a store
    cannot mutate stored state by assigning to a field. Every state
    transition (`mark_retired`/`mark_purged`) returns a *new*
    `IngestionRecord` instance rather than mutating one in place.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1)
    ingestion_id: str = Field(min_length=1)
    # Treated as an opaque, already-computed string by the storage layer --
    # neither the RFC 8785/SHA-256 algorithm (§E.0) nor its "sha256:<hex>"
    # format is validated here; computing it is a Phase 4D concern.
    report_fingerprint: str = Field(min_length=1)
    received_at: dt.datetime
    status: IngestionStatus
    reason: RetirementReason | None = None
    retired_at: dt.datetime | None = None
    deleted_at: dt.datetime | None = None

    @field_validator("received_at", "retired_at", "deleted_at")
    @classmethod
    def _timestamps_must_be_timezone_aware(
        cls, value: dt.datetime | None, info: ValidationInfo
    ) -> dt.datetime | None:
        if value is None:
            return None
        return _require_timezone_aware(value, info.field_name)

    @model_validator(mode="after")
    def _status_fields_are_internally_consistent(self) -> IngestionRecord:
        if self.status is IngestionStatus.RECEIVED:
            if (
                self.reason is not None
                or self.retired_at is not None
                or self.deleted_at is not None
            ):
                raise ValueError(
                    "a 'received' record must not carry reason, retired_at, or deleted_at."
                )
        elif self.status is IngestionStatus.RETIRED:
            if self.reason is None or self.retired_at is None:
                raise ValueError("a 'retired' record must carry both reason and retired_at.")
            if self.deleted_at is not None:
                raise ValueError("a 'retired' record must not yet carry deleted_at.")
        elif self.status is IngestionStatus.DELETED:
            if self.reason is None or self.retired_at is None or self.deleted_at is None:
                raise ValueError(
                    "a 'deleted' record must carry reason, retired_at, and deleted_at."
                )

        if self.retired_at is not None and self.retired_at < self.received_at:
            raise ValueError("retired_at must not precede received_at.")
        if (
            self.deleted_at is not None
            and self.retired_at is not None
            and self.deleted_at < self.retired_at
        ):
            raise ValueError("deleted_at must not precede retired_at.")
        return self


class Tombstone(BaseModel):
    """The minimal, content-free record that outlives the full
    `IngestionRecord` during tombstone retention (§E.4) -- never contains
    report content or findings, only enough to keep repeated `DELETE`
    calls idempotent and informative.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1)
    ingestion_id: str = Field(min_length=1)
    reason: RetirementReason
    retired_at: dt.datetime
    deleted_at: dt.datetime

    @field_validator("retired_at", "deleted_at")
    @classmethod
    def _timestamps_must_be_timezone_aware(
        cls, value: dt.datetime, info: ValidationInfo
    ) -> dt.datetime:
        return _require_timezone_aware(value, info.field_name)

    @model_validator(mode="after")
    def _deleted_at_not_before_retired_at(self) -> Tombstone:
        if self.deleted_at < self.retired_at:
            raise ValueError("deleted_at must not precede retired_at.")
        return self


class TokenRecord(BaseModel):
    """Storage-layer record for one bearer token's `lookup_id`/`secret`
    split (§F). Never holds a plaintext secret: `secret_hash` is the only
    secret-derived field, and Phase 4B never computes, verifies, or
    fabricates one -- a caller supplies an already-computed hash (a real
    Argon2id hash is Phase 4C's job); this type stores and returns it
    mechanically, as an opaque string, and nothing in this package ever
    logs or prints it.
    """

    model_config = ConfigDict(frozen=True)

    lookup_id: str = Field(min_length=1)
    secret_hash: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    scopes: frozenset[TokenScope]
    revoked: bool
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_timezone_aware(cls, value: dt.datetime) -> dt.datetime:
        return _require_timezone_aware(value, "created_at")

    @field_validator("scopes", mode="before")
    @classmethod
    def _scopes_must_be_non_empty(cls, value: object) -> object:
        if not isinstance(value, (frozenset, set, list, tuple)):
            return value  # let pydantic's own type coercion raise
        scopes = frozenset(value)
        if not scopes:
            raise ValueError("scopes must not be empty.")
        return scopes
