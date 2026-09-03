"""Fail-closed production-configuration validation for the ingestion API
(Phase 4F production-hardening review). No production entrypoint exists
yet -- provisioning one, and actually deploying it, remains Phase 4G,
requiring separate, explicit human authorization. This module exists so
that whenever such an entrypoint is built, it has one single, already-
tested guard to call before serving real traffic, rather than each
future entrypoint re-inventing (or forgetting) this check.

**Never** silently substitutes a "real" adapter for an in-memory one --
no real (database-, object-store-, or secret-manager-backed) adapter is
implemented anywhere in this codebase yet (Phase 4B/4D only implemented
the interfaces, §H, and their in-memory reference implementations). This
module only refuses to *start* with the wrong adapters; it does not, and
cannot, construct correct ones on a caller's behalf.
"""

from __future__ import annotations

import datetime as dt

from cloudops_guard.ingestion.reference import (
    InMemoryAttemptLimiter,
    InMemoryMetadataStore,
    InMemoryReportBlobStore,
    InMemoryRequestRateLimiter,
    InMemoryTokenStore,
)

from .config import IngestionApiConfig

#: Every reference/in-memory implementation this module refuses to run in
#: production mode -- deliberately matched by concrete class, never by
#: duck typing or a naming convention, so a future new in-memory
#: implementation must be added here explicitly rather than silently
#: passing this check by accident.
_IN_MEMORY_ADAPTER_TYPES = (
    InMemoryMetadataStore,
    InMemoryReportBlobStore,
    InMemoryTokenStore,
    InMemoryAttemptLimiter,
    InMemoryRequestRateLimiter,
)


class ProductionConfigError(Exception):
    """Raised by `validate_production_config` when an `IngestionApiConfig`
    is not fit to serve real traffic. Never raised for a local/test/CI
    configuration -- every automated test in this repository, and any
    developer running the API locally, legitimately constructs an
    in-memory configuration and must never call this function at all.
    """


def validate_production_config(config: IngestionApiConfig) -> None:
    """Fails closed -- raises `ProductionConfigError`, naming every
    offending field -- unless every store and limiter in `config` is
    something other than this package's own in-memory reference
    implementation, and the configured retention period is a genuine
    positive duration. Intended to be called exactly once, at startup, by
    a future (Phase 4G) production entrypoint, before that entrypoint
    accepts any real request.

    Deliberately conservative, and deliberately limited in scope: this
    function can only ever detect "this is still a known in-memory
    reference implementation" -- it cannot verify that a *different*
    adapter passed in its place is actually correctly configured (real
    TLS, real encryption at rest, a real atomic database transaction
    behind `create_or_get_received`, a real trusted-proxy-aware source
    identifier, etc.). Passing this check is necessary, never sufficient,
    for production readiness -- see
    `docs/reviews/v0.4.0-phase-4f-security-readiness.md` for the complete
    set of requirements this one function cannot verify on its own.
    """
    offending_adapters: list[str] = []
    for field_name, value in (
        ("metadata_store", config.metadata_store),
        ("blob_store", config.blob_store),
        ("token_store", config.token_store),
        ("lookup_limiter", config.lookup_limiter),
        ("source_limiter", config.source_limiter),
        ("token_rate_limiter", config.token_rate_limiter),
        ("capabilities_rate_limiter", config.capabilities_rate_limiter),
    ):
        if isinstance(value, _IN_MEMORY_ADAPTER_TYPES):
            offending_adapters.append(field_name)

    # `retention_period` is checked independently of the adapter loop above
    # so that a configuration violating both categories at once reports
    # both -- never only whichever check happened to run first. The
    # `isinstance` check is required before any comparison: `timedelta.
    # __le__` raises a raw `TypeError` (not `ProductionConfigError`) for a
    # non-comparable type such as `None` or a `str`, which this function
    # must never let escape.
    retention_is_invalid = not isinstance(
        config.retention_period, dt.timedelta
    ) or config.retention_period <= dt.timedelta(0)

    if not offending_adapters and not retention_is_invalid:
        return

    messages: list[str] = []
    if offending_adapters:
        messages.append(
            "the following IngestionApiConfig fields are still this "
            "package's own in-memory reference implementation -- never "
            "durable across a process restart, never shared across "
            "replicas, and never safe for real customer data: "
            + ", ".join(sorted(offending_adapters))
        )
    if retention_is_invalid:
        # Deliberately never includes `config.retention_period`'s own
        # repr/value here -- an arbitrary object's __repr__ is not
        # guaranteed safe to surface in an exception message (it could
        # contain sensitive text), so only the fixed field name is named.
        messages.append("retention_period must be a genuine positive datetime.timedelta")

    raise ProductionConfigError("refusing to start in production mode: " + "; ".join(messages))
