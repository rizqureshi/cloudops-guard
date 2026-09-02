"""Cross-store safe-write orchestration for `POST /api/v1/reports`
(Phase 4D correction, task 3.2). `interfaces.py`'s own module docstring
explicitly leaves this failure-recovery strategy to Phase 4D:
`MetadataStore` and `ReportBlobStore` are two independent stores with no
shared transaction between them (none is possible in general, once these
interfaces are backed by two genuinely separate production systems).

**The strategy, precisely** (see inline comments below for each step's
exact reasoning): reserve a brand-new blob key via `put_if_absent` before
ever touching metadata -- a `received` status is only ever reported once
*both* writes have durably succeeded, never on the basis of one alone.
- A blob-key collision (this generated `ingestion_id` already has bytes
  under it, from any earlier caller) retries with a fresh ID -- never
  overwrites, never touches bytes this request did not itself just
  reserve.
- A definite, non-ambiguous pre-commit metadata rejection
  (`IngestionIdConflict`, `IdempotencyKeyConflict`) is safe to clean up
  after: this request's own reserved blob was never linked to any
  record, so deleting it is this request's sole responsibility and
  cannot orphan anything.
- Losing the atomic dedup race (`create_or_get_received` returns
  `created=False`) means some *other* record is now the tenant's live
  answer for this content; this request's reserved blob is unused by
  anyone and safe to delete.
- Any *other*, ambiguous exception from `create_or_get_received` is
  deliberately left alone: it may or may not have already committed a
  record referencing this exact blob before raising, and this module
  cannot tell those two cases apart from the exception alone. Deleting
  the blob unconditionally in that case risks orphaning a committed
  record with no content, which is worse than leaving an unreferenced
  blob behind for a later reconciliation process to find -- so this
  request path leaves both stores exactly as they are and re-raises.

**Correction pass, item 6's own explicit re-review of this design**:
confirmed the two cleanup branches above are exhaustive for every
*documented* failure mode `create_or_get_received` can raise
(`interfaces.py`'s own docstrings name exactly these two exception
types as its contract). `InMemoryMetadataStore.create_or_get_received`
additionally raises a plain `ValueError` for four internal precondition
checks (e.g. `new_record.tenant_id != tenant_id`) -- also technically
"before any mutation," but these guard against *this module's own*
candidate-construction bugs, never a legitimate storage-layer outcome a
correctly-written caller could encounter (this function always
constructs `candidate` to exactly match the `tenant_id`/
`report_fingerprint`/`ingestion_id` it passes positionally, with
`status=RECEIVED` always). They are deliberately left in the "ambiguous,
leave the blob alone" bucket alongside every other undocumented
exception: if this module's own logic is broken badly enough to violate
those preconditions, this module cannot trust its own reasoning about
what already happened well enough to safely delete anything either. See
`tests/test_ingestion_api_coordinator.py::TestAmbiguousMetadataExceptionLeavesBlobAlone`
for both the original (generic exception) and this review's added
(`ValueError`-specific) proof.

**Second correction pass, item 3**: the original implementation called
`config.clock()` and constructed the `IngestionRecord` candidate *after*
`put_if_absent` succeeded but *outside* any `try` block guarding the
reservation -- if either raised, `create_or_get_received` was definitely
never called (unlike its own genuinely ambiguous exceptions), yet the
reservation was never cleaned up. This is now closed: both operations are
wrapped in their own `try`/`except Exception` that deletes the owned
reservation and re-raises, entirely separate from, and never widening,
the deliberately-narrower `except IngestionIdConflict`/
`except IdempotencyKeyConflict` handling immediately below it for
`create_or_get_received`'s own two documented pre-commit exceptions. See
`tests/test_ingestion_api_coordinator.py::TestReservationCleanupOnPreMetadataFailure`.
"""

from __future__ import annotations

from typing import Any

from cloudops_guard.ingestion import storage_keys
from cloudops_guard.ingestion.errors import IdempotencyKeyConflict, IngestionIdConflict
from cloudops_guard.ingestion.models import IngestionRecord, IngestionStatus

from .config import IngestionApiConfig
from .errors import INTERNAL_ERROR, ApiError
from .fingerprint import compute_report_fingerprint

#: A generated-ID collision is expected to be astronomically rare for a
#: UUIDv4-derived `ingestion_id` -- this bound exists only to fail
#: deterministically (rather than loop forever) in the pathological case
#: of a badly misbehaving `ingestion_id_generator`, e.g. a test double.
MAX_INGESTION_ID_GENERATION_ATTEMPTS = 5


def create_ingestion(
    *,
    config: IngestionApiConfig,
    tenant_id: str,
    platform: str,
    report_schema_version: int | float,
    report: dict[str, Any],
    report_bytes: bytes,
    idempotency_key: str | None,
) -> tuple[IngestionRecord, bool]:
    """Returns `(record, created)` -- `created=True` only for a genuinely
    new ingestion (the caller maps this to `201` vs `200`). Raises
    `ApiError(INTERNAL_ERROR)` if `MAX_INGESTION_ID_GENERATION_ATTEMPTS`
    is exhausted; re-raises `IdempotencyKeyConflict` for the caller to map
    to `400 invalid_request` (§E's idempotency semantics, step 3).
    """
    report_fingerprint = compute_report_fingerprint(platform, report_schema_version, report)

    for _attempt in range(MAX_INGESTION_ID_GENERATION_ATTEMPTS):
        ingestion_id = config.ingestion_id_generator()
        storage_key = storage_keys.derive_storage_key(tenant_id, ingestion_id)

        # Step 1: reserve this request's own, brand-new blob key. Never
        # plain `put` (which would silently overwrite) -- see
        # `interfaces.ReportBlobStore.put_if_absent`'s own docstring.
        reserved = config.blob_store.put_if_absent(storage_key, report_bytes)
        if not reserved:
            # A genuine collision against bytes already stored under this
            # exact (tenant_id, ingestion_id) -- retry with a freshly
            # generated ID rather than ever touching bytes this request
            # did not itself just reserve.
            continue

        try:
            # Step 1.5 (**second correction pass, item 3**): building the
            # candidate record is *always* strictly before
            # `create_or_get_received` is ever called -- unlike that
            # call's own genuinely ambiguous exceptions (see this
            # module's docstring), a failure here is never ambiguous
            # about whether metadata was touched: it definitely was not.
            # This request's own reservation is therefore always this
            # request's sole responsibility to clean up in that case,
            # covering both `config.clock()` raising outright and a
            # clock value `IngestionRecord`'s own validator rejects
            # (e.g. a naive datetime).
            now = config.clock()
            candidate = IngestionRecord(
                tenant_id=tenant_id,
                ingestion_id=ingestion_id,
                report_fingerprint=report_fingerprint,
                received_at=now,
                status=IngestionStatus.RECEIVED,
            )
        except Exception:
            config.blob_store.delete(storage_key)
            raise

        try:
            record, created = config.metadata_store.create_or_get_received(
                tenant_id,
                report_fingerprint,
                ingestion_id,
                candidate,
                idempotency_key=idempotency_key,
            )
        except IngestionIdConflict:
            # Raised before any store mutation (interfaces.py's own
            # comment on IngestionIdConflict) -- a definite, non-ambiguous
            # pre-commit rejection, safe to treat like a blob-level ID
            # collision: delete this request's own unused reservation and
            # retry with a freshly generated ID.
            config.blob_store.delete(storage_key)
            continue
        except IdempotencyKeyConflict:
            # Also a definite pre-commit rejection (step 1 of §H's
            # algorithm, before step 3's insert) -- this request's own
            # reserved blob was never linked to any record.
            config.blob_store.delete(storage_key)
            raise
        # Any OTHER exception from create_or_get_received is deliberately
        # NOT caught here -- see this module's docstring for why the
        # reserved blob is left alone in that ambiguous case.

        if not created:
            # Lost the atomic dedup race, or a genuine content/key
            # replay: some OTHER record is the tenant's live answer for
            # this fingerprint/key. This request's own reserved blob is
            # unused by anyone and safe to delete.
            config.blob_store.delete(storage_key)
            return record, False

        return record, True

    raise ApiError(INTERNAL_ERROR)
