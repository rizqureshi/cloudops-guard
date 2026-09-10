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
from typing import TYPE_CHECKING

from cloudops_guard.ingestion.interfaces import (
    AttemptLimiter,
    MetadataStore,
    ReportBlobStore,
    RequestRateLimiter,
    TokenStore,
)

if TYPE_CHECKING:
    from starlette.requests import Request

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

    #: **Phase 4G-A**: an optional override for `app._peer_source_identifier`'s
    #: default behavior (the raw ASGI `scope["client"]` peer address) --
    #: `None` (the default) preserves that exact existing behavior
    #: unchanged, which is what every Phase 4D/4F test, and any deployment
    #: without a trusted reverse proxy in front of it, continues to use.
    #: A production entrypoint deployed behind a specific, documented
    #: proxy topology (e.g. Azure Container Apps' own ingress --
    #: `cloudops_guard.ingestion_azure.source_identifier.
    #: resolve_azure_container_apps_client_address`) sets this instead,
    #: so Layer 2/2.5's abuse-protection source key is derived from the
    #: real client address that topology's own documentation establishes
    #: as trustworthy, never from the proxy's own peer address (which
    #: would otherwise collapse every real caller into one shared scope
    #: key -- Phase 4F's own recorded, open blocker this field closes).
    source_identifier_resolver: Callable[[Request], str] | None = None
