"""Behavioral and concurrency tests for `PostgresAttemptLimiter` (expiring
authentication-failure windows) and `PostgresRequestRateLimiter` (atomic
fixed-window request-rate ceiling) -- task 12, items 7-8.
"""

from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from cloudops_guard.ingestion_azure.pool import IngestionDatabasePool
from cloudops_guard.ingestion_azure.postgres_attempt_limiter import PostgresAttemptLimiter
from cloudops_guard.ingestion_azure.postgres_request_rate_limiter import PostgresRequestRateLimiter
from cloudops_guard.ingestion_azure.scope_key_hashing import hash_scope_key

pytestmark = pytest.mark.postgres

HMAC_KEY = b"x" * 32


class TestPostgresAttemptLimiter:
    def test_not_blocked_below_threshold(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresAttemptLimiter(
            pool, threshold=3, window=dt.timedelta(minutes=15), hmac_key=HMAC_KEY
        )
        limiter.record_failure("lookup_id:abc")
        limiter.record_failure("lookup_id:abc")
        assert limiter.is_blocked("lookup_id:abc") is False
        pool.close()

    def test_blocked_at_threshold(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresAttemptLimiter(
            pool, threshold=3, window=dt.timedelta(minutes=15), hmac_key=HMAC_KEY
        )
        for _ in range(3):
            limiter.record_failure("lookup_id:abc")
        assert limiter.is_blocked("lookup_id:abc") is True
        pool.close()

    def test_scopes_are_independent(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresAttemptLimiter(
            pool, threshold=2, window=dt.timedelta(minutes=15), hmac_key=HMAC_KEY
        )
        limiter.record_failure("lookup_id:a")
        limiter.record_failure("lookup_id:a")
        assert limiter.is_blocked("lookup_id:a") is True
        assert limiter.is_blocked("lookup_id:b") is False
        pool.close()

    def test_window_expires_automatically(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresAttemptLimiter(
            pool, threshold=2, window=dt.timedelta(seconds=1), hmac_key=HMAC_KEY
        )
        limiter.record_failure("lookup_id:a")
        limiter.record_failure("lookup_id:a")
        assert limiter.is_blocked("lookup_id:a") is True
        time.sleep(1.5)
        assert limiter.is_blocked("lookup_id:a") is False
        pool.close()

    def test_scope_keys_are_hashed_not_stored_in_plaintext(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresAttemptLimiter(
            pool, threshold=1, window=dt.timedelta(minutes=15), hmac_key=HMAC_KEY
        )
        raw_scope_key = "source:203.0.113.42"
        limiter.record_failure(raw_scope_key)
        with pool.connection() as conn:
            rows = conn.execute("SELECT scope_key_hash FROM attempt_failures").fetchall()
        assert len(rows) == 1
        assert rows[0][0] != raw_scope_key
        assert "203.0.113.42" not in rows[0][0]
        assert rows[0][0] == hash_scope_key(raw_scope_key, hmac_key=HMAC_KEY)
        pool.close()

    def test_cleanup_expired_deletes_old_rows_only(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresAttemptLimiter(
            pool, threshold=100, window=dt.timedelta(minutes=15), hmac_key=HMAC_KEY
        )
        limiter.record_failure("lookup_id:old")
        time.sleep(1.2)
        limiter.record_failure("lookup_id:new")
        deleted = limiter.cleanup_expired(max_age=dt.timedelta(seconds=1))
        assert deleted == 1
        with pool.connection() as conn:
            remaining = conn.execute("SELECT count(*) FROM attempt_failures").fetchone()[0]
        assert remaining == 1
        pool.close()

    def test_concurrent_failures_all_counted_exactly_once(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo, min_size=2, max_size=25)
        pool.open()
        limiter = PostgresAttemptLimiter(
            pool, threshold=1000, window=dt.timedelta(minutes=15), hmac_key=HMAC_KEY
        )

        def record(_i: int) -> None:
            limiter.record_failure("lookup_id:concurrent")

        with ThreadPoolExecutor(max_workers=20) as executor:
            list(executor.map(record, range(20)))

        with pool.connection() as conn:
            count = conn.execute(
                "SELECT count(*) FROM attempt_failures WHERE scope_key_hash = %s",
                (hash_scope_key("lookup_id:concurrent", hmac_key=HMAC_KEY),),
            ).fetchone()[0]
        assert count == 20
        pool.close()


class TestPostgresRequestRateLimiter:
    def test_allows_up_to_threshold(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresRequestRateLimiter(
            pool, threshold=3, window=dt.timedelta(minutes=1), hmac_key=HMAC_KEY
        )
        results = [limiter.check_and_record_request("token:abc") for _ in range(4)]
        assert results == [True, True, True, False]
        pool.close()

    def test_scopes_are_independent(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresRequestRateLimiter(
            pool, threshold=1, window=dt.timedelta(minutes=1), hmac_key=HMAC_KEY
        )
        assert limiter.check_and_record_request("token:a") is True
        assert limiter.check_and_record_request("token:a") is False
        assert limiter.check_and_record_request("token:b") is True
        pool.close()

    def test_new_window_resets_budget(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresRequestRateLimiter(
            pool, threshold=1, window=dt.timedelta(seconds=1), hmac_key=HMAC_KEY
        )
        assert limiter.check_and_record_request("token:a") is True
        assert limiter.check_and_record_request("token:a") is False
        time.sleep(1.5)
        assert limiter.check_and_record_request("token:a") is True
        pool.close()

    def test_rejected_request_is_never_itself_counted(self, postgres_conninfo: str) -> None:
        """A rejected request must not extend starvation indefinitely
        (task 5) -- calling past the budget repeatedly must not further
        increment the stored counter beyond the threshold.
        """
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresRequestRateLimiter(
            pool, threshold=2, window=dt.timedelta(minutes=1), hmac_key=HMAC_KEY
        )
        for _ in range(10):
            limiter.check_and_record_request("token:a")
        with pool.connection() as conn:
            count = conn.execute(
                "SELECT request_count FROM request_rate_counters WHERE scope_key_hash = %s",
                (hash_scope_key("token:a", hmac_key=HMAC_KEY),),
            ).fetchone()[0]
        assert count == 2
        pool.close()

    def test_concurrent_requests_never_exceed_threshold(self, postgres_conninfo: str) -> None:
        """The atomic-ceiling proof, under real concurrent transactions:
        `CONCURRENCY` threads race the same scope key with a threshold of
        `THRESHOLD` -- exactly `THRESHOLD` may succeed, never more.
        """
        concurrency = 30
        threshold = 15
        pool = IngestionDatabasePool(postgres_conninfo, min_size=2, max_size=concurrency + 2)
        pool.open()
        limiter = PostgresRequestRateLimiter(
            pool, threshold=threshold, window=dt.timedelta(minutes=1), hmac_key=HMAC_KEY
        )

        def attempt(_i: int) -> bool:
            return limiter.check_and_record_request("token:shared")

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(attempt, range(concurrency)))

        assert results.count(True) == threshold
        assert results.count(False) == concurrency - threshold
        pool.close()

    def test_cleanup_expired_deletes_old_windows_only(self, postgres_conninfo: str) -> None:
        pool = IngestionDatabasePool(postgres_conninfo)
        pool.open()
        limiter = PostgresRequestRateLimiter(
            pool, threshold=100, window=dt.timedelta(seconds=1), hmac_key=HMAC_KEY
        )
        limiter.check_and_record_request("token:old")
        time.sleep(1.2)
        limiter.check_and_record_request("token:new")
        deleted = limiter.cleanup_expired(max_age=dt.timedelta(seconds=1))
        assert deleted == 1
        with pool.connection() as conn:
            remaining = conn.execute("SELECT count(*) FROM request_rate_counters").fetchone()[0]
        assert remaining == 1
        pool.close()
