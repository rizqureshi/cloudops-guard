"""A real, transactionally-atomic PostgreSQL `MetadataStore`
(`docs/milestones/v0.4.0-ingestion-api.md` §H, Phase 4G-A). Reproduces
`InMemoryMetadataStore`'s exact observable behavior (Phase 4B/4D/purge-
claim-hardening-pass semantics) against a real database instead of an
in-process dict -- every method's docstring below states only what
differs about *how* the guarantee is achieved, never a different
guarantee.

**Concurrency strategy**: every mutating method takes a PostgreSQL
session-level advisory *transaction* lock
(`pg_advisory_xact_lock(hashtextextended(tenant_id, 0))`), scoped to the
tenant, held for the method's entire transaction and released
automatically at commit/rollback. This is a stricter, simpler analogue of
`InMemoryMetadataStore`'s own single `threading.Lock` (which serializes
*every* tenant against a single lock) -- serializing only per-tenant
gives strictly *better* concurrency across tenants while providing the
exact same "the whole multi-step algorithm runs under one lock
acquisition" guarantee §H's own interface comment requires. Two
concurrent callers for the *same* tenant genuinely serialize against each
other, which is what makes the atomic-dedup and purge-claim guarantees
provably correct under real, concurrent database transactions (see
`tests/ingestion_azure/test_postgres_metadata_store_concurrency.py`).

Every `IngestionRecord`/`Tombstone` this module constructs goes through
the *exact same* Pydantic model constructors
(`cloudops_guard.ingestion.models`) the in-memory reference
implementation uses -- never a hand-rolled dict presented as a model, and
never `model_copy(update=...)`, which skips validation. This is what
guarantees a Postgres-backed candidate can never violate an invariant the
in-memory reference implementation would have caught.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

import psycopg
from psycopg.rows import dict_row

from cloudops_guard.ingestion.errors import IdempotencyKeyConflict, IngestionIdConflict
from cloudops_guard.ingestion.interfaces import MetadataStore
from cloudops_guard.ingestion.models import (
    IngestionRecord,
    IngestionStatus,
    PurgeClaim,
    RetirementReason,
    Tombstone,
)

from .errors import DatabaseUnavailableError
from .pool import IngestionDatabasePool

#: Mirrors `InMemoryMetadataStore`'s own `DEFAULT_TOMBSTONE_RETENTION`
#: default -- a constructor parameter here too, for the same reason (§C:
#: "configurable per pilot agreement").
DEFAULT_TOMBSTONE_RETENTION = dt.timedelta(days=90)

_RECORD_COLUMNS = (
    "tenant_id, ingestion_id, report_fingerprint, received_at, status, "
    "reason, retired_at, deleted_at"
)
#: Same columns, qualified with the `ir` alias -- required wherever
#: `ingestion_records` is joined against another table that also has a
#: `tenant_id`/`ingestion_id` column (`idempotency_bindings`), since an
#: unqualified reference is otherwise genuinely ambiguous to PostgreSQL,
#: not merely to a human reader.
_RECORD_COLUMNS_IR_QUALIFIED = (
    "ir.tenant_id, ir.ingestion_id, ir.report_fingerprint, ir.received_at, ir.status, "
    "ir.reason, ir.retired_at, ir.deleted_at"
)


def _require_timezone_aware(value: dt.datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


def _row_to_record(row: dict) -> IngestionRecord:
    return IngestionRecord(
        tenant_id=row["tenant_id"],
        ingestion_id=row["ingestion_id"],
        report_fingerprint=row["report_fingerprint"],
        received_at=row["received_at"],
        status=IngestionStatus(row["status"]),
        reason=RetirementReason(row["reason"]) if row["reason"] is not None else None,
        retired_at=row["retired_at"],
        deleted_at=row["deleted_at"],
    )


def _row_to_tombstone(row: dict) -> Tombstone:
    return Tombstone(
        tenant_id=row["tenant_id"],
        ingestion_id=row["ingestion_id"],
        reason=RetirementReason(row["reason"]),
        retired_at=row["retired_at"],
        deleted_at=row["deleted_at"],
    )


class PostgresMetadataStore(MetadataStore):
    def __init__(
        self,
        pool: IngestionDatabasePool,
        *,
        idempotency_key_window: dt.timedelta,
        tombstone_retention: dt.timedelta = DEFAULT_TOMBSTONE_RETENTION,
    ) -> None:
        self._pool = pool
        self._idempotency_key_window = idempotency_key_window
        self._tombstone_retention = tombstone_retention

    # -- internal --------------------------------------------------------

    def _lock_tenant(self, cur: psycopg.Cursor, tenant_id: str) -> None:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (tenant_id,))

    def _expire_tombstone_if_needed(
        self, cur: psycopg.Cursor, tenant_id: str, ingestion_id: str
    ) -> None:
        """Must be called with the tenant's advisory lock already held,
        inside the same transaction as any subsequent read/write for this
        key. Mirrors `InMemoryMetadataStore._expire_tombstone_if_needed`
        exactly: a `deleted` record whose tombstone has aged past
        `tombstone_retention` is removed from *both* tables atomically,
        making its `(tenant_id, ingestion_id)` key available for a
        genuinely new identity.
        """
        cur.execute(
            """
            WITH expired AS (
                DELETE FROM tombstones
                WHERE tenant_id = %s AND ingestion_id = %s
                  AND deleted_at < (SELECT now()) - %s
                RETURNING tenant_id, ingestion_id
            )
            DELETE FROM ingestion_records
            WHERE (tenant_id, ingestion_id) IN (SELECT tenant_id, ingestion_id FROM expired)
            """,
            (tenant_id, ingestion_id, self._tombstone_retention),
        )

    # -- MetadataStore -----------------------------------------------------

    def create_or_get_received(
        self,
        tenant_id: str,
        report_fingerprint: str,
        new_ingestion_id: str,
        new_record: IngestionRecord,
        idempotency_key: str | None = None,
    ) -> tuple[IngestionRecord, bool]:
        if new_record.tenant_id != tenant_id:
            raise ValueError("new_record.tenant_id must match the given tenant_id.")
        if new_record.report_fingerprint != report_fingerprint:
            raise ValueError(
                "new_record.report_fingerprint must match the given report_fingerprint."
            )
        if new_record.ingestion_id != new_ingestion_id:
            raise ValueError("new_record.ingestion_id must match new_ingestion_id.")
        if new_record.status is not IngestionStatus.RECEIVED:
            raise ValueError("new_record must have status=IngestionStatus.RECEIVED.")

        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._lock_tenant(cur, tenant_id)
                    now_row = cur.execute("SELECT now()").fetchone()
                    assert now_row is not None
                    now: dt.datetime = now_row["now"]

                    # Step 1: idempotency-key check, if supplied.
                    if idempotency_key is not None:
                        cur.execute(
                            f"""
                            SELECT ib.received_at AS binding_received_at,
                                   {_RECORD_COLUMNS_IR_QUALIFIED}
                            FROM idempotency_bindings ib
                            LEFT JOIN ingestion_records ir
                                ON ir.tenant_id = ib.tenant_id AND ir.ingestion_id = ib.ingestion_id
                            WHERE ib.tenant_id = %s AND ib.idempotency_key = %s
                            """,
                            (tenant_id, idempotency_key),
                        )
                        binding_row = cur.fetchone()
                        if binding_row is not None and binding_row["status"] is not None:
                            still_active = (
                                binding_row["status"] == IngestionStatus.RECEIVED.value
                                and now
                                <= binding_row["binding_received_at"] + self._idempotency_key_window
                            )
                            if still_active:
                                bound_record = _row_to_record(binding_row)
                                if bound_record.report_fingerprint == report_fingerprint:
                                    return bound_record, False
                                raise IdempotencyKeyConflict(
                                    "idempotency_key is already bound to a different "
                                    "report_fingerprint for this tenant."
                                )
                            # Inactive binding -- fall through to fresh evaluation.

                    # Step 2: content-based dedup against any currently
                    # active 'received' record for this exact fingerprint.
                    cur.execute(
                        f"SELECT {_RECORD_COLUMNS} FROM ingestion_records "
                        "WHERE tenant_id = %s AND report_fingerprint = %s AND status = 'received'",
                        (tenant_id, report_fingerprint),
                    )
                    existing_row = cur.fetchone()
                    if existing_row is not None:
                        existing_record = _row_to_record(existing_row)
                        if idempotency_key is not None:
                            cur.execute(
                                """
                                INSERT INTO idempotency_bindings
                                    (tenant_id, idempotency_key, ingestion_id, received_at)
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (tenant_id, idempotency_key) DO UPDATE
                                    SET ingestion_id = EXCLUDED.ingestion_id,
                                        received_at = EXCLUDED.received_at
                                """,
                                (
                                    tenant_id,
                                    idempotency_key,
                                    existing_record.ingestion_id,
                                    existing_record.received_at,
                                ),
                            )
                        return existing_record, False

                    # Step 3: create -- but first, a genuine ID collision
                    # (live, retired, or still-tombstoned) must be
                    # rejected, never silently overwritten.
                    self._expire_tombstone_if_needed(cur, tenant_id, new_ingestion_id)
                    cur.execute(
                        "SELECT 1 FROM ingestion_records "
                        "WHERE tenant_id = %s AND ingestion_id = %s",
                        (tenant_id, new_ingestion_id),
                    )
                    if cur.fetchone() is not None:
                        raise IngestionIdConflict(
                            f"ingestion_id {new_ingestion_id!r} already identifies a different "
                            f"record for this tenant."
                        )

                    cur.execute(
                        """
                        INSERT INTO ingestion_records
                            (tenant_id, ingestion_id, report_fingerprint, received_at,
                             status, generation)
                        VALUES (%s, %s, %s, %s, 'received', nextval('ingestion_generation_seq'))
                        """,
                        (tenant_id, new_ingestion_id, report_fingerprint, new_record.received_at),
                    )
                    if idempotency_key is not None:
                        # ON CONFLICT DO UPDATE, never a plain INSERT: an
                        # *inactive* (window-elapsed, or bound record
                        # since retired/deleted) binding for this exact
                        # (tenant_id, idempotency_key) may already exist
                        # as a row -- step 1 above deliberately falls
                        # through to here without deleting it, exactly as
                        # `InMemoryMetadataStore`'s own equivalent branch
                        # simply overwrites its dict entry. A plain INSERT
                        # would raise a UNIQUE-violation in that case.
                        cur.execute(
                            """
                            INSERT INTO idempotency_bindings
                                (tenant_id, idempotency_key, ingestion_id, received_at)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (tenant_id, idempotency_key) DO UPDATE
                                SET ingestion_id = EXCLUDED.ingestion_id,
                                    received_at = EXCLUDED.received_at
                            """,
                            (tenant_id, idempotency_key, new_ingestion_id, new_record.received_at),
                        )
                    return new_record, True
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def get(self, tenant_id: str, ingestion_id: str) -> IngestionRecord | None:
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._lock_tenant(cur, tenant_id)
                    self._expire_tombstone_if_needed(cur, tenant_id, ingestion_id)
                    cur.execute(
                        f"SELECT {_RECORD_COLUMNS} FROM ingestion_records "
                        "WHERE tenant_id = %s AND ingestion_id = %s AND status = 'received'",
                        (tenant_id, ingestion_id),
                    )
                    row = cur.fetchone()
                    return _row_to_record(row) if row is not None else None
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def get_any_status(self, tenant_id: str, ingestion_id: str) -> IngestionRecord | None:
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._lock_tenant(cur, tenant_id)
                    self._expire_tombstone_if_needed(cur, tenant_id, ingestion_id)
                    cur.execute(
                        f"SELECT {_RECORD_COLUMNS} FROM ingestion_records "
                        "WHERE tenant_id = %s AND ingestion_id = %s",
                        (tenant_id, ingestion_id),
                    )
                    row = cur.fetchone()
                    return _row_to_record(row) if row is not None else None
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def mark_retired(
        self, tenant_id: str, ingestion_id: str, at: dt.datetime, reason: RetirementReason
    ) -> IngestionRecord | None:
        _require_timezone_aware(at, "at")
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._lock_tenant(cur, tenant_id)
                    self._expire_tombstone_if_needed(cur, tenant_id, ingestion_id)
                    cur.execute(
                        f"""
                        UPDATE ingestion_records
                        SET status = 'retired', reason = %s, retired_at = %s
                        WHERE tenant_id = %s AND ingestion_id = %s AND status = 'received'
                        RETURNING {_RECORD_COLUMNS}
                        """,
                        (reason.value, at, tenant_id, ingestion_id),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        return _row_to_record(row)
                    # Not updated: either unknown, or already retired/deleted
                    # (idempotent no-op) -- either way, return current state.
                    cur.execute(
                        f"SELECT {_RECORD_COLUMNS} FROM ingestion_records "
                        "WHERE tenant_id = %s AND ingestion_id = %s",
                        (tenant_id, ingestion_id),
                    )
                    existing = cur.fetchone()
                    return _row_to_record(existing) if existing is not None else None
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def mark_purged(
        self, tenant_id: str, ingestion_id: str, at: dt.datetime
    ) -> IngestionRecord | None:
        _require_timezone_aware(at, "at")
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._lock_tenant(cur, tenant_id)
                    self._expire_tombstone_if_needed(cur, tenant_id, ingestion_id)
                    cur.execute(
                        f"SELECT {_RECORD_COLUMNS} FROM ingestion_records "
                        "WHERE tenant_id = %s AND ingestion_id = %s",
                        (tenant_id, ingestion_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return None
                    record = _row_to_record(row)
                    if record.status is IngestionStatus.DELETED:
                        return record  # idempotent
                    if record.status is not IngestionStatus.RETIRED:
                        raise ValueError("mark_purged requires an already-retired record.")

                    cur.execute(
                        "SELECT 1 FROM purge_claims WHERE tenant_id = %s AND ingestion_id = %s",
                        (tenant_id, ingestion_id),
                    )
                    if cur.fetchone() is not None:
                        raise ValueError(
                            "mark_purged cannot run while an exclusive purge claim is "
                            "active for this record; use begin_purge/finalize_purge instead."
                        )

                    # Validate BOTH candidates before committing either
                    # mutation -- identical ordering to the in-memory
                    # reference implementation.
                    purged_record = IngestionRecord(
                        tenant_id=record.tenant_id,
                        ingestion_id=record.ingestion_id,
                        report_fingerprint=record.report_fingerprint,
                        received_at=record.received_at,
                        status=IngestionStatus.DELETED,
                        reason=record.reason,
                        retired_at=record.retired_at,
                        deleted_at=at,
                    )
                    tombstone = Tombstone(
                        tenant_id=tenant_id,
                        ingestion_id=ingestion_id,
                        reason=purged_record.reason,  # type: ignore[arg-type]
                        retired_at=purged_record.retired_at,  # type: ignore[arg-type]
                        deleted_at=at,
                    )

                    cur.execute(
                        "UPDATE ingestion_records SET status = 'deleted', deleted_at = %s "
                        "WHERE tenant_id = %s AND ingestion_id = %s",
                        (at, tenant_id, ingestion_id),
                    )
                    cur.execute(
                        "INSERT INTO tombstones "
                        "(tenant_id, ingestion_id, reason, retired_at, deleted_at) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            tenant_id,
                            ingestion_id,
                            tombstone.reason.value,
                            tombstone.retired_at,
                            at,
                        ),
                    )
                    return purged_record
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def begin_purge(self, tenant_id: str, ingestion_id: str, at: dt.datetime) -> PurgeClaim | None:
        _require_timezone_aware(at, "at")
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._lock_tenant(cur, tenant_id)
                    self._expire_tombstone_if_needed(cur, tenant_id, ingestion_id)
                    cur.execute(
                        f"SELECT {_RECORD_COLUMNS}, generation FROM ingestion_records "
                        "WHERE tenant_id = %s AND ingestion_id = %s",
                        (tenant_id, ingestion_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return None
                    record = _row_to_record(row)
                    if record.status is IngestionStatus.RECEIVED:
                        raise ValueError("begin_purge requires an already-retired record.")
                    if record.status is IngestionStatus.DELETED:
                        return None

                    cur.execute(
                        "SELECT 1 FROM purge_claims WHERE tenant_id = %s AND ingestion_id = %s",
                        (tenant_id, ingestion_id),
                    )
                    if cur.fetchone() is not None:
                        return None  # another claim is already active -- exclusive

                    # Validate the complete eventual candidates NOW,
                    # atomically with claim acquisition -- a validation
                    # failure raises before any claim row is ever
                    # inserted.
                    purged_record = IngestionRecord(
                        tenant_id=record.tenant_id,
                        ingestion_id=record.ingestion_id,
                        report_fingerprint=record.report_fingerprint,
                        received_at=record.received_at,
                        status=IngestionStatus.DELETED,
                        reason=record.reason,
                        retired_at=record.retired_at,
                        deleted_at=at,
                    )
                    Tombstone(
                        tenant_id=tenant_id,
                        ingestion_id=ingestion_id,
                        reason=purged_record.reason,  # type: ignore[arg-type]
                        retired_at=purged_record.retired_at,  # type: ignore[arg-type]
                        deleted_at=at,
                    )

                    generation = row["generation"]
                    claim_id_row = cur.execute(
                        "SELECT nextval('ingestion_claim_id_seq')"
                    ).fetchone()
                    assert claim_id_row is not None
                    claim_id = claim_id_row["nextval"]

                    cur.execute(
                        """
                        INSERT INTO purge_claims
                            (tenant_id, ingestion_id, generation, claim_id,
                             purged_reason, purged_retired_at, purged_deleted_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            tenant_id,
                            ingestion_id,
                            generation,
                            claim_id,
                            purged_record.reason.value,  # type: ignore[union-attr]
                            purged_record.retired_at,
                            at,
                        ),
                    )
                    return PurgeClaim(
                        tenant_id=tenant_id,
                        ingestion_id=ingestion_id,
                        generation=generation,
                        claim_id=claim_id,
                    )
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def release_purge_claim(self, claim: PurgeClaim) -> None:
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    self._lock_tenant(cur, claim.tenant_id)
                    cur.execute(
                        "DELETE FROM purge_claims "
                        "WHERE tenant_id = %s AND ingestion_id = %s "
                        "AND generation = %s AND claim_id = %s",
                        (claim.tenant_id, claim.ingestion_id, claim.generation, claim.claim_id),
                    )
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def finalize_purge(self, claim: PurgeClaim) -> IngestionRecord | None:
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._lock_tenant(cur, claim.tenant_id)
                    self._expire_tombstone_if_needed(cur, claim.tenant_id, claim.ingestion_id)
                    cur.execute(
                        """
                        SELECT generation, claim_id, purged_reason,
                               purged_retired_at, purged_deleted_at
                        FROM purge_claims
                        WHERE tenant_id = %s AND ingestion_id = %s
                        """,
                        (claim.tenant_id, claim.ingestion_id),
                    )
                    active = cur.fetchone()
                    if (
                        active is None
                        or active["generation"] != claim.generation
                        or active["claim_id"] != claim.claim_id
                    ):
                        # Not the exact currently active claim -- no
                        # mutation, and no other active claim is touched.
                        return None

                    cur.execute(
                        f"""
                        UPDATE ingestion_records
                        SET status = 'deleted', deleted_at = %s
                        WHERE tenant_id = %s AND ingestion_id = %s
                        RETURNING {_RECORD_COLUMNS}
                        """,
                        (active["purged_deleted_at"], claim.tenant_id, claim.ingestion_id),
                    )
                    updated = cur.fetchone()
                    assert updated is not None
                    cur.execute(
                        "INSERT INTO tombstones "
                        "(tenant_id, ingestion_id, reason, retired_at, deleted_at) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            claim.tenant_id,
                            claim.ingestion_id,
                            active["purged_reason"],
                            active["purged_retired_at"],
                            active["purged_deleted_at"],
                        ),
                    )
                    cur.execute(
                        "DELETE FROM purge_claims WHERE tenant_id = %s AND ingestion_id = %s",
                        (claim.tenant_id, claim.ingestion_id),
                    )
                    return _row_to_record(updated)
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def get_tombstone(self, tenant_id: str, ingestion_id: str) -> Tombstone | None:
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._lock_tenant(cur, tenant_id)
                    self._expire_tombstone_if_needed(cur, tenant_id, ingestion_id)
                    cur.execute(
                        "SELECT tenant_id, ingestion_id, reason, retired_at, deleted_at "
                        "FROM tombstones WHERE tenant_id = %s AND ingestion_id = %s",
                        (tenant_id, ingestion_id),
                    )
                    row = cur.fetchone()
                    return _row_to_tombstone(row) if row is not None else None
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def list_tenant_records(self, tenant_id: str) -> list[IngestionRecord]:
        """**Not** part of the `MetadataStore` interface -- the operator-
        only tenant-inventory primitive (`inventory.py`, task 11) that
        the public ingestion API deliberately has no equivalent of. Unlike
        every other read method in this class, this is **not** scoped to
        a single `ingestion_id` -- it returns every record for `tenant_id`
        regardless of status (received, retired, or still-tombstoned),
        which is exactly what lets an operator discover a record the
        customer's own retained ID list never mentioned (`docs/pilots/
        ingestion-pilot-runbook.md` §16's own "never rely solely on
        customer-retained ingestion_id`s" requirement). Never returns
        another tenant's records: every row is filtered by an exact
        `tenant_id` equality predicate, never a prefix/pattern match.
        """
        if not tenant_id:
            raise ValueError("tenant_id must not be empty.")
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        f"SELECT {_RECORD_COLUMNS} FROM ingestion_records "
                        "WHERE tenant_id = %s ORDER BY received_at",
                        (tenant_id,),
                    )
                    return [_row_to_record(row) for row in cur.fetchall()]
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def list_expired_for_retention_sweep(
        self, older_than: dt.datetime
    ) -> Iterable[IngestionRecord]:
        _require_timezone_aware(older_than, "older_than")
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        f"SELECT {_RECORD_COLUMNS} FROM ingestion_records "
                        "WHERE status = 'received' AND received_at < %s "
                        "ORDER BY received_at, tenant_id, ingestion_id",
                        (older_than,),
                    )
                    return [_row_to_record(row) for row in cur.fetchall()]
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def list_retired_for_purge_sweep(self, *, limit: int) -> list[IngestionRecord]:
        """**Not** part of the `MetadataStore` interface -- the
        purge-sweep-only listing primitive (correction-pass item 7) that
        finds `retired` records eligible for the *real* purge sweep to
        attempt. Deliberately **not** filtered by any minimum age since
        `retired_at`: unlike retention (which defines *when* a `received`
        record first becomes eligible for retirement), nothing in this
        project's lifecycle model requires -- or benefits from -- delaying
        physical purge once a record is already retired; the "bounded
        window (proposed: 30 days)" §C describes is an upper bound the
        sweep's own recurring schedule satisfies trivially, not a
        minimum grace period this query must enforce.

        Excludes any record with a currently-active purge claim (a
        `NOT EXISTS` against `purge_claims`) -- purely a scheduling
        optimization, not a safety requirement: `begin_purge` itself
        would safely return `None` for a claimed record regardless, but
        skipping it here avoids every sweep run wasting a batch slot on
        a record another in-flight caller is already handling. Ordered
        oldest-`retired_at`-first, tie-broken by `(tenant_id,
        ingestion_id)` for a fully deterministic, fair sweep order across
        repeated calls -- never returns more than `limit` records, which
        is what makes the caller's own batch bounded.
        """
        if limit <= 0:
            raise ValueError("limit must be positive.")
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        f"SELECT {_RECORD_COLUMNS} FROM ingestion_records ir "
                        "WHERE status = 'retired' "
                        "AND NOT EXISTS ("
                        "  SELECT 1 FROM purge_claims pc "
                        "  WHERE pc.tenant_id = ir.tenant_id AND pc.ingestion_id = ir.ingestion_id"
                        ") "
                        "ORDER BY retired_at, tenant_id, ingestion_id "
                        "LIMIT %s",
                        (limit,),
                    )
                    return [_row_to_record(row) for row in cur.fetchall()]
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc
