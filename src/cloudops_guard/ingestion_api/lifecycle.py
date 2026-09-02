"""Explicitly-invoked retention-sweep and physical-purge operations
(`docs/milestones/v0.4.0-ingestion-api.md` §E.4/§C, task 12) -- callable
directly by a test, or by a future phase's own scheduling mechanism
(cron, queue consumer, etc., deliberately unspecified here). Starts no
thread, scheduler, or background task itself.
"""

from __future__ import annotations

import datetime as dt

from cloudops_guard.ingestion import storage_keys
from cloudops_guard.ingestion.models import IngestionRecord, RetirementReason

from .config import IngestionApiConfig


def run_retention_sweep(
    config: IngestionApiConfig, *, now: dt.datetime | None = None
) -> list[IngestionRecord]:
    """Retires every `received` record whose age exceeds
    `config.retention_period`, with `reason=RETENTION_EXPIRED`. Returns
    the records actually retired *by this call* (empty if none were due).

    Safe to call repeatedly or concurrently with itself or with a
    customer `DELETE`: `MetadataStore.mark_retired` is itself idempotent
    (§H), so a record another caller already retired first is simply
    returned unchanged here, not double-retired or overwritten -- this
    function relies on that idempotency rather than re-implementing any
    locking of its own.
    """
    at = now if now is not None else config.clock()
    cutoff = at - config.retention_period
    retired: list[IngestionRecord] = []
    for candidate in config.metadata_store.list_expired_for_retention_sweep(cutoff):
        result = config.metadata_store.mark_retired(
            candidate.tenant_id, candidate.ingestion_id, at, RetirementReason.RETENTION_EXPIRED
        )
        # mark_retired returns the record UNCHANGED (its true original
        # retired_at) if some other caller already retired it first --
        # comparing against this call's own `at` is what distinguishes
        # "this call actually performed the retirement" from "it was
        # already done."
        if result is not None and result.retired_at == at:
            retired.append(result)
    return retired


def purge_retired_ingestion(
    config: IngestionApiConfig,
    tenant_id: str,
    ingestion_id: str,
    *,
    now: dt.datetime | None = None,
) -> IngestionRecord | None:
    """Physically purges one retired record's report bytes, then marks
    metadata/tombstone deleted -- **blob deletion first, always** among
    the two *mutating* steps: a purge that fails between them must never
    have already claimed `deleted` while report bytes still exist.

    **Phase 4D second correction pass, item 1**: the first correction
    pass's own `get_any_status`-based eligibility check closed the
    still-`received` case, but remained a genuine check-*then*-act
    operation. Fixed by never caching an eligibility read across the
    blob deletion: `MetadataStore.begin_purge` is the single atomic
    eligibility check, always called immediately before
    `ReportBlobStore.delete` (never earlier), and its returned
    `PurgeClaim` is re-verified, atomically, by
    `MetadataStore.finalize_purge` right before the metadata `deleted`
    transition.

    **Purge-claim hardening pass**: closes three further gaps the
    second correction pass's own generation-only claim design still
    left open (see `PurgeClaim`'s, `begin_purge`'s, and `mark_purged`'s
    own updated docstrings in `interfaces.py`/`models.py` for the full
    detail of each):

    1. A claim is now identified by `(generation, claim_id)` together,
       never `generation` alone -- closing an ABA gap where releasing an
       old, already-superseded claim could cancel a different, currently
       active claim for the same unchanged generation, and where a
       released claim could still successfully finalize.
    2. `mark_purged` -- preserved, unused by this function, but still
       part of the same `MetadataStore` -- now refuses to run while an
       exact claim is active, so it can no longer bypass this function's
       own claim protocol out from under a claim holder.
    3. **This function's own exception-safety**: `at`, the proposed
       deletion timestamp, is now computed *before* any claim is ever
       requested (`config.clock()` raising, or `now` being invalid,
       therefore can never leave a claim dangling -- there is nothing
       yet to release). `begin_purge` itself now validates `at` and the
       complete eventual `deleted` candidate atomically with claim
       acquisition, so a validation failure (a naive timestamp, or `at`
       preceding `retired_at`) also happens *before* any claim is
       granted -- never mid-purge. From claim acquisition onward, every
       remaining step (`ReportBlobStore.delete`, `finalize_purge`) is
       wrapped so that *any* exception releases exactly this call's own
       claim, then re-raises -- both blob-deletion failure (existing
       behavior) and a `finalize_purge` failure (defensive: expected to
       be unreachable in the reference implementation now that
       `begin_purge` pre-validates everything, but a real backing
       store's own commit step could still fail) leave the claim
       released, so a later retry can re-acquire and try again rather
       than finding the record permanently unclaimable.

    - **Unknown, foreign-tenant, tombstone-expired, or already
      `deleted`** (`begin_purge` returns `None` without raising):
      returns `None` immediately -- **no `ReportBlobStore` call at all**
      for the already-`deleted` case, which is exactly what makes a
      repeated, idempotent purge safe even after this exact key has
      since been reused by a different identity.
    - **Still `received`**, **an invalid `at`**, or **`at` preceding
      `retired_at`**: `begin_purge` itself raises (`ValueError`), before
      any store mutation and before any claim is ever granted.
    - **`retired`**: `begin_purge` returns a `PurgeClaim`. The blob is
      then physically deleted (itself safe to call repeatedly, never
      raises for a missing key), and `finalize_purge` is called with
      that exact claim. If `finalize_purge` reports the claim as stale
      (`None`), this function returns `None` too -- the metadata layer
      was never corrupted into claiming the wrong acquisition `deleted`,
      even in the rare case where the physical blob deletion that just
      ran may already have targeted a since-superseded identity.
    """
    at = now if now is not None else config.clock()
    claim = config.metadata_store.begin_purge(tenant_id, ingestion_id, at)
    if claim is None:
        return None

    storage_key = storage_keys.derive_storage_key(tenant_id, ingestion_id)
    try:
        config.blob_store.delete(storage_key)
    except Exception:
        # Release this exact claim so a later retry -- by this caller or
        # another -- is not permanently blocked by a claim its own
        # holder will never finish. The original exception still
        # propagates unchanged (existing, unchanged "blob deletion first"
        # ordering: metadata is never touched after a blob-deletion
        # failure).
        config.metadata_store.release_purge_claim(claim)
        raise

    try:
        return config.metadata_store.finalize_purge(claim)
    except Exception:
        # Purge-claim hardening pass, item 3: a finalize failure -- never
        # expected to be reachable given begin_purge's own upfront
        # validation in the reference implementation, but a real backing
        # store's own commit step could still fail -- must not leave the
        # record permanently unclaimable either. Releasing this exact
        # claim here is always safe: finalize_purge only ever mutates
        # metadata as its very last, unconditional step, so an exception
        # from it means that commit never happened and this claim is
        # still exactly the one to release.
        config.metadata_store.release_purge_claim(claim)
        raise
