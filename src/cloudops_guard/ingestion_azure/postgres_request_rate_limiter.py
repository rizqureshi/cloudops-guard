"""A real, persistent, concurrency-safe PostgreSQL `RequestRateLimiter`
(Phase 4D's interface, Phase 4G-A's implementation). `check_and_record_request`
is one atomic SQL statement (task 5: "one atomic database operation") --
a fixed-window counter, using the database's own clock, never this
process's local time.

Each of §F/§H's two named uses (the unauthenticated capabilities
endpoint, source-scoped; Layer 3's per-authenticated-token budget) is a
*separate instance*, configured with its own `threshold`/`window_seconds`
-- exactly mirroring how `InMemoryRequestRateLimiter(threshold=...)` is
already constructed once per use in every existing test.
"""

from __future__ import annotations

import datetime as dt

import psycopg

from cloudops_guard.ingestion.interfaces import RequestRateLimiter

from .errors import DatabaseUnavailableError
from .pool import IngestionDatabasePool
from .scope_key_hashing import hash_scope_key

_ATOMIC_CHECK_AND_RECORD_SQL = """
WITH bucket AS (
    SELECT to_timestamp(floor(extract(epoch FROM now()) / %(window_seconds)s) * %(window_seconds)s)
        AS window_start
)
INSERT INTO request_rate_counters (scope_key_hash, window_start, request_count)
SELECT %(scope_key_hash)s, bucket.window_start, 1 FROM bucket
ON CONFLICT (scope_key_hash, window_start) DO UPDATE
    SET request_count = request_rate_counters.request_count + 1
    WHERE request_rate_counters.request_count < %(threshold)s
RETURNING request_count
"""


class PostgresRequestRateLimiter(RequestRateLimiter):
    def __init__(
        self,
        pool: IngestionDatabasePool,
        *,
        threshold: int,
        window: dt.timedelta,
        hmac_key: bytes,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be at least 1.")
        if window <= dt.timedelta(0):
            raise ValueError("window must be a positive duration.")
        self._pool = pool
        self._threshold = threshold
        self._window_seconds = window.total_seconds()
        self._hmac_key = hmac_key

    def check_and_record_request(self, scope_key: str) -> bool:
        hashed = hash_scope_key(scope_key, hmac_key=self._hmac_key)
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        _ATOMIC_CHECK_AND_RECORD_SQL,
                        {
                            "scope_key_hash": hashed,
                            "window_seconds": self._window_seconds,
                            "threshold": self._threshold,
                        },
                    )
                    row = cur.fetchone()
                    return row is not None
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def cleanup_expired(self, *, max_age: dt.timedelta) -> int:
        """Bounded cleanup of counter rows for windows older than
        `max_age` (task 5/11). Safe to call repeatedly.
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM request_rate_counters "
                        "WHERE window_start < (SELECT now()) - %s",
                        (max_age,),
                    )
                    return cur.rowcount
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc
