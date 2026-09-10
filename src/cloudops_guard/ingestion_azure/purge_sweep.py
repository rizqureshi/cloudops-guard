"""The real, bounded, concurrency-safe physical-purge sweep
(correction-pass item 7). Replaces the acknowledged `purge-sweep`
placeholder that previously just ran `migration-status`.

Deliberately thin: this module contributes only two things that did not
already exist -- (1) the candidate-selection query
(`PostgresMetadataStore.list_retired_for_purge_sweep`, which this module
does not itself implement, only calls) and (2) the per-record iteration/
batching/failure-isolation loop below. Every actual purge-claim safety
guarantee (exclusive acquisition, generation+claim_id comparison,
exception-safe release, blob-deletion-before-metadata-commit ordering,
idempotent no-op on an already-`deleted`/unknown/foreign-tenant/
tombstone-expired key) is inherited **unchanged** from
`cloudops_guard.ingestion_api.lifecycle.purge_retired_ingestion` -- this
module never reimplements, second-guesses, or duplicates any of that
logic, so its own concurrency/idempotency behavior is only ever as
correct as that already-hardened function's own three-pass-verified
guarantees.

**No deletion of received/live records** is structural, not a runtime
check this module performs: `list_retired_for_purge_sweep` selects only
`status = 'retired'` rows, and `purge_retired_ingestion`'s own
`begin_purge` independently raises `ValueError` for a still-`received`
record as defense in depth -- a `received` record can never reach this
sweep's per-record purge call at all.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from cloudops_guard.ingestion_api import lifecycle as ingestion_api_lifecycle
from cloudops_guard.ingestion_api.config import IngestionApiConfig

from .postgres_metadata_store import PostgresMetadataStore

#: Bounds a single sweep run's work -- "Bounded batches" (correction-pass
#: item 7). A caller wanting to purge more than this in one invocation
#: simply calls `run_purge_sweep` again (safe: each call re-queries
#: current eligibility, so a previous run's own purges are never
#: revisited, and a record another concurrent run already claimed is
#: excluded from the very next query too).
DEFAULT_PURGE_SWEEP_BATCH_SIZE = 100


@dataclasses.dataclass(frozen=True, slots=True)
class PurgeSweepFailure:
    tenant_id: str
    ingestion_id: str
    error_type: str


@dataclasses.dataclass(frozen=True, slots=True)
class PurgeSweepResult:
    #: `(tenant_id, ingestion_id)` pairs actually, physically purged by
    #: *this* call.
    purged: tuple[tuple[str, str], ...]
    #: Candidates this call selected but which turned out to already be
    #: claimed/purged/reused by the time `purge_retired_ingestion` ran --
    #: an expected, benign outcome of concurrent sweeps racing the same
    #: candidate list, never an error.
    already_unavailable: tuple[tuple[str, str], ...]
    #: Per-record purge failures (a real blob/database exception),
    #: isolated so one failing record never aborts the rest of the
    #: batch. `error_type` is the exception's class name only -- never
    #: its message, which could otherwise leak infrastructure detail
    #: into operator-facing tooling output.
    failures: tuple[PurgeSweepFailure, ...]
    #: How many retired candidates this call actually examined --
    #: distinct from `len(purged)`, since a candidate can also land in
    #: `already_unavailable` or `failures`.
    candidates_considered: int


def run_purge_sweep(
    config: IngestionApiConfig,
    *,
    batch_size: int = DEFAULT_PURGE_SWEEP_BATCH_SIZE,
    now: dt.datetime | None = None,
) -> PurgeSweepResult:
    """Physically purges up to `batch_size` currently-eligible `retired`
    records. Safe to call repeatedly, and safe to call concurrently with
    itself (from a second sweep run, an operator's manual `purge`
    invocation, or a customer-triggered path) -- every actual purge goes
    through `purge_retired_ingestion`'s own exclusive purge-claim
    protocol, so two callers racing the same candidate never both
    physically delete the same blob, and a candidate already claimed
    elsewhere is simply skipped here (recorded in `already_unavailable`,
    not `failures`).

    Requires a `PostgresMetadataStore` (the purge-sweep listing query,
    `list_retired_for_purge_sweep`, is a Postgres-specific extension, not
    part of the portable `MetadataStore` interface -- see that method's
    own docstring).
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive.")
    if not isinstance(config.metadata_store, PostgresMetadataStore):
        raise TypeError(
            "run_purge_sweep requires a PostgresMetadataStore "
            f"(got {type(config.metadata_store).__name__})."
        )

    candidates = config.metadata_store.list_retired_for_purge_sweep(limit=batch_size)

    purged: list[tuple[str, str]] = []
    already_unavailable: list[tuple[str, str]] = []
    failures: list[PurgeSweepFailure] = []

    for candidate in candidates:
        key = (candidate.tenant_id, candidate.ingestion_id)
        try:
            result = ingestion_api_lifecycle.purge_retired_ingestion(
                config, candidate.tenant_id, candidate.ingestion_id, now=now
            )
        except Exception as exc:
            failures.append(
                PurgeSweepFailure(
                    tenant_id=candidate.tenant_id,
                    ingestion_id=candidate.ingestion_id,
                    error_type=type(exc).__name__,
                )
            )
            continue
        if result is None:
            # Already claimed by a concurrent caller, already purged, or
            # its key was reused/tombstone-expired between the listing
            # query above and this call -- benign, not a failure.
            already_unavailable.append(key)
        else:
            purged.append(key)

    return PurgeSweepResult(
        purged=tuple(purged),
        already_unavailable=tuple(already_unavailable),
        failures=tuple(failures),
        candidates_considered=len(candidates),
    )
