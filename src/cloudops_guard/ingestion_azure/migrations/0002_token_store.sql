-- Phase 4G-A: TokenStore schema (docs/milestones/v0.4.0-ingestion-api.md
-- §F). lookup_id is the genuine, plaintext, indexed primary key -- never
-- itself secret (§F). secret_hash holds only an Argon2id hash, never a
-- recoverable secret; nothing in this schema or its adapter ever stores a
-- plaintext token component.

CREATE TABLE tokens (
    lookup_id text PRIMARY KEY,
    secret_hash text NOT NULL,
    tenant_id text NOT NULL,
    scopes text[] NOT NULL CHECK (cardinality(scopes) > 0),
    revoked boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL
);

-- Supports the operator-only tenant inventory (inventory.py) without a
-- full table scan.
CREATE INDEX ix_tokens_tenant ON tokens (tenant_id);
