"""The Phase 4G-A production entrypoint (task 7) -- the one process a
container image's `ENTRYPOINT` actually runs. Not invoked by anything in
this codebase automatically; a Phase 4G-B deployment is what would ever
actually run this against real infrastructure.

**Startup sequence, in order, and why**:

1. Load and strictly validate environment configuration
   (`production_config.load_settings_from_environment`) -- fails closed
   before anything else happens if any required value is missing,
   malformed, or names a region other than `canadacentral`.
2. Construct real (never in-memory) adapters
   (`production_config.build_production_adapters`) -- constructing them
   performs no I/O.
3. Open every connection pool (`ProductionAdapters.*_pool.open()`) --
   the first real I/O this process performs; fails closed (raises) if any
   database is unreachable, before this process ever starts accepting
   HTTP traffic.
4. Build the `IngestionApiConfig`, wiring in the Azure Container Apps
   trusted-source-identifier resolver (task 6) -- so Layer 2/2.5 abuse
   protection is scoped by the real client address, never the ingress's
   own peer address.
5. Call `production_readiness.validate_production_config` -- refuses
   every in-memory adapter, and refuses a non-positive/malformed
   retention period. This call is **necessary, never sufficient**: it
   proves the config is not still the local reference implementation; it
   proves nothing about whether the real adapters just constructed are
   themselves correctly configured (a wrong connection string, an
   unreachable blob container) -- step 3 above is what actually proves
   connectivity.
6. Run the existing, unchanged `ingestion_api.app.create_app(config)`
   ASGI application under `uvicorn`.
7. On termination (SIGTERM/SIGINT, or `uvicorn.Server.serve()` returning),
   close every pool -- never leak a connection.

**No new public HTTP endpoint**: Azure Container Apps' own TCP startup/
liveness/readiness probes (configured in `infra/azure/modules/
container-app.bicep`) check only that this process accepts a TCP
connection on the configured port -- they never issue an HTTP request,
so they can never consume the capabilities endpoint's own rate-limit
budget (task 7's explicit requirement), and this module adds no fifth
route to the existing, reviewed four-endpoint contract.
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType

import uvicorn

from cloudops_guard.ingestion_api.app import create_app
from cloudops_guard.ingestion_api.config import IngestionApiConfig
from cloudops_guard.ingestion_api.production_readiness import validate_production_config

from .pool import IngestionDatabasePool
from .production_config import (
    ProductionAdapters,
    build_production_adapters,
    load_settings_from_environment,
)
from .source_identifier import resolve_azure_container_apps_client_address

_logger = logging.getLogger("cloudops_guard.ingestion_azure.entrypoint")


def _close_pools(pools: tuple[IngestionDatabasePool, ...]) -> None:
    """Closes every given pool, tolerating an individual close failure so
    one misbehaving pool never prevents the others from being released --
    never leaves a connection open longer than necessary during shutdown.
    """
    for pool in pools:
        try:
            pool.close()
        except Exception:  # noqa: BLE001 -- best-effort shutdown, never re-raises
            _logger.exception("error closing a database connection pool during shutdown")


def _open_pools(adapters: ProductionAdapters) -> None:
    """Opens every pool in order. **Correction-pass item 9**: if a later
    pool's `.open()` call raises, every pool already opened by this same
    call is closed before the exception propagates -- the original
    version of this sequence opened all three pools unconditionally, one
    after another, so a failure on the second or third `.open()` call
    left the earlier pool(s)' already-established connections leaked for
    the remaining lifetime of this failed startup attempt (and, for a
    process that retries startup in a loop, indefinitely accumulating
    further leaked connections on every retry). Never partially succeeds:
    either all three pools end up open, or none of them do.
    """
    opened: list[IngestionDatabasePool] = []
    for pool in (adapters.metadata_pool, adapters.token_pool, adapters.limiter_pool):
        try:
            pool.open()
        except Exception:
            _close_pools(tuple(opened))
            raise
        opened.append(pool)


def build_production_config() -> tuple[IngestionApiConfig, ProductionAdapters]:
    """Performs startup steps 1-5 above. Returns the validated
    `IngestionApiConfig` and the `ProductionAdapters` it was built from
    (the caller needs the latter only to close the pools on shutdown).
    Raises `ProductionEnvironmentError`/`DatabaseUnavailableError`/
    `ProductionConfigError` and refuses to return a config in any of
    those cases -- there is no partially-started state this function can
    return.
    """
    settings = load_settings_from_environment()
    adapters = build_production_adapters(settings)

    _open_pools(adapters)

    # **Correction pass, item 8**: everything from here on runs with all
    # three pools already open. Reproduced directly before this fix: a
    # `validate_production_config` failure (or any other exception in
    # this block) propagated straight out of this function, since the
    # `try/finally` in `run()` that closes pools only wraps `server.run()`
    # -- which never starts if this function itself raises. That left
    # every already-opened pool's connections leaked for the remaining
    # lifetime of a container stuck retrying startup. Wrapping this
    # remaining sequence in its own `try`/`except` closes every opened
    # pool before re-raising, so a post-open failure here leaks nothing,
    # exactly like a failure *during* `_open_pools` itself already didn't.
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
            source_identifier_resolver=resolve_azure_container_apps_client_address,
        )

        # Necessary, never sufficient (see this module's own docstring)
        # -- the last gate before this process is allowed to serve any
        # request.
        validate_production_config(config)
    except Exception:
        _close_pools((adapters.metadata_pool, adapters.token_pool, adapters.limiter_pool))
        raise

    return config, adapters


def run(*, host: str = "0.0.0.0", port: int = 8000) -> None:  # noqa: S104 -- container-internal bind
    """Builds the production config and runs the existing ingestion API
    under `uvicorn`, blocking until termination. Binding `0.0.0.0` is
    correct and required here: this process runs *inside* a container
    whose only network exposure is what Azure Container Apps' own
    ingress explicitly proxies to it (never directly internet-facing on
    its own) -- binding a narrower loopback address would make the
    container unreachable from the platform's ingress entirely.
    """
    logging.basicConfig(level=logging.INFO)
    config, adapters = build_production_config()
    app = create_app(config)

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))

    def _handle_termination(signum: int, frame: FrameType | None) -> None:
        _logger.info("received signal %s -- initiating graceful shutdown", signum)
        server.should_exit = True

    signal.signal(signal.SIGTERM, _handle_termination)
    signal.signal(signal.SIGINT, _handle_termination)

    try:
        server.run()
    finally:
        _close_pools((adapters.metadata_pool, adapters.token_pool, adapters.limiter_pool))


if __name__ == "__main__":  # pragma: no cover -- exercised via `run()` directly in tests
    try:
        run()
    except Exception:
        _logger.exception("production entrypoint failed to start")
        sys.exit(1)
