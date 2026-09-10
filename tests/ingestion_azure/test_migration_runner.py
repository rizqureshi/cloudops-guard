"""Migration-runner behavioral tests (task 8): forward-only, safe to run
more than once, drift detection, and lock-based serialization -- against
a real, local PostgreSQL instance.
"""

from __future__ import annotations

import threading

import psycopg
import pytest

from cloudops_guard.ingestion_azure.errors import MigrationError
from cloudops_guard.ingestion_azure.migration_runner import (
    load_migration_files,
    migration_status,
    run_migrations,
)

pytestmark = pytest.mark.postgres


def test_all_packaged_migrations_apply_cleanly(postgres_conninfo: str) -> None:
    # postgres_conninfo fixture already applies migrations once -- confirm
    # every packaged version is present.
    status = migration_status(postgres_conninfo)
    files = load_migration_files()
    assert status["applied_versions"] == [f.version for f in files]
    assert status["up_to_date"] is True


def test_running_again_is_a_safe_noop(postgres_conninfo: str) -> None:
    applied = run_migrations(postgres_conninfo)
    assert applied == []
    status = migration_status(postgres_conninfo)
    assert status["up_to_date"] is True


def test_drift_in_an_already_applied_migration_is_detected(postgres_conninfo: str) -> None:
    with psycopg.connect(postgres_conninfo, autocommit=True) as conn:
        conn.execute(
            "UPDATE schema_migrations SET checksum = 'deliberately-wrong' WHERE version = 1"
        )
    with pytest.raises(MigrationError):
        run_migrations(postgres_conninfo)


def test_unexpected_applied_version_is_detected_and_marks_not_up_to_date(
    postgres_conninfo: str,
) -> None:
    """Rollback-safety fix: a version recorded as applied in the database
    but absent from this candidate's own packaged migration files (e.g.
    a newer image already applied it, and this candidate is an older
    rollback target that predates that migration's file) must be
    reported distinctly, and must make `up_to_date` false -- forward-only
    migrations mean the older candidate was never written to understand
    a schema a later version already moved to.
    """
    with psycopg.connect(postgres_conninfo, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
            "VALUES (999999, 'a_future_migration_this_candidate_does_not_package', "
            "'deadbeef', now())"
        )
    status = migration_status(postgres_conninfo)
    assert status["unexpected_applied_versions"] == [999999]
    assert status["up_to_date"] is False
    # Genuinely pending/drifted migrations remain unaffected by this
    # unrelated extra row.
    assert status["pending_versions"] == []
    assert status["drifted_versions"] == []


def test_migration_files_are_loaded_in_version_order() -> None:
    files = load_migration_files()
    versions = [f.version for f in files]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)


def test_concurrent_migration_runs_serialize_safely(postgres_admin_conninfo: str) -> None:
    """Two callers racing `run_migrations` against a fresh, unmigrated
    database must never both attempt to `CREATE TABLE` concurrently --
    the advisory lock (task 8) must serialize them, and both must finish
    successfully with the schema fully applied exactly once.
    """
    import uuid

    db_name = f"cog_test_concurrent_{uuid.uuid4().hex[:16]}"
    with psycopg.connect(postgres_admin_conninfo, autocommit=True) as admin_conn:
        admin_conn.execute(f'CREATE DATABASE "{db_name}"')
    base = postgres_admin_conninfo.rsplit("/", 1)[0]
    test_conninfo = f"{base}/{db_name}"

    results: list[list[int]] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def run() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(run_migrations(test_conninfo))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"unexpected errors: {errors}"
    all_applied = [v for result in results for v in result]
    files = load_migration_files()
    # Exactly one thread should have applied each migration version --
    # never both (which would mean the lock failed to serialize them),
    # and never neither (which would mean the schema was never applied).
    assert sorted(all_applied) == [f.version for f in files]

    status = migration_status(test_conninfo)
    assert status["up_to_date"] is True

    with psycopg.connect(postgres_admin_conninfo, autocommit=True) as admin_conn:
        admin_conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (db_name,),
        )
        admin_conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
