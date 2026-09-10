"""A real, expiring, persistent PostgreSQL `AttemptLimiter`
(`docs/milestones/v0.4.0-ingestion-api.md` §F, Phase 4G-A). Unlike
`InMemoryAttemptLimiter` (a fixed count with no time dimension at all,
deliberately, for deterministic reference-implementation testing), this
adapter implements the *actual* production behavior §F's own language
describes -- "a cheap, fast-path counter of **recent** failed attempts"
-- as a genuine rolling time window: a failure recorded now stops
counting once it ages past `window`, with no separate reset operation
required (task 5: "Authentication-failure windows must expire
automatically").

Each of §F's three named layers (Layer 1 per-`lookup_id`, Layer 2
per-source) is a *separate instance* of this class, configured with its
own `threshold`/`window` -- exactly mirroring how
`InMemoryAttemptLimiter(threshold=...)` is already constructed once per
layer in every existing test and in a future `IngestionApiConfig`.
"""

from __future__ import annotations

import datetime as dt

import psycopg

from cloudops_guard.ingestion.interfaces import AttemptLimiter

from .errors import DatabaseUnavailableError
from .pool import IngestionDatabasePool
from .scope_key_hashing import hash_scope_key


class PostgresAttemptLimiter(AttemptLimiter):
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
        self._window = window
        self._hmac_key = hmac_key

    def record_failure(self, scope_key: str) -> None:
        hashed = hash_scope_key(scope_key, hmac_key=self._hmac_key)
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO attempt_failures (scope_key_hash, occurred_at) "
                        "VALUES (%s, (SELECT now()))",
                        (hashed,),
                    )
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def is_blocked(self, scope_key: str) -> bool:
        hashed = hash_scope_key(scope_key, hmac_key=self._hmac_key)
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    # Uses the database's own clock (task 5: "use database
                    # time or another explicitly tested authoritative
                    # clock") -- never this process's local time, so
                    # multiple application replicas never disagree due to
                    # their own clock skew.
                    cur.execute(
                        "SELECT count(*) FROM attempt_failures "
                        "WHERE scope_key_hash = %s AND occurred_at > (SELECT now()) - %s",
                        (hashed, self._window),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    return row[0] >= self._threshold
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def cleanup_expired(self, *, max_age: dt.timedelta) -> int:
        """Bounded cleanup of rows older than `max_age` (task 5/11 --
        "Expired rows must have bounded cleanup"). `max_age` should be at
        least as large as the largest `window` configured for any
        `PostgresAttemptLimiter` sharing this table, so a row is never
        deleted while it could still legitimately count toward some
        layer's `is_blocked` check. Returns the number of rows deleted.
        Safe to call repeatedly (e.g. from a scheduled Container Apps
        Job, task 9) -- a call that deletes nothing is a normal outcome,
        never an error.
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM attempt_failures WHERE occurred_at < (SELECT now()) - %s",
                        (max_age,),
                    )
                    return cur.rowcount
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc
