"""Scope-key helpers and the standalone, credential-free Layer 2 check for
the three-layer authentication-abuse protection
(`docs/milestones/v0.4.0-ingestion-api.md` §F). All three layers share
Phase 4B's one generic `interfaces.AttemptLimiter` interface, keyed by an
opaque scope string -- this module is the single place those exact key
formats are composed, so `authenticator.py` and any future capabilities
endpoint never hand-format one inconsistently.
"""

from __future__ import annotations

from .errors import RateLimited
from .interfaces import AttemptLimiter, RequestRateLimiter

_LOOKUP_PREFIX = "lookup_id:"
_SOURCE_PREFIX = "source:"
_TOKEN_PREFIX = "token:"


def lookup_scope_key(lookup_id: str) -> str:
    """Layer 1's scope key: a pre-Argon2id limit scoped to one
    `lookup_id`, independent of every other `lookup_id`.
    """
    return f"{_LOOKUP_PREFIX}{lookup_id}"


def source_scope_key(source_identifier: str) -> str:
    """Layer 2's scope key: a limit scoped to the request's own source
    (never a credential) -- covers a malformed token, an unknown
    `lookup_id`, and the unauthenticated capabilities endpoint, none of
    which have a `lookup_id` to scope Layer 1 against.
    """
    return f"{_SOURCE_PREFIX}{source_identifier}"


def token_scope_key(lookup_id: str) -> str:
    """Layer 3's scope key: ordinary per-authenticated-token request-
    volume limiting, checked only after a successful authentication.
    Keyed by `lookup_id` (never the full token or the secret) -- this is
    the only truthful way to represent "this token" without ever holding
    or re-deriving the plaintext token past the moment it was verified.
    """
    return f"{_TOKEN_PREFIX}{lookup_id}"


def check_capabilities_allowed(source_identifier: str, *, attempt_limiter: AttemptLimiter) -> None:
    """The framework-independent Layer 2 check a future, unauthenticated
    `GET /api/v1/capabilities` endpoint calls directly -- no token or
    `lookup_id` exists for such a request, so this checks the source-scoped
    limiter only, and is a pure read (`AttemptLimiter.is_blocked`): a
    capabilities request that is not itself blocked never *records* a
    failure merely for having been made.

    Raises `RateLimited` if `source_identifier` is currently blocked;
    returns `None` otherwise. Never touches a `TokenStore` or Argon2id --
    there is no credential involved in a capabilities request at all.
    """
    if attempt_limiter.is_blocked(source_scope_key(source_identifier)):
        raise RateLimited("This source is temporarily rate-limited.")


def check_and_record_capabilities_request(
    source_identifier: str, *, request_rate_limiter: RequestRateLimiter
) -> None:
    """The ordinary-request-volume counterpart to `check_capabilities_allowed`
    (Phase 4D correction, `interfaces.RequestRateLimiter`) -- a completely
    separate concern and a completely separate counter: this throttles how
    often a source calls the endpoint at all, regardless of whether any of
    those calls ever looked like authentication abuse. A future
    `GET /api/v1/capabilities` handler calls **both** this and
    `check_capabilities_allowed`, in either order, since they read/write
    disjoint state.

    Raises `RateLimited` if `source_identifier` is already at its request
    budget (and does not count this call in that case); otherwise records
    this request and returns `None`.
    """
    if not request_rate_limiter.check_and_record_request(source_scope_key(source_identifier)):
        raise RateLimited("This source has exceeded its request-rate limit.")
