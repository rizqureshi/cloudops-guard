-- Phase 4G-A: append-only audit trail for the operator-only tenant
-- inventory/offboarding mechanism (task 11, docs/pilots/
-- ingestion-pilot-runbook.md §16's own hard-blocker requirement). No
-- UPDATE or DELETE grant is ever expected against this table in
-- production (append-only by convention and by role grant, not by a
-- database-level trigger in this pilot-scale design) -- never contains
-- report content, a token value, or a secret (mirrors this project's
-- existing ingestion-service log allowlist discipline exactly).

CREATE TABLE operator_audit_events (
    id bigserial PRIMARY KEY,
    occurred_at timestamptz NOT NULL,
    operator text NOT NULL,
    tenant_id text NOT NULL,
    action text NOT NULL,
    ingestion_id text,
    outcome text NOT NULL
);

CREATE INDEX ix_operator_audit_events_tenant ON operator_audit_events (tenant_id, occurred_at);
