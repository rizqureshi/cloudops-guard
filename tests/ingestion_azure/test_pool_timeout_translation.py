"""Regression test for `IngestionDatabasePool.connection()`'s
`PoolTimeout` -> `DatabaseUnavailableError` translation (correction-pass
item 9, referenced directly by `pool.py`'s own docstring). Runs against a
real local PostgreSQL instance with a genuinely exhausted pool (`max_size
=1`, one connection held open by a concurrent thread) -- never a mock of
`psycopg_pool`, since the bug this guards against was specifically about
*when* `psycopg_pool.ConnectionPool.connection()`'s own
`@contextmanager`-decorated generator actually attempts acquisition, a
detail a mock could easily paper over.
"""

from __future__ import annotations

import threading

import psycopg_pool
import pytest

from cloudops_guard.ingestion_azure.errors import DatabaseUnavailableError
from cloudops_guard.ingestion_azure.pool import IngestionDatabasePool

pytestmark = pytest.mark.postgres


def test_pool_exhaustion_raises_database_unavailable_error_not_a_raw_pool_timeout(
    postgres_conninfo: str,
) -> None:
    pool = IngestionDatabasePool(postgres_conninfo, min_size=1, max_size=1, timeout_seconds=0.5)
    pool.open()
    try:
        holder_acquired = threading.Event()
        release_holder = threading.Event()

        def hold_the_only_connection() -> None:
            with pool.connection():
                holder_acquired.set()
                release_holder.wait(timeout=5)

        holder_thread = threading.Thread(target=hold_the_only_connection)
        holder_thread.start()
        try:
            assert holder_acquired.wait(timeout=5), "holder thread never acquired the connection"

            # The pool's only connection is held by the thread above --
            # this second, concurrent acquisition attempt must time out.
            with pytest.raises(DatabaseUnavailableError) as exc_info:
                with pool.connection():
                    pytest.fail(
                        "should never have acquired a second connection from a max_size=1 pool"
                    )

            # The raw psycopg_pool exception must never propagate directly
            # -- only the translated, package-specific error type.
            assert not isinstance(exc_info.value, psycopg_pool.PoolTimeout)
            assert isinstance(exc_info.value.__cause__, psycopg_pool.PoolTimeout)
        finally:
            release_holder.set()
            holder_thread.join(timeout=5)
    finally:
        pool.close()


def test_pool_still_works_normally_once_the_held_connection_is_released(
    postgres_conninfo: str,
) -> None:
    """Companion to the exhaustion test above: proves the fix didn't
    break the ordinary, uncontended path -- a connection acquired after
    the only other holder releases its own must succeed and actually run
    a real query.
    """
    pool = IngestionDatabasePool(postgres_conninfo, min_size=1, max_size=1, timeout_seconds=2.0)
    pool.open()
    try:
        with pool.connection() as conn:
            row = conn.execute("SELECT 1").fetchone()
            assert row == (1,)
        # The connection was returned to the pool on `__exit__` above --
        # acquiring again must succeed immediately, not time out.
        with pool.connection() as conn:
            row = conn.execute("SELECT 2").fetchone()
            assert row == (2,)
    finally:
        pool.close()
