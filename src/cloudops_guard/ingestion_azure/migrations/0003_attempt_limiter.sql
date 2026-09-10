-- Phase 4G-A: AttemptLimiter schema (docs/milestones/v0.4.0-ingestion-api.md
-- §F, three-layer authentication-abuse protection). Insert-only: each
-- failed attempt is one row; `is_blocked` counts rows within a rolling
-- window, so a failure automatically stops counting once it ages out --
-- no separate "reset" operation is needed for the window itself to
-- expire (task 5's "Authentication-failure windows must expire
-- automatically").
--
-- `scope_key_hash` is an HMAC-SHA256 of the real scope key (which itself
-- embeds a caller-supplied lookup_id or source identifier, e.g. an IP
-- address) -- task 5 explicitly requires source IP addresses never be
-- stored in plaintext as limiter keys. The HMAC key comes from
-- process configuration (eventually Key Vault, Phase 4G-B) and is never
-- itself persisted in this database.

CREATE TABLE attempt_failures (
    id bigserial PRIMARY KEY,
    scope_key_hash text NOT NULL,
    occurred_at timestamptz NOT NULL
);

CREATE INDEX ix_attempt_failures_scope_time ON attempt_failures (scope_key_hash, occurred_at);

-- Supports bounded cleanup of expired rows (task 5, task 11) without a
-- full table scan.
CREATE INDEX ix_attempt_failures_occurred_at ON attempt_failures (occurred_at);
