"""Explicit, dependency-injected configuration for the ingestion API
application factory (`app.create_app`) -- no global/module-level storage
instance, clock, or ID generator exists anywhere in this package;
everything a handler needs is reached only through an `IngestionApiConfig`
instance a caller constructs and passes in. No production database,
object store, secret manager, or numeric abuse-protection threshold is
selected here (§F/§I) -- every limiter/store field is caller-supplied,
typically one of Phase 4B/4D's local, in-memory reference
implementations.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field

from cloudops_guard.ingestion.interfaces import (
    AttemptLimiter,
    MetadataStore,
    ReportBlobStore,
    RequestRateLimiter,
    TokenStore,
)

from .ids import generate_ingestion_id, generate_request_id

#: §C's proposed default retention period -- explicitly documented there
#: as "configurable per pilot agreement," so this one is a constructor
#: parameter rather than a fixed protocol constant like `limits.py`'s.
DEFAULT_RETENTION_PERIOD = dt.timedelta(days=90)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True, slots=True)
class IngestionApiConfig:
    metadata_store: MetadataStore
    blob_store: ReportBlobStore
    token_store: TokenStore

    #: Layer 1 (pre-Argon2id, per-`lookup_id`) and Layer 2 (per-source,
    #: covering malformed tokens/unknown `lookup_id`s/capabilities) --
    #: unchanged from Phase 4C, `AttemptLimiter`-backed.
    lookup_limiter: AttemptLimiter
    source_limiter: AttemptLimiter

    #: Layer 3 (per-authenticated-token ordinary request volume) --
    #: `RequestRateLimiter`-backed as of Phase 4D (task 3.3).
    token_rate_limiter: RequestRateLimiter

    #: The unauthenticated capabilities endpoint's own ordinary
    #: request-volume throttle -- a separate `RequestRateLimiter`
    #: instance/scope from `token_rate_limiter` above (task 3.3), source-
    #: scoped rather than token-scoped.
    capabilities_rate_limiter: RequestRateLimiter

    clock: Callable[[], dt.datetime] = _utc_now
    request_id_generator: Callable[[], str] = generate_request_id
    ingestion_id_generator: Callable[[], str] = generate_ingestion_id
    retention_period: dt.timedelta = field(default=DEFAULT_RETENTION_PERIOD)
