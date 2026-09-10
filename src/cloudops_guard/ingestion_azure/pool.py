"""A single, shared PostgreSQL connection pool for every adapter in this
package (Phase 4G-A). Every adapter class takes a pool instance rather
than opening its own connections, so a production entrypoint constructs
exactly one pool per database and shares it across the metadata store,
token store, and both limiters -- mirroring how a single
`InMemoryMetadataStore`/`InMemoryTokenStore` instance is already shared
across a test's `IngestionApiConfig` today.

**Fail-closed, never fail-open**: every method below either returns a
healthy connection or raises -- there is no code path that could cause a
caller to proceed as though a database operation succeeded when the pool
could not actually reach PostgreSQL.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import psycopg_pool

from .errors import DatabaseUnavailableError


class IngestionDatabasePool:
    """Thin wrapper over `psycopg_pool.ConnectionPool` -- exists so
    adapters depend on this package's own narrow interface (`connection()`,
    `server_now()`) rather than importing `psycopg_pool` directly
    themselves, and so `open`/`close` have one obvious owner (the
    production entrypoint, `entrypoint.py`).
    """

    def __init__(
        self,
        conninfo: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not conninfo:
            raise ValueError("conninfo must not be empty.")
        # `open=False`: connections are established lazily by `open()`,
        # never as a side effect of constructing this object -- mirrors
        # this project's existing "importing/constructing performs no I/O"
        # discipline (`ingestion_api.app.create_app`'s own docstring).
        self._pool = psycopg_pool.ConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=timeout_seconds,
            kwargs={"autocommit": False},
        )

    def open(self) -> None:
        """Eagerly establishes the pool's minimum connections and fails
        closed (raises) if PostgreSQL is unreachable -- called exactly
        once, at production-entrypoint startup, never per-request.
        """
        try:
            self._pool.open(wait=True, timeout=self._pool.timeout)
        except psycopg_pool.PoolTimeout as exc:
            raise DatabaseUnavailableError(
                "could not establish the PostgreSQL connection pool within the configured timeout."
            ) from exc

    def close(self) -> None:
        """Closes every pooled connection -- called once, at shutdown."""
        self._pool.close()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """A context manager yielding a `psycopg.Connection` checked out
        from the pool, returned on exit. Raises `DatabaseUnavailableError`
        (never a raw `psycopg` exception) if a connection cannot be
        obtained within the configured timeout -- every adapter method in
        this package calls this rather than `psycopg.connect` directly, so
        "the database is unreachable" always fails the same, typed way.

        **Correction-pass item 9**: `psycopg_pool.ConnectionPool.
        connection()` is itself a `@contextmanager`-decorated generator --
        calling it merely constructs a context-manager object; the actual
        connection acquisition (and thus any `PoolTimeout`) happens only
        when that object's `__enter__` runs, i.e. at the caller's own
        `with ... as conn:` line. The original version of this method
        wrapped only the *call* to `self._pool.connection()` in a
        `try/except PoolTimeout` -- which can never catch anything, since
        no acquisition has happened yet at that point -- silently letting
        a raw, undocumented `PoolTimeout` escape from pool exhaustion
        under real concurrent load (reproduced with `max_size=1` and two
        overlapping callers; see `tests/ingestion_azure/
        test_pool_timeout_translation.py`). Fixed by making this method
        itself a generator wrapping the *entry* into `self._pool.
        connection()` inside the `try` block, so the translation actually
        covers the moment acquisition can fail.
        """
        try:
            with self._pool.connection() as conn:
                yield conn
        except psycopg_pool.PoolTimeout as exc:
            raise DatabaseUnavailableError(
                "no PostgreSQL connection became available within the configured timeout."
            ) from exc

    def server_now(self) -> dt.datetime:
        """Returns PostgreSQL's own `now()` -- the "authoritative clock"
        every windowed/expiring guarantee in this package (task 5's
        requirement) is measured against, never this process's own
        `datetime.now()`, so multiple application replicas racing the
        same limiter/metadata rows never disagree about "now" due to
        their own local clock skew.
        """
        with self.connection() as conn:
            row = conn.execute("SELECT now()").fetchone()
            assert row is not None
            value = row[0]
            assert isinstance(value, dt.datetime)
            return value

    def healthcheck(self) -> bool:
        """A cheap, read-only liveness probe -- `SELECT 1` against a
        pooled connection. Returns `True`/`False`, never raises; used by
        the production entrypoint's own startup/liveness check (task 7),
        never exposed as a new public HTTP endpoint (task 7 explicitly
        forbids adding one that would contradict the four-endpoint API
        contract).
        """
        try:
            with self.connection() as conn:
                conn.execute("SELECT 1")
            return True
        except (psycopg.Error, DatabaseUnavailableError):
            return False
