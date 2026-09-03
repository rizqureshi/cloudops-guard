"""Injectable HTTP transport boundary for `cloudops-guard upload`.

`UploadTransport` is a `Protocol` any test double can implement (see
`tests/test_uploader_zero_network.py`'s fakes); `Urllib3UploadTransport`
is the one production implementation, built on the already-required
`urllib3` base dependency -- **not** the `httpx`/`starlette`/`anyio`
stack `cloudops_guard.ingestion_api` uses, so uploading never requires
installing the `api` optional-dependency group. Nothing in this module
performs any I/O at import time or at construction time
(`Urllib3UploadTransport.__init__` only stores configuration and lazily
builds its `urllib3.PoolManager` on first use) -- a network request
happens only inside `post()`, called only from `service.py`'s
post-confirmation upload step.

TLS certificate verification is always required (`cert_reqs=
"CERT_REQUIRED"`, urllib3 2.x's own default besides). Retries and
redirects are both explicitly disabled (`Retry(total=0, redirect=0,
raise_on_status=False)` plus `redirect=False` on the request call itself,
belt and suspenders) -- a 3xx response is therefore always returned
as-is, never auto-followed, so the bearer token can never be silently
forwarded to a different host. Connect/read timeouts are bounded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import urllib3
import urllib3.exceptions as urllib3_exceptions

from .errors import UploadTransportError

#: Bounded connect/read timeouts (seconds) -- no specific value is
#: mandated anywhere in the ingestion API contract; these are a
#: deliberately conservative, documented judgment call, generous enough
#: for a real network round-trip against a report up to
#: `MAX_REQUEST_BODY_BYTES` while still failing within a bounded time
#: rather than hanging indefinitely.
CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0

#: The maximum number of response bytes this client will ever read, no
#: matter what a declared Content-Length header says or how many bytes
#: actually arrive -- every documented ingestion API response body is the
#: small, fixed success/error envelope (`docs/milestones/
#: v0.4.0-ingestion-api.md` §E), so this is a generous but still bounded
#: ceiling, defensive against a misbehaving or malicious server, never a
#: value any real response is expected to approach.
MAX_RESPONSE_BODY_BYTES = 64 * 1024

_CHUNK_SIZE = 8192


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """The result of one HTTP request this client actually received a
    response for -- `status`/`body` only. Never carries the request's
    own headers (so a caller can never accidentally echo the
    `Authorization` header back out via this object) and never a raw
    native transport exception.
    """

    status: int
    body: bytes


class UploadTransport(Protocol):
    """The one operation `service.py` needs: send the request envelope,
    get back a status and a bounded body, or raise `UploadTransportError`
    for anything that prevented that (connection/DNS/TLS/timeout
    failure, a redirect response, or an oversized response body). An
    ordinary HTTP error status (400/401/.../500) is **not** raised here
    -- it is returned as an ordinary `TransportResponse`, for
    `response.py` to interpret; only a failure to get a well-formed,
    bounded HTTP response at all raises.
    """

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> TransportResponse: ...


def _classify_transport_exception(exc: Exception) -> str:
    # `Retry(total=0, ...)` still routes a connection-level failure
    # through `MaxRetryError` in current urllib3 versions -- unwrapped
    # here so classification is robust to that detail either way, since
    # `.reason` holds the actual underlying exception when present.
    if isinstance(exc, urllib3_exceptions.MaxRetryError) and exc.reason is not None:
        exc = exc.reason  # type: ignore[assignment]
    if isinstance(exc, urllib3_exceptions.NameResolutionError):
        return "DNS resolution failed"
    if isinstance(exc, urllib3_exceptions.SSLError):
        return "TLS verification failed"
    if isinstance(exc, urllib3_exceptions.ConnectTimeoutError):
        return "connection timed out"
    if isinstance(exc, urllib3_exceptions.ReadTimeoutError):
        return "the server did not respond in time"
    if isinstance(exc, urllib3_exceptions.NewConnectionError):
        return "connection failed"
    return "the request could not be completed"


class Urllib3UploadTransport:
    """The production `UploadTransport`. Lazily constructs its own
    `urllib3.PoolManager` on first use -- never at `__init__` time, so
    constructing this class (e.g. while wiring up the CLI command) opens
    no socket and performs no I/O by itself.
    """

    def __init__(
        self,
        *,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        read_timeout: float = READ_TIMEOUT_SECONDS,
    ) -> None:
        self._timeout = urllib3.Timeout(connect=connect_timeout, read=read_timeout)
        self._pool: urllib3.PoolManager | None = None

    def _pool_manager(self) -> urllib3.PoolManager:
        if self._pool is None:
            self._pool = urllib3.PoolManager(cert_reqs="CERT_REQUIRED")
        return self._pool

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> TransportResponse:
        pool = self._pool_manager()
        no_retries = urllib3.Retry(
            total=0,
            connect=0,
            read=0,
            redirect=0,
            raise_on_redirect=False,
            raise_on_status=False,
        )
        try:
            response = pool.request(
                "POST",
                url,
                body=body,
                headers=headers,
                timeout=self._timeout,
                retries=no_retries,
                redirect=False,
                preload_content=False,
            )
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: every
            # transport-layer failure, of any native exception type this
            # client library might ever raise, must be classified and
            # sanitized here, never left to propagate as a raw native
            # exception a caller might print verbatim.
            raise UploadTransportError(_classify_transport_exception(exc)) from None

        return _finalize_response(response)


#: The real HTTP status range (`RFC 9110` §15): any 3-digit code from
#: `100` through `599`. `_validate_status` uses this to reject a
#: `status` property that returns something technically `int`-shaped but
#: not a plausible HTTP status at all (e.g. a hostile/broken response
#: object returning `-1` or `999999`), in addition to rejecting a
#: non-`int` (including `bool`, since `bool` is an `int` subclass in
#: Python) outright.
_MIN_HTTP_STATUS = 100
_MAX_HTTP_STATUS = 599


def _validate_status(value: object) -> int:
    """**Second correction pass, item 3.** Never trusts `response.status`
    to already be a well-formed `int` -- a hostile/broken response object
    could return a `bool`, a `str`, a `float`, or an arbitrary object,
    any of which would otherwise reach a numeric comparison
    (`300 <= status < 400`) or `TransportResponse.status`'s own `int`
    field unchecked. `isinstance(value, bool)` is checked *before*
    `isinstance(value, int)` because `bool` is an `int` subclass in
    Python -- `True`/`False` would otherwise silently "validate" as
    `1`/`0`.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise UploadTransportError("the server responded with a malformed HTTP status.")
    if not (_MIN_HTTP_STATUS <= value <= _MAX_HTTP_STATUS):
        raise UploadTransportError("the server responded with a malformed HTTP status.")
    return value


def _read_response_body(response: object) -> tuple[int, bytes]:
    """Status capture, header inspection, and bounded streaming for one
    already-obtained response -- everything in here can raise an
    arbitrary native exception (a hostile/broken response's `.status`/
    `.headers.get`, a decoding failure, an incomplete read, a mid-stream
    connection drop) and is deliberately never sanitized *here*;
    `_finalize_response`, the sole caller, is the one place that
    boundary is enforced (correction pass, item 1).

    **Second correction pass, item 3.** `response.status` is read
    **exactly once**, right here, and returned to the caller alongside
    the body -- `_finalize_response` builds its `TransportResponse` from
    that single captured, validated value and never accesses
    `response.status` a second time. The original implementation read
    `response.status` once here (inside the sanitizing boundary) and
    again when constructing `TransportResponse` (**after** that
    boundary, and after `response.release_conn()` had already run) --
    independently reproduced with a fake `.status` property that
    returned `201` on its first access and raised
    `RuntimeError("SECOND_STATUS_RAW_SENTINEL")` on its second, letting
    that raw `RuntimeError` escape completely unsanitized.
    """
    status = _validate_status(response.status)  # type: ignore[attr-defined]
    if 300 <= status < 400:
        # Redirects are never followed (redirect=False in post() above);
        # treated as an error outright so the bearer token this request
        # just carried can never be forwarded anywhere else -- see this
        # module's own docstring.
        raise UploadTransportError(
            f"the server responded with a redirect (HTTP {status}), "
            "which this client never follows."
        )
    declared_length = response.headers.get("Content-Length")  # type: ignore[attr-defined]
    if declared_length is not None:
        try:
            if int(declared_length) > MAX_RESPONSE_BODY_BYTES:
                raise UploadTransportError(
                    "the server's response declared a body larger than this client will read."
                )
        except ValueError:
            pass  # malformed Content-Length -- fall through to the bounded read below
    body = _read_bounded(response, MAX_RESPONSE_BODY_BYTES)
    return status, body


def _finalize_response(response: object) -> TransportResponse:
    """**Correction pass, item 1.** The complete post-request lifecycle
    for one already-obtained response -- header inspection, bounded body
    streaming, and connection release/cleanup -- with every native
    exception any of those three steps might raise (urllib3, socket,
    SSL/TLS, HTTP-protocol, decoding, or incomplete-read errors, or an
    arbitrary exception from a hostile/broken response object) sanitized
    into `UploadTransportError` before it can ever reach a caller. A
    deliberately raised `UploadTransportError` (the redirect/oversized-
    declared-length checks in `_read_response_body`) is never re-wrapped,
    passed through unchanged.

    Connection release (`response.release_conn()`) is attempted exactly
    once, always, on every path (success or failure) -- but a failure
    *there* can never mask an already-in-flight primary exception from
    body streaming: it is caught and held, then only re-surfaced (as its
    own sanitized `UploadTransportError`) if streaming itself otherwise
    succeeded. If both streaming and cleanup fail, the streaming failure
    -- the earlier, primary one -- is what propagates; the cleanup
    failure is discarded.
    """
    release_error: Exception | None = None
    try:
        status, body_bytes = _read_response_body(response)
    except UploadTransportError:
        raise
    except Exception as exc:  # noqa: BLE001 -- see module docstring: every
        # native exception streaming/header-inspection might raise must
        # be sanitized here, the same discipline `post()`'s own
        # pool.request() boundary already applies to connection setup.
        raise UploadTransportError(_classify_transport_exception(exc)) from None
    finally:
        try:
            response.release_conn()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 -- held, never raised
            # directly from inside `finally`, which would otherwise
            # silently replace/mask a primary exception already
            # propagating from the `try` block above.
            release_error = exc

    if release_error is not None:
        # Streaming itself succeeded, but cleanup did not -- surfaced as
        # its own sanitized failure rather than silently returning a
        # response whose connection was never actually released cleanly.
        raise UploadTransportError(
            "failed to release the HTTP connection cleanly after a successful response."
        ) from None

    # Second correction pass, item 3: built from the single `status`
    # value `_read_response_body` already captured and validated --
    # `response.status` is never accessed a second time here.
    return TransportResponse(status=status, body=body_bytes)


def _read_bounded(response: object, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.stream(_CHUNK_SIZE):  # type: ignore[attr-defined]
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise UploadTransportError(
                "the server's response exceeded this client's maximum response size."
            )
        chunks.append(chunk)
    return b"".join(chunks)
