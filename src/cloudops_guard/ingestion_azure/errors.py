"""Typed exceptions for the Azure production adapters (Phase 4G-A).

None of these carry an HTTP status code -- the existing `ingestion_api`
transport layer (`app.py`) only ever calls these adapters through the
provider-neutral interfaces (`cloudops_guard.ingestion.interfaces`), so a
database/blob-store failure surfaces to a handler as an ordinary,
undocumented exception -- caught by `app.py`'s existing generic
`except Exception` fallback and mapped to the fixed `500 internal_error`
envelope, exactly as any other unexpected storage failure already is.
This module exists so adapter-internal failure modes are at least typed
and identifiable in logs/tests, never so they can be given a bespoke HTTP
mapping that would leak infrastructure detail to a caller.
"""

from __future__ import annotations


class AzureAdapterError(Exception):
    """Base class for every exception this package raises."""


class DatabaseUnavailableError(AzureAdapterError):
    """Raised when a PostgreSQL-backed adapter cannot obtain or use a
    connection -- the connection pool is exhausted, the database is
    unreachable, or a query fails for a connectivity reason. Fail-closed:
    every adapter in this package raises this (or lets the underlying
    `psycopg` exception propagate) rather than silently permitting a
    request because the database was unavailable. Never includes a
    connection string, credential, or raw query text in its message.
    """


class MigrationError(AzureAdapterError):
    """Raised by the migration runner (`migrations.py`) when a migration
    cannot be safely applied -- an out-of-order version, a checksum
    mismatch against an already-applied migration, or a failure to
    acquire the migration lock within a bounded timeout (indicating a
    concurrent migration run, never silently proceeding in that case).
    """


class BlobPreconditionFailedError(AzureAdapterError):
    """Raised internally when Azure Blob Storage's own conditional-create
    precondition (`If-None-Match: *`) fails because the blob already
    exists -- caught by `AzureBlobReportBlobStore.put_if_absent` and
    translated to its documented `False` return value; a caller of this
    package's public adapter classes should never see this exception
    directly, only the boolean/interface-defined behavior.
    """


class ProductionEnvironmentError(AzureAdapterError):
    """Raised by `production_config.load_production_config_from_environment`
    when a required environment variable is missing, empty, or fails its
    own strict validation (e.g. a region other than `canadacentral`, a
    non-numeric threshold, a malformed connection URL). Fails closed --
    never falls back to a default value for anything security- or
    correctness-relevant. Never includes a secret value in its message,
    only the name of the offending variable.
    """


class SourceIdentificationError(AzureAdapterError):
    """Raised by `source_identifier.resolve_trusted_client_address` when
    the configured trusted-proxy topology cannot be honored for a given
    request -- e.g. `X-Forwarded-For` is absent entirely, which Azure
    Container Apps' own HTTP ingress is documented to always populate
    (`docs/deployment/azure-ingestion-production.md` §6), so its absence
    indicates either a request that did not arrive through Container
    Apps ingress at all, or a platform anomaly this code must never
    silently paper over by falling back to an untrusted value.
    """
