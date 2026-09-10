"""Offline operator commands (Phase 4G-A, task 11): retention-expiry
sweep, eligible physical purge, expired limiter-row cleanup, migration
status/check, operator-only tenant inventory, and tenant retirement/purge
planning.

**Deliberately not registered as a `cloudops-guard` subcommand** (task 20:
"Existing CLI commands functioning without Azure extras installed") --
importing this module requires the `azure-production` extra
(`psycopg`/`azure-storage-blob`/`azure-identity`), so wiring it into
`cloudops_guard.cli` (which the base install must remain usable without
that extra) would make every ordinary `cloudops-guard audit ...`/`upload`
invocation transitively import a database driver and a cloud SDK it never
needs. An operator runs this module directly instead:
`python -m cloudops_guard.ingestion_azure.ops_cli <command>` -- never
wired into the public HTTP API (`ingestion_api.app`), and never invoked
automatically by anything in this codebase.

Every command below reads its database connection info from the same
`COG_*` environment variables `production_config.py` reads, so an
operator running this against a real deployment uses the exact same
configuration the production entrypoint itself would.
"""

from __future__ import annotations

import datetime as dt
import getpass
import json
import os

import typer

from cloudops_guard.ingestion_api import lifecycle as ingestion_api_lifecycle
from cloudops_guard.ingestion_api.config import IngestionApiConfig

from . import inventory
from .migration_runner import migration_status, run_migrations
from .pool import IngestionDatabasePool
from .postgres_attempt_limiter import PostgresAttemptLimiter
from .postgres_request_rate_limiter import PostgresRequestRateLimiter
from .production_config import (
    build_production_adapters,
    load_limiter_cleanup_settings_from_environment,
    load_migration_settings_from_environment,
    load_settings_from_environment,
)
from .purge_sweep import run_purge_sweep

app = typer.Typer(
    help="Offline Phase 4G-A operator commands. Never a public HTTP endpoint.",
    add_completion=False,
)


@app.command("migrate")
def migrate_command() -> None:
    """Applies every not-yet-applied migration to the metadata database.
    Safe to run more than once. Uses the narrow migration loader
    (`load_migration_settings_from_environment`, correction-pass item 3)
    -- only `COG_INGESTION_REGION`/`COG_METADATA_DB_CONNINFO` are
    required, never the full 15-variable production configuration this
    command structurally cannot use.
    """
    settings = load_migration_settings_from_environment()
    applied = run_migrations(settings.metadata_db_conninfo)
    typer.echo(f"applied migrations: {applied}")


@app.command("migration-status")
def migration_status_command() -> None:
    """Read-only: reports which migrations are packaged, applied, and
    whether any drift was detected. Never takes the migration lock. Uses
    the narrow migration loader, same as `migrate` above.

    **Correction pass (workflow gate correlation)**: the printed JSON is
    wrapped with an `execution_name` field taken from
    `CONTAINER_APP_JOB_EXECUTION_NAME` -- the environment variable Azure
    Container Apps Jobs documents as being injected into every job
    execution's own containers. This lets a caller reading this output
    back out of Log Analytics (which aggregates every execution's stdout
    into one shared table) confirm the JSON blob it parsed came from the
    *exact* execution it just started, not an unrelated earlier one
    within the same time window. Never a secret -- an execution name is
    an opaque Azure-generated identifier, not connection info.

    Printed as a single **compact** (non-indented) JSON line, deliberately
    -- Container Apps' own log collection captures stdout per line, so a
    pretty-printed, multi-line document would be split across several
    separate Log Analytics rows, only the first of which would contain
    the `execution_name` a correlating query filters on. One line keeps
    the whole document one atomic, correlatable, parseable unit.
    """
    settings = load_migration_settings_from_environment()
    status = migration_status(settings.metadata_db_conninfo)
    payload = {"execution_name": os.environ.get("CONTAINER_APP_JOB_EXECUTION_NAME"), **status}
    typer.echo(json.dumps(payload))


@app.command("retention-sweep")
def retention_sweep_command() -> None:
    """Retires every `received` record whose age exceeds the configured
    retention period. Safe to call repeatedly or concurrently with itself.
    """
    settings = load_settings_from_environment()
    adapters = build_production_adapters(settings)
    adapters.metadata_pool.open()
    try:
        config = IngestionApiConfig(
            metadata_store=adapters.metadata_store,
            blob_store=adapters.blob_store,
            token_store=adapters.token_store,
            lookup_limiter=adapters.lookup_limiter,
            source_limiter=adapters.source_limiter,
            token_rate_limiter=adapters.token_rate_limiter,
            capabilities_rate_limiter=adapters.capabilities_rate_limiter,
            retention_period=adapters.retention_period,
        )
        retired = ingestion_api_lifecycle.run_retention_sweep(config)
        typer.echo(f"retired {len(retired)} record(s).")
    finally:
        adapters.metadata_pool.close()


@app.command("purge-sweep")
def purge_sweep_command(
    batch_size: int = typer.Option(
        100, help="Maximum number of retired records to purge in this run."
    ),
) -> None:
    """Physically purges up to `batch_size` currently-eligible retired
    records (correction-pass item 7 -- the real bounded, concurrency-safe
    sweep, replacing the earlier acknowledged migration-status
    placeholder). Safe to call repeatedly or concurrently with itself, a
    manual `purge` invocation, or a customer `DELETE`: every actual purge
    goes through the same exclusive purge-claim protocol
    `cloudops_guard.ingestion_api.lifecycle.purge_retired_ingestion`
    already enforces.
    """
    settings = load_settings_from_environment()
    adapters = build_production_adapters(settings)
    adapters.metadata_pool.open()
    try:
        config = IngestionApiConfig(
            metadata_store=adapters.metadata_store,
            blob_store=adapters.blob_store,
            token_store=adapters.token_store,
            lookup_limiter=adapters.lookup_limiter,
            source_limiter=adapters.source_limiter,
            token_rate_limiter=adapters.token_rate_limiter,
            capabilities_rate_limiter=adapters.capabilities_rate_limiter,
            retention_period=adapters.retention_period,
        )
        result = run_purge_sweep(config, batch_size=batch_size)
        typer.echo(
            f"considered {result.candidates_considered}, "
            f"purged {len(result.purged)}, "
            f"already_unavailable {len(result.already_unavailable)}, "
            f"failures {len(result.failures)}."
        )
        for failure in result.failures:
            typer.echo(
                f"  failure: tenant={failure.tenant_id} "
                f"ingestion_id={failure.ingestion_id} error={failure.error_type}"
            )
    finally:
        adapters.metadata_pool.close()


@app.command("purge")
def purge_command(
    tenant_id: str = typer.Option(..., help="Tenant ID."),
    ingestion_id: str = typer.Option(..., help="Ingestion ID to purge."),
) -> None:
    """Physically purges one already-retired record's report bytes and
    marks its metadata/tombstone deleted. A no-op (returns `None`, prints
    a clear message) if the record is unknown, foreign-tenant, already
    deleted, or its tombstone has expired -- never raises for those
    cases.
    """
    settings = load_settings_from_environment()
    adapters = build_production_adapters(settings)
    adapters.metadata_pool.open()
    try:
        config = IngestionApiConfig(
            metadata_store=adapters.metadata_store,
            blob_store=adapters.blob_store,
            token_store=adapters.token_store,
            lookup_limiter=adapters.lookup_limiter,
            source_limiter=adapters.source_limiter,
            token_rate_limiter=adapters.token_rate_limiter,
            capabilities_rate_limiter=adapters.capabilities_rate_limiter,
            retention_period=adapters.retention_period,
        )
        result = ingestion_api_lifecycle.purge_retired_ingestion(config, tenant_id, ingestion_id)
        typer.echo("purged." if result is not None else "nothing to purge.")
    finally:
        adapters.metadata_pool.close()


@app.command("limiter-cleanup")
def limiter_cleanup_command(
    max_age_seconds: int = typer.Option(
        3600, help="Delete limiter rows older than this many seconds."
    ),
) -> None:
    """Bounded cleanup of expired `AttemptLimiter`/`RequestRateLimiter`
    rows. Safe to call repeatedly (e.g. from a scheduled Container Apps
    Job). Uses the narrow limiter-cleanup loader
    (`load_limiter_cleanup_settings_from_environment`, correction-pass
    item 3) -- only 3 variables are required, never the full 15.
    """
    settings = load_limiter_cleanup_settings_from_environment()
    pool = IngestionDatabasePool(settings.limiter_db_conninfo)
    pool.open()
    try:
        max_age = dt.timedelta(seconds=max_age_seconds)
        attempt_limiter = PostgresAttemptLimiter(
            pool, threshold=1, window=dt.timedelta(seconds=1), hmac_key=settings.limiter_hmac_key
        )
        rate_limiter = PostgresRequestRateLimiter(
            pool, threshold=1, window=dt.timedelta(seconds=1), hmac_key=settings.limiter_hmac_key
        )
        deleted_failures = attempt_limiter.cleanup_expired(max_age=max_age)
        deleted_counters = rate_limiter.cleanup_expired(max_age=max_age)
        typer.echo(
            f"deleted {deleted_failures} attempt-failure row(s), "
            f"{deleted_counters} request-rate-counter row(s)."
        )
    finally:
        pool.close()


@app.command("tenant-inventory")
def tenant_inventory_command(
    tenant_id: str = typer.Option(..., help="Tenant ID."),
    confirm_tenant_id: str = typer.Option(
        ..., help="Re-enter the tenant ID exactly, to confirm the intended target."
    ),
) -> None:
    """Read-only. Lists every ingestion record for `tenant_id`, regardless
    of status -- never report content, which `IngestionRecord` never
    holds.
    """
    settings = load_settings_from_environment()
    metadata_pool = IngestionDatabasePool(settings.metadata_db_conninfo)
    metadata_pool.open()
    try:
        from .postgres_metadata_store import PostgresMetadataStore

        metadata_store = PostgresMetadataStore(
            metadata_pool, idempotency_key_window=dt.timedelta(hours=24)
        )
        entries = inventory.build_tenant_inventory(
            metadata_store, tenant_id=tenant_id, confirm_tenant_id=confirm_tenant_id
        )
        for entry in entries:
            typer.echo(
                f"{entry.ingestion_id}  {entry.status:<10}  reason={entry.reason}  "
                f"received_at={entry.received_at.isoformat()}  "
                f"retired_at={entry.retired_at.isoformat() if entry.retired_at else '-'}  "
                f"deleted_at={entry.deleted_at.isoformat() if entry.deleted_at else '-'}"
            )
        typer.echo(f"{len(entries)} record(s) total for tenant {tenant_id!r}.")
    finally:
        metadata_pool.close()


@app.command("tenant-offboard-plan")
def tenant_offboard_plan_command(
    tenant_id: str = typer.Option(..., help="Tenant ID."),
    confirm_tenant_id: str = typer.Option(
        ..., help="Re-enter the tenant ID exactly, to confirm the intended target."
    ),
) -> None:
    """Read-only preview of what `tenant-offboard-execute` would do."""
    settings = load_settings_from_environment()
    metadata_pool = IngestionDatabasePool(settings.metadata_db_conninfo)
    metadata_pool.open()
    try:
        from .postgres_metadata_store import PostgresMetadataStore

        metadata_store = PostgresMetadataStore(
            metadata_pool, idempotency_key_window=dt.timedelta(hours=24)
        )
        plan = inventory.plan_tenant_offboarding(
            metadata_store, tenant_id=tenant_id, confirm_tenant_id=confirm_tenant_id
        )
        typer.echo(f"to_retire: {plan.to_retire}")
        typer.echo(f"to_purge: {plan.to_purge}")
        typer.echo(f"already_deleted: {plan.already_deleted}")
        typer.echo(
            f"required confirmation phrase for execute: "
            f"{inventory.required_offboarding_confirmation_phrase(tenant_id)!r}"
        )
    finally:
        metadata_pool.close()


@app.command("tenant-offboard-execute")
def tenant_offboard_execute_command(
    tenant_id: str = typer.Option(..., help="Tenant ID."),
    confirm_tenant_id: str = typer.Option(
        ..., help="Re-enter the tenant ID exactly, to confirm the intended target."
    ),
    confirmation_phrase: str = typer.Option(
        ...,
        help=(
            "Must exactly equal 'RETIRE-AND-PURGE-<tenant_id>' -- see "
            "tenant-offboard-plan's output."
        ),
    ),
) -> None:
    """Mutating. Retires every `received` record, then physically purges
    every `retired` record for `tenant_id`. Requires the exact
    confirmation phrase; never proceeds without it.
    """
    settings = load_settings_from_environment()
    adapters = build_production_adapters(settings)
    adapters.metadata_pool.open()
    adapters.limiter_pool.open()
    try:
        config = IngestionApiConfig(
            metadata_store=adapters.metadata_store,
            blob_store=adapters.blob_store,
            token_store=adapters.token_store,
            lookup_limiter=adapters.lookup_limiter,
            source_limiter=adapters.source_limiter,
            token_rate_limiter=adapters.token_rate_limiter,
            capabilities_rate_limiter=adapters.capabilities_rate_limiter,
            retention_period=adapters.retention_period,
        )
        result = inventory.execute_tenant_offboarding(
            config,
            adapters.limiter_pool,
            tenant_id=tenant_id,
            confirm_tenant_id=confirm_tenant_id,
            confirmation_phrase=confirmation_phrase,
            operator=getpass.getuser(),
        )
        typer.echo(f"retired: {result.retired}")
        typer.echo(f"purged: {result.purged}")
        typer.echo(f"already_deleted: {result.already_deleted}")
        typer.echo(f"failures: {result.failures}")
    finally:
        adapters.metadata_pool.close()
        adapters.limiter_pool.close()


if __name__ == "__main__":  # pragma: no cover
    app()
