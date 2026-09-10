-- Phase 4G-A: RequestRateLimiter schema -- a fixed-window counter per
-- (scope_key_hash, window_start). `check_and_record_request`'s atomic
-- check-and-increment (task 5's "one atomic database operation"
-- requirement) is implemented as a single
-- `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE ... RETURNING`
-- statement against this table -- see request_rate_limiter.py.

CREATE TABLE request_rate_counters (
    scope_key_hash text NOT NULL,
    window_start timestamptz NOT NULL,
    request_count integer NOT NULL DEFAULT 0,
    PRIMARY KEY (scope_key_hash, window_start)
);

-- Supports bounded cleanup of past windows (task 5, task 11).
CREATE INDEX ix_request_rate_counters_window_start ON request_rate_counters (window_start);
