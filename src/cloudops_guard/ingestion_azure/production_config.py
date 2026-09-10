"""Strict, fail-closed production-configuration loading from environment
variables (Phase 4G-A, task 7). `load_production_config_from_environment`
is the **only** place in this codebase that reads `os.environ` for the
Azure production deployment -- every other module in this package takes
already-parsed, already-validated values as constructor arguments.

**Fails closed on anything missing, empty, or malformed** -- never a
silent default for anything security- or correctness-relevant (a region,
a database connection string, an HMAC key, a numeric threshold). The one
deliberate exception is `EXPECTED_REGION`, which is not read from the
environment at all: it is a fixed constant (`canadacentral`), so a
misconfigured environment cannot accidentally "configure its way around"
the recorded region decision (task 7: "Refuses any region other than
`canadacentral`").
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

from cloudops_guard.ingestion.argon2_backend import Argon2SecretVerifier
from cloudops_guard.ingestion.interfaces import AttemptLimiter, RequestRateLimiter

from .blob_store import AzureBlobReportBlobStore, create_managed_identity_blob_service_client
from .errors import ProductionEnvironmentError
from .pool import IngestionDatabasePool
from .postgres_attempt_limiter import PostgresAttemptLimiter
from .postgres_metadata_store import PostgresMetadataStore
from .postgres_request_rate_limiter import PostgresRequestRateLimiter
from .postgres_token_store import PostgresTokenStore

#: The one recorded, approved region (see the Phase 4G-A correction
#: request's own "Recorded human decisions" section, and
#: `docs/pilots/phase-4g-authorization-checklist.md`). Deliberately not
#: an environment variable -- a region decision this consequential must
#: never be one misconfigured/missing environment variable away from
#: silently drifting.
EXPECTED_REGION = "canadacentral"

#: Pilot-scale defaults (Recorded human decisions): Layer 1 (per
#: lookup_id), Layer 2 (per source), Layer 3 (per authenticated token),
#: and the unauthenticated capabilities endpoint. Overridable via
#: environment variables for the rare case a pilot's own written
#: agreement calls for a different figure, but a missing/malformed value
#: always fails closed -- it never silently falls back to these
#: defaults, so an operator can never be surprised by a *silently
#: applied* default in a production environment (`_read_int`/`_read_
#: timedelta_seconds` below always require the variable to be present).
DEFAULT_LOOKUP_THRESHOLD = 10
DEFAULT_LOOKUP_WINDOW = dt.timedelta(minutes=15)
DEFAULT_SOURCE_THRESHOLD = 30
DEFAULT_SOURCE_WINDOW = dt.timedelta(minutes=5)
DEFAULT_TOKEN_RATE_THRESHOLD = 60
DEFAULT_TOKEN_RATE_WINDOW = dt.timedelta(minutes=1)
DEFAULT_CAPABILITIES_RATE_THRESHOLD = 60
DEFAULT_CAPABILITIES_RATE_WINDOW = dt.timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class AzureProductionSettings:
    """The complete, already-validated set of values
    `load_production_config_from_environment` extracts from the process
    environment, before any adapter is constructed. Exists as its own
    type (rather than passing raw strings around) so every value's
    validation happens in exactly one place, and so a test can construct
    one directly without needing real environment variables.
    """

    region: str
    metadata_db_conninfo: str
    token_db_conninfo: str
    blob_account_url: str
    blob_container_name: str
    limiter_db_conninfo: str
    limiter_hmac_key: bytes
    retention_period: dt.timedelta
    lookup_threshold: int
    lookup_window: dt.timedelta
    source_threshold: int
    source_window: dt.timedelta
    token_rate_threshold: int
    token_rate_window: dt.timedelta
    capabilities_rate_threshold: int
    capabilities_rate_window: dt.timedelta


def _read_required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise ProductionEnvironmentError(
            f"required environment variable {name} is missing or empty."
        )
    return value


def _read_int(name: str, *, minimum: int = 1) -> int:
    raw = _read_required(name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProductionEnvironmentError(
            f"environment variable {name} must be an integer."
        ) from exc
    if value < minimum:
        raise ProductionEnvironmentError(f"environment variable {name} must be >= {minimum}.")
    return value


def _read_timedelta_seconds(name: str) -> dt.timedelta:
    seconds = _read_int(name, minimum=1)
    return dt.timedelta(seconds=seconds)


def _read_hmac_key(name: str) -> bytes:
    raw = _read_required(name)
    key = raw.encode("utf-8")
    if len(key) < 32:
        raise ProductionEnvironmentError(
            f"environment variable {name} must be at least 32 bytes (256 bits) of key material."
        )
    return key


def _read_and_validate_region() -> str:
    region = _read_required("COG_INGESTION_REGION")
    if region != EXPECTED_REGION:
        raise ProductionEnvironmentError(
            f"COG_INGESTION_REGION must be {EXPECTED_REGION!r} (the recorded, approved "
            f"region) -- got a different value. Refusing to start in any other region."
        )
    return region


@dataclass(frozen=True, slots=True)
class MigrationSettings:
    """Correction-pass item 3's "command-specific configuration loader"
    alternative: the schema-migration job (`ops_cli.py migrate`/
    `migration-status`) only ever calls `migration_runner.run_migrations`/
    `migration_status`, both of which take a single connection string --
    it never constructs an `IngestionApiConfig` and never touches
    blob storage, the token store, or any limiter. Requiring it to
    supply all 15 `AzureProductionSettings` variables would force an
    operator to wire secrets this job structurally cannot use into its
    own least-privilege identity (`docs/deployment/
    azure-ingestion-production.md` §4's `cog_migrator` role) -- exactly
    the "genuinely needs fewer values" case this module's own docstring
    (and the correction request) calls for.
    """

    region: str
    metadata_db_conninfo: str


def load_migration_settings_from_environment() -> MigrationSettings:
    """Reads only the two variables the migration job genuinely needs.
    Still enforces the region invariant -- a migration job is exactly as
    capable of running in the wrong region as the main application is.
    """
    region = _read_and_validate_region()
    return MigrationSettings(
        region=region,
        metadata_db_conninfo=_read_required("COG_METADATA_DB_CONNINFO"),
    )


@dataclass(frozen=True, slots=True)
class LimiterCleanupSettings:
    """Correction-pass item 3's narrow loader for `ops_cli.py
    limiter-cleanup`, which only ever constructs
    `PostgresAttemptLimiter`/`PostgresRequestRateLimiter` instances
    against the limiter database -- it never touches the metadata store,
    the token store, or blob storage.
    """

    region: str
    limiter_db_conninfo: str
    limiter_hmac_key: bytes


def load_limiter_cleanup_settings_from_environment() -> LimiterCleanupSettings:
    """Reads only the three variables the limiter-cleanup job genuinely
    needs.
    """
    region = _read_and_validate_region()
    return LimiterCleanupSettings(
        region=region,
        limiter_db_conninfo=_read_required("COG_LIMITER_DB_CONNINFO"),
        limiter_hmac_key=_read_hmac_key("COG_LIMITER_HMAC_KEY"),
    )


def load_settings_from_environment() -> AzureProductionSettings:
    """Reads and strictly validates every required environment variable.
    Raises `ProductionEnvironmentError` naming the first offending
    variable -- never a partial/best-effort configuration.
    """
    region = _read_and_validate_region()

    return AzureProductionSettings(
        region=region,
        metadata_db_conninfo=_read_required("COG_METADATA_DB_CONNINFO"),
        token_db_conninfo=_read_required("COG_TOKEN_DB_CONNINFO"),
        blob_account_url=_read_required("COG_BLOB_ACCOUNT_URL"),
        blob_container_name=_read_required("COG_BLOB_CONTAINER_NAME"),
        limiter_db_conninfo=_read_required("COG_LIMITER_DB_CONNINFO"),
        limiter_hmac_key=_read_hmac_key("COG_LIMITER_HMAC_KEY"),
        retention_period=_read_timedelta_seconds("COG_RETENTION_PERIOD_SECONDS"),
        lookup_threshold=_read_int("COG_LOOKUP_LIMITER_THRESHOLD"),
        lookup_window=_read_timedelta_seconds("COG_LOOKUP_LIMITER_WINDOW_SECONDS"),
        source_threshold=_read_int("COG_SOURCE_LIMITER_THRESHOLD"),
        source_window=_read_timedelta_seconds("COG_SOURCE_LIMITER_WINDOW_SECONDS"),
        token_rate_threshold=_read_int("COG_TOKEN_RATE_LIMITER_THRESHOLD"),
        token_rate_window=_read_timedelta_seconds("COG_TOKEN_RATE_LIMITER_WINDOW_SECONDS"),
        capabilities_rate_threshold=_read_int("COG_CAPABILITIES_RATE_LIMITER_THRESHOLD"),
        capabilities_rate_window=_read_timedelta_seconds(
            "COG_CAPABILITIES_RATE_LIMITER_WINDOW_SECONDS"
        ),
    )


@dataclass(frozen=True, slots=True)
class ProductionAdapters:
    """Every constructed adapter plus the pools that own their
    connections -- returned together so a caller (the production
    entrypoint) can `open()` the pools before serving traffic and
    `close()` them on shutdown, without needing to know each adapter's
    own internal pool reference.
    """

    metadata_store: PostgresMetadataStore
    token_store: PostgresTokenStore
    blob_store: AzureBlobReportBlobStore
    lookup_limiter: AttemptLimiter
    source_limiter: AttemptLimiter
    token_rate_limiter: RequestRateLimiter
    capabilities_rate_limiter: RequestRateLimiter
    retention_period: dt.timedelta
    metadata_pool: IngestionDatabasePool
    token_pool: IngestionDatabasePool
    limiter_pool: IngestionDatabasePool


def build_production_adapters(settings: AzureProductionSettings) -> ProductionAdapters:
    """Constructs every real adapter from already-validated settings.
    Constructing these objects performs no I/O by itself (mirrors
    `ingestion_api.app.create_app`'s own "constructing performs no I/O"
    discipline) -- the pools are constructed with `open=False`
    (`IngestionDatabasePool.__init__`); the caller must call `.open()` on
    each pool (`ProductionAdapters.metadata_pool`/`token_pool`/
    `limiter_pool` -- deliberately separate pools per task 4's "separate
    runtime, migration, and operator privileges" design, even though a
    pilot deployment may point all three at the same physical database
    instance under different schemas/roles) before serving traffic.
    """
    metadata_pool = IngestionDatabasePool(settings.metadata_db_conninfo)
    token_pool = IngestionDatabasePool(settings.token_db_conninfo)
    limiter_pool = IngestionDatabasePool(settings.limiter_db_conninfo)

    metadata_store = PostgresMetadataStore(
        metadata_pool,
        idempotency_key_window=dt.timedelta(hours=24),
    )
    token_store = PostgresTokenStore(token_pool, Argon2SecretVerifier())

    blob_client = create_managed_identity_blob_service_client(settings.blob_account_url)
    blob_store = AzureBlobReportBlobStore(blob_client, container_name=settings.blob_container_name)

    lookup_limiter = PostgresAttemptLimiter(
        limiter_pool,
        threshold=settings.lookup_threshold,
        window=settings.lookup_window,
        hmac_key=settings.limiter_hmac_key,
    )
    source_limiter = PostgresAttemptLimiter(
        limiter_pool,
        threshold=settings.source_threshold,
        window=settings.source_window,
        hmac_key=settings.limiter_hmac_key,
    )
    token_rate_limiter = PostgresRequestRateLimiter(
        limiter_pool,
        threshold=settings.token_rate_threshold,
        window=settings.token_rate_window,
        hmac_key=settings.limiter_hmac_key,
    )
    capabilities_rate_limiter = PostgresRequestRateLimiter(
        limiter_pool,
        threshold=settings.capabilities_rate_threshold,
        window=settings.capabilities_rate_window,
        hmac_key=settings.limiter_hmac_key,
    )

    return ProductionAdapters(
        metadata_store=metadata_store,
        token_store=token_store,
        blob_store=blob_store,
        lookup_limiter=lookup_limiter,
        source_limiter=source_limiter,
        token_rate_limiter=token_rate_limiter,
        capabilities_rate_limiter=capabilities_rate_limiter,
        retention_period=settings.retention_period,
        metadata_pool=metadata_pool,
        token_pool=token_pool,
        limiter_pool=limiter_pool,
    )
