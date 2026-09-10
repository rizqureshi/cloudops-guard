"""Real-PostgreSQL regression test for the provisioning-checklist
GRANT-ordering bug (correction-pass item 6).

`docs/deployment/azure-ingestion-production.md` §4 previously ran
`GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public`
for `cog_runtime`/`cog_operator` **immediately after PostgreSQL
provisioning, before any migration had ever run** -- at that point
`schema public` contains zero tables, so PostgreSQL's `ON ALL TABLES`
grants exactly nothing; the later migration-created tables (e.g.
`ingestion_records`) never inherit that grant, since `GRANT ... ON ALL
TABLES` is a one-time, present-tense operation, never a standing rule.
The documented fix uses `ALTER DEFAULT PRIVILEGES FOR ROLE cog_migrator`
**before** migrations run, so every table `cog_migrator` subsequently
creates automatically carries the configured grant.

This test independently reproduces the exact documented (fixed)
provisioning sequence against a real, fresh local PostgreSQL database --
not a mock, not a hand-simulated privilege model -- then proves, via
real connections authenticated as each role:
- the runtime role can SELECT/INSERT/UPDATE/DELETE on a real,
  migration-created table (`ingestion_records`);
- the runtime role cannot CREATE TABLE (no DDL at all);
- the migrator role can apply migrations (already proven by this test's
  own setup: migrations run under the migrator role's own connection).

Mutation-verifiable by reordering the `ALTER DEFAULT PRIVILEGES`
statement to *after* `run_migrations` (reproducing the original bug) --
doing so makes `test_runtime_role_can_perform_required_dml` fail with a
real `psycopg.errors.InsufficientPrivilege`.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from cloudops_guard.ingestion_azure.migration_runner import run_migrations


def _conninfo_with_credentials(base_conninfo: str, *, user: str, password: str) -> str:
    """Returns `base_conninfo` with its userinfo replaced by `user`/
    `password`, leaving host/port/database untouched -- used to build a
    per-role connection string against the same test database.
    """
    parsed = urlsplit(base_conninfo)
    netloc = f"{user}:{password}@{parsed.hostname}"
    if parsed.port is not None:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


pytestmark = pytest.mark.postgres


@pytest.fixture()
def role_names() -> dict[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return {
        "migrator": f"cog_test_migrator_{suffix}",
        "runtime": f"cog_test_runtime_{suffix}",
    }


@pytest.fixture()
def provisioned_database(postgres_admin_conninfo: str, role_names: dict[str, str]):
    """Creates a fresh database and the two roles above, using the exact
    documented (fixed) provisioning sequence -- ALTER DEFAULT PRIVILEGES
    set up under the migrator role *before* migrations run -- then
    applies every real migration under the migrator role's own
    connection. Yields `(migrator_conninfo, runtime_conninfo)`. Tears
    down the database and both roles afterward regardless of outcome.
    """
    db_name = f"cog_priv_test_{uuid.uuid4().hex[:16]}"
    migrator = role_names["migrator"]
    runtime = role_names["runtime"]
    migrator_password = "test-migrator-password"
    runtime_password = "test-runtime-password"

    with psycopg.connect(postgres_admin_conninfo, autocommit=True) as admin_conn:
        admin_conn.execute(f'CREATE DATABASE "{db_name}"')
        # `CREATE ROLE ... PASSWORD` does not accept a query parameter
        # placeholder (Postgres's DDL grammar has no such support) --
        # `sql.Literal` still quotes/escapes the value safely, unlike a
        # bare f-string interpolation. Both passwords are fixed,
        # test-internal constants, never attacker- or user-supplied.
        admin_conn.execute(
            sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(migrator), sql.Literal(migrator_password)
            )
        )
        admin_conn.execute(
            sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(runtime), sql.Literal(runtime_password)
            )
        )

    base = postgres_admin_conninfo.rsplit("/", 1)[0]
    db_conninfo = f"{base}/{db_name}"

    # Step 1: grant connect/usage and full schema privilege to the
    # migrator (needed for CREATE TABLE), and read/write to the runtime
    # role -- table-level grants here affect nothing yet (no tables
    # exist), which is precisely the original bug's own blind spot.
    with psycopg.connect(db_conninfo, autocommit=True) as admin_conn:
        admin_conn.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO {migrator}, {runtime}')
        admin_conn.execute(f"GRANT USAGE ON SCHEMA public TO {migrator}, {runtime}")
        admin_conn.execute(f"GRANT ALL ON SCHEMA public TO {migrator}")

        # Step 2 (the fix): ALTER DEFAULT PRIVILEGES under the migrator
        # role, *before* any migration runs -- this is what makes every
        # table/sequence the migrator subsequently creates automatically
        # carry these grants for the runtime role.
        admin_conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {runtime}"
        )
        admin_conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {runtime}"
        )

    # Step 3: apply every real migration, authenticated as the migrator
    # role -- exactly how a real deployment's `migrate` Container Apps
    # Job would run it (docs §9's provisioning checklist).
    migrator_conninfo = _conninfo_with_credentials(
        db_conninfo, user=migrator, password=migrator_password
    )
    run_migrations(migrator_conninfo)

    runtime_conninfo = _conninfo_with_credentials(
        db_conninfo, user=runtime, password=runtime_password
    )

    try:
        yield migrator_conninfo, runtime_conninfo
    finally:
        with psycopg.connect(postgres_admin_conninfo, autocommit=True) as admin_conn:
            admin_conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            admin_conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
            admin_conn.execute(f"DROP ROLE IF EXISTS {migrator}")
            admin_conn.execute(f"DROP ROLE IF EXISTS {runtime}")


def test_runtime_role_can_perform_required_dml(provisioned_database) -> None:
    """The exact guarantee item 6 requires: a real runtime role can
    perform DML (SELECT/INSERT/UPDATE/DELETE) on a table that was
    created by a migration run *after* the ALTER DEFAULT PRIVILEGES
    setup -- proving the fix, not merely that grants exist in the
    abstract.
    """
    _migrator_conninfo, runtime_conninfo = provisioned_database
    with psycopg.connect(runtime_conninfo, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO ingestion_records "
            "(tenant_id, ingestion_id, report_fingerprint, received_at, status, generation) "
            "VALUES ('t1', 'i1', 'fp1', now(), 'received', 0)"
        )
        row = conn.execute(
            "SELECT status FROM ingestion_records WHERE tenant_id = 't1' AND ingestion_id = 'i1'"
        ).fetchone()
        assert row == ("received",)

        conn.execute(
            "UPDATE ingestion_records SET status = 'received' "
            "WHERE tenant_id = 't1' AND ingestion_id = 'i1'"
        )
        conn.execute("DELETE FROM ingestion_records WHERE tenant_id = 't1' AND ingestion_id = 'i1'")


def test_runtime_role_cannot_perform_ddl(provisioned_database) -> None:
    """The runtime role must never hold CREATE/ALTER/DROP privilege --
    `cog_runtime` is deliberately never granted `GRANT ALL ON SCHEMA`,
    only table-level DML via ALTER DEFAULT PRIVILEGES.
    """
    _migrator_conninfo, runtime_conninfo = provisioned_database
    with psycopg.connect(runtime_conninfo, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE runtime_should_not_be_able_to_create_this (id int)")


def test_migrator_role_can_apply_migrations(provisioned_database) -> None:
    """The migrator role's own ability to apply migrations is exercised
    by `provisioned_database`'s own setup (it calls `run_migrations`
    under the migrator's connection) -- this test asserts that setup
    actually produced the expected schema, confirming the migrator role
    genuinely has DDL privilege (`GRANT ALL ON SCHEMA public`).
    """
    migrator_conninfo, _runtime_conninfo = provisioned_database
    with psycopg.connect(migrator_conninfo, autocommit=True) as conn:
        row = conn.execute("SELECT to_regclass('ingestion_records') IS NOT NULL").fetchone()
        assert row == (True,)
        row = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()
        assert row is not None
        assert row[0] > 0
