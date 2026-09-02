"""Framework-independent authentication coordination and per-scope
authorization for the v0.4.0 ingestion API
(`docs/milestones/v0.4.0-ingestion-api.md` §F).

`AuthenticationCoordinator.authenticate` takes domain values only --
a presented token string and a caller-supplied trusted source identifier
-- never an HTTP request object. A future Phase 4D transport adapter is
responsible for extracting those two values from a real request (the
`Authorization: Bearer <token>` header for the token; whatever that
adapter's own deployment trusts as a source identifier, e.g. a verified
client IP) and translating the typed exceptions this module raises
(`errors.AuthenticationFailed`/`AuthorizationFailed`/`RateLimited`) into
HTTP responses. None of that translation, and no HTTP framework, route,
handler, or server of any kind, exists in this module or anywhere in this
package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .abuse_protection import lookup_scope_key, source_scope_key, token_scope_key
from .errors import AuthenticationFailed, AuthorizationFailed, RateLimited
from .interfaces import AttemptLimiter, RequestRateLimiter, TokenStore
from .models import TokenScope
from .token_format import TokenFormatError, parse_token

#: The one fixed message every `AuthenticationFailed` carries, regardless
#: of cause -- a malformed token, an unknown `lookup_id`, a revoked
#: token, a wrong secret, a Layer 1 block, and a Layer 2 block are all
#: indistinguishable to a caller by design
#: (`docs/milestones/v0.4.0-ingestion-api.md` §G: "no distinguishing
#: detail in the response").
GENERIC_AUTHENTICATION_FAILURE_MESSAGE: Final[str] = "Authentication failed."

#: The (future-endpoint -> required scope) mapping §F/Phase 4C's own
#: instructions define. Not consumed by any code path in this
#: package -- there is no HTTP layer yet to route through it -- kept here
#: purely as the single, documented, testable source of truth for this
#: mapping, so `authorize()`'s scope values are traceable back to a real
#: future endpoint rather than an arbitrary string.
FUTURE_ENDPOINT_SCOPES: Final[dict[str, TokenScope]] = {
    "POST /api/v1/reports": TokenScope.REPORTS_WRITE,
    "GET /api/v1/reports/{id}": TokenScope.REPORTS_READ,
    "DELETE /api/v1/reports/{id}": TokenScope.REPORTS_DELETE,
}


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """The immutable result of a successful `authenticate` call -- safe,
    server-side-derived identity only. Never carries the plaintext
    secret, the complete bearer token, or `secret_hash`: there is no
    field here that could hold any of them, by construction (compare
    `models.TokenRecord`, which does carry `secret_hash` but is never
    itself returned by `authenticate`).
    """

    lookup_id: str
    tenant_id: str
    scopes: frozenset[TokenScope]


def authorize(principal: AuthenticatedPrincipal, required_scope: TokenScope) -> None:
    """Authorization, kept entirely separate from credential verification
    (`docs/milestones/v0.4.0-ingestion-api.md` §F): `principal` must
    already be the result of a *successful* `authenticate` call. Accepts
    a `TokenScope` value, never an arbitrary endpoint string (see
    `FUTURE_ENDPOINT_SCOPES` for the endpoint -> scope mapping a future
    transport adapter uses to pick one). Raises `errors.AuthorizationFailed`
    if `required_scope` is not in `principal.scopes` -- possessing one
    scope never implicitly grants another.
    """
    if required_scope not in principal.scopes:
        raise AuthorizationFailed(f"missing required scope: {required_scope.value}")


class AuthenticationCoordinator:
    """Coordinates one `authenticate` call against an injected
    `TokenStore` and three independently-scoped `AttemptLimiter`
    instances (§F's Layers 1/2/3) -- see each constructor parameter's
    docstring below for why three separate instances, not one shared
    limiter, are accepted.
    """

    def __init__(
        self,
        *,
        token_store: TokenStore,
        lookup_limiter: AttemptLimiter,
        source_limiter: AttemptLimiter,
        token_rate_limiter: RequestRateLimiter,
    ) -> None:
        """`lookup_limiter` (Layer 1) and `source_limiter` (Layer 2) are
        `AttemptLimiter` instances -- failure counters for authentication-
        abuse protection, unchanged from Phase 4C. `token_rate_limiter`
        (Layer 3) is a `RequestRateLimiter` (Phase 4D correction): Layer 3
        was never about authentication-guessing at all (§F describes it as
        "unrelated to authentication-guessing... bounding a legitimately-
        authenticated token's own ordinary request volume"), so counting
        it with a *failure* counter was a category error from the start --
        Phase 4C's own version of this class could check `AttemptLimiter.
        is_blocked` for Layer 3, but nothing could truthfully ever call
        `record_failure` for an ordinary successful request, so that
        check could never actually trigger. See `interfaces.
        RequestRateLimiter` for the full rationale. Each parameter is a
        separate instance so each layer's own threshold can be configured
        independently (§F selects no production threshold for any of
        them).
        """
        self._token_store = token_store
        self._lookup_limiter = lookup_limiter
        self._source_limiter = source_limiter
        self._token_rate_limiter = token_rate_limiter

    def authenticate(self, presented_token: str, source_identifier: str) -> AuthenticatedPrincipal:
        """The complete authentication flow, in the exact order
        `docs/milestones/v0.4.0-ingestion-api.md` §F requires. Every call
        performs a fresh `TokenStore.lookup` -- nothing here is cached
        across calls, so a `mark_revoked` call takes effect on the very
        next `authenticate` call for that `lookup_id`, with no window.

        Raises `errors.AuthenticationFailed` (always with the same
        message, `GENERIC_AUTHENTICATION_FAILURE_MESSAGE`) for a Layer 2
        block, a malformed token, an unknown `lookup_id`, a revoked
        token, a Layer 1 block, or a wrong secret -- all indistinguishable
        to the caller. Raises `errors.RateLimited` only after a
        *successful* authentication, if Layer 3 is blocked. Returns an
        `AuthenticatedPrincipal` otherwise.
        """
        # Step 1: Layer 2 (source-scoped), before any other work -- this
        # request's source is checked before the token is even parsed.
        source_key = source_scope_key(source_identifier)
        if self._source_limiter.is_blocked(source_key):
            raise AuthenticationFailed(GENERIC_AUTHENTICATION_FAILURE_MESSAGE)

        # Step 2: parse and validate token structure. A malformed token
        # is one of the two things Layer 2 is specifically documented to
        # cover (the other being an unknown lookup_id, step 4 below) --
        # record a source-scoped failure and reject generically, never
        # reaching TokenStore or Argon2id.
        try:
            parsed = parse_token(presented_token)
        except TokenFormatError:
            self._source_limiter.record_failure(source_key)
            raise AuthenticationFailed(GENERIC_AUTHENTICATION_FAILURE_MESSAGE) from None

        # Step 3: look up TokenRecord using only lookup_id.
        record = self._token_store.lookup(parsed.lookup_id)

        # Step 4: unknown lookup_id -- record the source-scoped failure
        # only (there is no valid lookup_id to scope a Layer 1 counter
        # against); never invoke Argon2id.
        if record is None:
            self._source_limiter.record_failure(source_key)
            raise AuthenticationFailed(GENERIC_AUTHENTICATION_FAILURE_MESSAGE)

        # Step 5: revoked -- reject immediately, never invoke Argon2id.
        if record.revoked:
            raise AuthenticationFailed(GENERIC_AUTHENTICATION_FAILURE_MESSAGE)

        # Step 6/7: Layer 1 (lookup_id-scoped), checked before Argon2id.
        lookup_key = lookup_scope_key(parsed.lookup_id)
        if self._lookup_limiter.is_blocked(lookup_key):
            raise AuthenticationFailed(GENERIC_AUTHENTICATION_FAILURE_MESSAGE)

        # Step 8: verify the secret exactly once, through the approved
        # TokenStore.verify_secret interface -- never a raw Argon2id call
        # here, and never a preliminary plaintext comparison of any kind.
        verified = self._token_store.verify_secret(parsed.secret, record.secret_hash)

        # Step 9: wrong secret -- record both the lookup_id-scoped and
        # the source-scoped failure, then reject generically.
        if not verified:
            self._lookup_limiter.record_failure(lookup_key)
            self._source_limiter.record_failure(source_key)
            raise AuthenticationFailed(GENERIC_AUTHENTICATION_FAILURE_MESSAGE)

        # Step 10: tenant and scopes are derived exclusively from the
        # stored TokenRecord -- never from presented_token, source_identifier,
        # or any other caller-supplied value.
        principal = AuthenticatedPrincipal(
            lookup_id=record.lookup_id,
            tenant_id=record.tenant_id,
            scopes=record.scopes,
        )

        # Step 11/12: Layer 3 (per-authenticated-token), checked -- and
        # this request counted against it -- only now, strictly after
        # successful verification: the budget is never checked or
        # consumed for a request that has not yet authenticated. One
        # atomic check_and_record_request call, never a separate
        # is_blocked-then-record pair (which could race), and never
        # called more than once per request (no double-counting).
        token_key = token_scope_key(record.lookup_id)
        if not self._token_rate_limiter.check_and_record_request(token_key):
            raise RateLimited("This token has exceeded its authenticated request-rate limit.")

        # Step 13.
        return principal
