-- Phase 4G-A: MetadataStore schema (docs/milestones/v0.4.0-ingestion-api.md
-- §H). Every table is scoped by tenant_id in its primary key or a leading
-- index column -- there is no query path in this package's adapters that
-- omits a tenant_id predicate.
--
-- generation/claim_id use shared sequences (never per-row identity
-- columns) because their only contract is "monotonically increasing,
-- globally unique" (models.PurgeClaim's own docstring) -- a global
-- sequence is the simplest correct implementation, exactly mirroring
-- InMemoryMetadataStore's own `_next_generation`/`_next_claim_id`
-- counters.

CREATE SEQUENCE ingestion_generation_seq;
CREATE SEQUENCE ingestion_claim_id_seq;

CREATE TABLE ingestion_records (
    tenant_id text NOT NULL,
    ingestion_id text NOT NULL,
    report_fingerprint text NOT NULL,
    received_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('received', 'retired', 'deleted')),
    reason text CHECK (reason IN ('customer_requested', 'retention_expired')),
    retired_at timestamptz,
    deleted_at timestamptz,
    generation bigint NOT NULL,
    PRIMARY KEY (tenant_id, ingestion_id),
    -- Mirrors IngestionRecord's own model_validator invariants -- a
    -- corrupt row can never be written in the first place, defense in
    -- depth alongside the Python-level validation every candidate
    -- already goes through before this INSERT/UPDATE is issued.
    CONSTRAINT status_fields_consistent CHECK (
        (status = 'received' AND reason IS NULL AND retired_at IS NULL AND deleted_at IS NULL)
        OR (status = 'retired' AND reason IS NOT NULL AND retired_at IS NOT NULL AND deleted_at IS NULL)
        OR (status = 'deleted' AND reason IS NOT NULL AND retired_at IS NOT NULL AND deleted_at IS NOT NULL)
    ),
    CONSTRAINT retired_not_before_received CHECK (retired_at IS NULL OR retired_at >= received_at),
    CONSTRAINT deleted_not_before_retired CHECK (deleted_at IS NULL OR deleted_at >= retired_at)
);

-- THE atomic-dedup constraint: at most one 'received' row may exist per
-- (tenant_id, report_fingerprint) -- enforced by PostgreSQL itself, not
-- merely application logic (interfaces.py's own explicit requirement).
CREATE UNIQUE INDEX ux_ingestion_active_fingerprint
    ON ingestion_records (tenant_id, report_fingerprint)
    WHERE status = 'received';

-- Supports list_expired_for_retention_sweep's "received records whose
-- received_at predates the cutoff" query without a full table scan.
CREATE INDEX ix_ingestion_received_for_sweep
    ON ingestion_records (received_at)
    WHERE status = 'received';

CREATE TABLE idempotency_bindings (
    tenant_id text NOT NULL,
    idempotency_key text NOT NULL,
    ingestion_id text NOT NULL,
    received_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, idempotency_key)
);

CREATE TABLE tombstones (
    tenant_id text NOT NULL,
    ingestion_id text NOT NULL,
    reason text NOT NULL CHECK (reason IN ('customer_requested', 'retention_expired')),
    retired_at timestamptz NOT NULL,
    deleted_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, ingestion_id),
    CONSTRAINT deleted_not_before_retired CHECK (deleted_at >= retired_at)
);

-- The exclusive, in-progress purge claim for a given key -- at most one
-- row per (tenant_id, ingestion_id), mirroring
-- InMemoryMetadataStore._active_purge_claims exactly. The already-
-- validated eventual 'deleted' candidate and its tombstone are stored
-- alongside the claim (purged_* columns) so finalize_purge commits
-- exactly what begin_purge already validated, never reconstructing or
-- re-validating anything at finalize time -- the same design the
-- in-memory reference implementation uses.
CREATE TABLE purge_claims (
    tenant_id text NOT NULL,
    ingestion_id text NOT NULL,
    generation bigint NOT NULL,
    claim_id bigint NOT NULL,
    purged_reason text NOT NULL CHECK (purged_reason IN ('customer_requested', 'retention_expired')),
    purged_retired_at timestamptz NOT NULL,
    purged_deleted_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, ingestion_id)
);
