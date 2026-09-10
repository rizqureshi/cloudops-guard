"""The operator-only, tenant-scoped ingestion-inventory and offboarding
mechanism (Phase 4G-A, task 11) `docs/pilots/ingestion-pilot-runbook.md`
§16 declares a hard blocker until it exists and is tested. **This is not,
and must never become, a public HTTP endpoint** -- every function here is
an offline, operator-invoked function (a script, a one-off shell, a
Container Apps Job -- never wired into `ingestion_api.app`'s four-route
dispatch table).

**Never relies solely on customer-retained `ingestion_id`s**:
`build_tenant_inventory` queries `PostgresMetadataStore.list_tenant_records`
directly (the database's own tenant-scoped index), so it surfaces every
record for a tenant regardless of whether the customer ever knew about
it -- closing exactly the gap the pilot runbook's own tabletop test
describes (one tenant ingestion ID missing from the customer's own
records).

**Resistant to accidentally selecting another tenant**: every mutating
function requires the operator to pass `tenant_id` **twice** --
`tenant_id` and `confirm_tenant_id`, which must match exactly -- a
lightweight, explicit "confirm the resolved identity" step, mirroring
this project's existing exact-typed-confirmation pattern
(`cloudops-guard upload`'s `UPLOAD` prompt; `deploy-web.yml`'s
confirmation-phrase gate) rather than trusting a single positional
argument a copy-paste error could silently transpose.

**Read-only by default, mutation requires an exact confirmation phrase**:
`build_tenant_inventory` never mutates anything.
`execute_tenant_offboarding` requires `confirmation_phrase` to exactly
equal `f"RETIRE-AND-PURGE-{tenant_id}"` -- both the tenant-identity
double-entry above *and* this phrase must be correct, or nothing is
mutated.

**Append-only audit event per action**: every mutation this module
performs writes one row to `operator_audit_events`
(migration `0005_operator_audit_log.sql`) -- `occurred_at`, `operator`,
`tenant_id`, `action`, `ingestion_id`, `outcome` only. Never a report
field, a token value, or a secret (mirrors this project's existing
ingestion-service log allowlist discipline).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import psycopg

from cloudops_guard.ingestion.models import IngestionStatus, RetirementReason
from cloudops_guard.ingestion_api import lifecycle as ingestion_api_lifecycle
from cloudops_guard.ingestion_api.config import IngestionApiConfig

from .errors import AzureAdapterError, DatabaseUnavailableError
from .pool import IngestionDatabasePool
from .postgres_metadata_store import PostgresMetadataStore


class TenantIdentityMismatchError(AzureAdapterError):
    """Raised when `tenant_id` and `confirm_tenant_id` do not match
    exactly -- the operator-facing signal that a copy-paste or transposed
    argument was caught before touching any data.
    """


class OffboardingConfirmationError(AzureAdapterError):
    """Raised when `confirmation_phrase` does not exactly equal the
    required, tenant-specific phrase.
    """


@dataclass(frozen=True, slots=True)
class TenantInventoryEntry:
    ingestion_id: str
    status: str
    reason: str | None
    received_at: dt.datetime
    retired_at: dt.datetime | None
    deleted_at: dt.datetime | None


def _require_matching_tenant_identity(tenant_id: str, confirm_tenant_id: str) -> None:
    if not tenant_id:
        raise ValueError("tenant_id must not be empty.")
    if tenant_id != confirm_tenant_id:
        raise TenantIdentityMismatchError(
            "tenant_id and confirm_tenant_id must match exactly -- refusing to act "
            "against a possibly-mistyped tenant identity."
        )


def _record_audit_event(
    pool: IngestionDatabasePool,
    *,
    occurred_at: dt.datetime,
    operator: str,
    tenant_id: str,
    action: str,
    ingestion_id: str | None,
    outcome: str,
) -> None:
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO operator_audit_events
                        (occurred_at, operator, tenant_id, action, ingestion_id, outcome)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (occurred_at, operator, tenant_id, action, ingestion_id, outcome),
                )
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc


def build_tenant_inventory(
    metadata_store: PostgresMetadataStore,
    *,
    tenant_id: str,
    confirm_tenant_id: str,
) -> list[TenantInventoryEntry]:
    """Read-only. Returns every ingestion record for `tenant_id`,
    regardless of status -- IDs, status, `reason`, and timestamps only,
    never report content (which `IngestionRecord` never holds in the
    first place, so there is nothing to accidentally include).
    """
    _require_matching_tenant_identity(tenant_id, confirm_tenant_id)
    records = metadata_store.list_tenant_records(tenant_id)
    return [
        TenantInventoryEntry(
            ingestion_id=r.ingestion_id,
            status=r.status.value,
            reason=r.reason.value if r.reason is not None else None,
            received_at=r.received_at,
            retired_at=r.retired_at,
            deleted_at=r.deleted_at,
        )
        for r in records
    ]


@dataclass(frozen=True, slots=True)
class OffboardingPlan:
    """A read-only preview of what `execute_tenant_offboarding` would do
    -- never mutates anything by itself. An operator reviews this before
    supplying the exact confirmation phrase.
    """

    tenant_id: str
    to_retire: list[str]
    to_purge: list[str]
    already_deleted: list[str]


def plan_tenant_offboarding(
    metadata_store: PostgresMetadataStore, *, tenant_id: str, confirm_tenant_id: str
) -> OffboardingPlan:
    _require_matching_tenant_identity(tenant_id, confirm_tenant_id)
    records = metadata_store.list_tenant_records(tenant_id)
    return OffboardingPlan(
        tenant_id=tenant_id,
        to_retire=[r.ingestion_id for r in records if r.status is IngestionStatus.RECEIVED],
        to_purge=[r.ingestion_id for r in records if r.status is IngestionStatus.RETIRED],
        already_deleted=[r.ingestion_id for r in records if r.status is IngestionStatus.DELETED],
    )


def required_offboarding_confirmation_phrase(tenant_id: str) -> str:
    return f"RETIRE-AND-PURGE-{tenant_id}"


@dataclass(frozen=True, slots=True)
class OffboardingResult:
    tenant_id: str
    retired: list[str]
    purged: list[str]
    already_deleted: list[str]
    failures: list[tuple[str, str]]


def execute_tenant_offboarding(
    config: IngestionApiConfig,
    limiter_pool: IngestionDatabasePool,
    *,
    tenant_id: str,
    confirm_tenant_id: str,
    confirmation_phrase: str,
    operator: str,
) -> OffboardingResult:
    """Retires every still-`received` record for `tenant_id`, then
    physically purges every `retired` record (including ones this call
    itself just retired) -- using the *existing*, unmodified,
    purge-claim-safe `cloudops_guard.ingestion_api.lifecycle.
    purge_retired_ingestion` directly against `config` (the exact same
    `IngestionApiConfig` a production entrypoint already builds), so
    every purge-claim safety property Phase 4D already established
    (exclusive acquisition, generation+claim_id identity, exception-safe
    claim release) applies unchanged here -- this module never
    reimplements any part of that logic. `config.metadata_store` must be
    a `PostgresMetadataStore` (the one adapter this module's own
    tenant-inventory query, `list_tenant_records`, is defined on -- not
    part of the provider-neutral `MetadataStore` interface).

    Requires **both** the tenant-identity double-entry (`tenant_id` ==
    `confirm_tenant_id`) **and** the exact, tenant-specific confirmation
    phrase (`required_offboarding_confirmation_phrase(tenant_id)`) -- a
    read-only call (`plan_tenant_offboarding`) never requires either.

    Writes one append-only `operator_audit_events` row per action
    (retire attempt, purge attempt) plus one summary row for the
    offboarding request itself -- before, not after, this function
    returns, so an audit trail exists even if a later step in the same
    call fails.
    """
    _require_matching_tenant_identity(tenant_id, confirm_tenant_id)
    required_phrase = required_offboarding_confirmation_phrase(tenant_id)
    if confirmation_phrase != required_phrase:
        raise OffboardingConfirmationError(
            "confirmation_phrase did not exactly match the required phrase for this "
            "tenant. Refusing to retire or purge anything."
        )

    metadata_store = config.metadata_store
    assert isinstance(metadata_store, PostgresMetadataStore)
    clock = config.clock

    _record_audit_event(
        limiter_pool,
        occurred_at=clock(),
        operator=operator,
        tenant_id=tenant_id,
        action="offboarding_requested",
        ingestion_id=None,
        outcome="confirmed",
    )

    records = metadata_store.list_tenant_records(tenant_id)
    retired: list[str] = []
    already_deleted: list[str] = []
    failures: list[tuple[str, str]] = []

    for record in records:
        if record.status is IngestionStatus.RECEIVED:
            try:
                metadata_store.mark_retired(
                    tenant_id, record.ingestion_id, clock(), RetirementReason.CUSTOMER_REQUESTED
                )
                retired.append(record.ingestion_id)
                outcome = "retired"
            except Exception as exc:  # noqa: BLE001 -- recorded, then re-raised via failures list
                failures.append((record.ingestion_id, "retire_failed"))
                outcome = f"retire_failed:{type(exc).__name__}"
            _record_audit_event(
                limiter_pool,
                occurred_at=clock(),
                operator=operator,
                tenant_id=tenant_id,
                action="retire",
                ingestion_id=record.ingestion_id,
                outcome=outcome,
            )
        elif record.status is IngestionStatus.DELETED:
            already_deleted.append(record.ingestion_id)

    # Re-fetch: some records may have just transitioned from received to
    # retired above, and any pre-existing retired records are eligible too.
    purge_candidates = [
        r.ingestion_id
        for r in metadata_store.list_tenant_records(tenant_id)
        if r.status is IngestionStatus.RETIRED
    ]
    purged: list[str] = []
    for ingestion_id in purge_candidates:
        try:
            result = ingestion_api_lifecycle.purge_retired_ingestion(
                config, tenant_id, ingestion_id, now=clock()
            )
            outcome = "purged" if result is not None else "nothing_to_purge"
            if result is not None:
                purged.append(ingestion_id)
        except Exception as exc:  # noqa: BLE001
            failures.append((ingestion_id, "purge_failed"))
            outcome = f"purge_failed:{type(exc).__name__}"
        _record_audit_event(
            limiter_pool,
            occurred_at=clock(),
            operator=operator,
            tenant_id=tenant_id,
            action="purge",
            ingestion_id=ingestion_id,
            outcome=outcome,
        )

    return OffboardingResult(
        tenant_id=tenant_id,
        retired=retired,
        purged=purged,
        already_deleted=already_deleted,
        failures=failures,
    )
