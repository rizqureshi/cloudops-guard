"""The ingestion API's ASGI application factory and request dispatch
(`docs/milestones/v0.4.0-ingestion-api.md` §E). Deliberately does **not**
use Starlette's `Router`/`Route` classes -- those default to behavior
this contract explicitly forbids (automatic trailing-slash redirects,
automatic `HEAD`/`OPTIONS` handling, framework-default error bodies).
Routing and dispatch below are hand-written path/method matching; only
`starlette.requests.Request` and the `JSONResponse` wrapper in
`responses.py` are used as convenience layers over the raw ASGI
`scope`/`receive`/`send` protocol.

`create_app` performs no I/O of any kind -- calling it does not open a
socket, start a thread, or contact anything. A caller (typically a test)
is responsible for running the returned application under an ASGI server
(e.g. `uvicorn`) on an explicitly-chosen loopback address.

**Correction pass, item 5**: every handler's blocking work (Argon2id
authentication, report/envelope validation, RFC 8785 fingerprinting, and
every synchronous `MetadataStore`/`ReportBlobStore`/limiter call, all of
which are plain synchronous Python calls with no `await` of their own) is
executed inside `anyio.to_thread.run_sync`, on AnyIO's own bounded worker
thread pool (no custom limiter is configured) -- never inline on the
event loop. Without this, a single request's Argon2id hash (deliberately
slow by design) or large-report validation would block the *entire*
event loop for its duration, serializing every other concurrent request
behind it regardless of how many client connections uvicorn had already
accepted. Each handler's own closure captures exactly the local state
(`request`, `config`, already-read body bytes, path parameters) that
call needs -- request context is preserved by ordinary Python closure
capture, not by any thread-local or context-var machinery. Only
`read_bounded_body`'s own `await request.stream()` (genuine async
socket I/O) and the handful of pure, non-blocking dispatch/header checks
stay directly on the event loop.

**Second correction pass, item 2**: for `POST /api/v1/reports`
specifically, authentication and authorization are offloaded and awaited
to completion *before* `read_bounded_body` is ever called -- a missing,
malformed, duplicated, or invalid credential, a rate-limited caller, or
one with insufficient scope must never cause `receive()` to be invoked at
all. The already-authenticated `AuthenticatedPrincipal` is then passed
into the second, decode/validate/store offload rather than
re-authenticating a second time. `_ingest_report_blocking` therefore no
longer performs authentication itself -- see
`_authenticate_and_authorize_for_write` and `_handle_reports_collection`.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Awaitable, Callable

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from cloudops_guard.ingestion.abuse_protection import (
    check_and_record_capabilities_request,
    check_capabilities_allowed,
)
from cloudops_guard.ingestion.authenticator import (
    AuthenticatedPrincipal,
    AuthenticationCoordinator,
    authorize,
)
from cloudops_guard.ingestion.errors import (
    AuthenticationFailed,
    AuthorizationFailed,
    IdempotencyKeyConflict,
)
from cloudops_guard.ingestion.errors import RateLimited as AuthRateLimited
from cloudops_guard.ingestion.models import RetirementReason, TokenScope

from .bounded_body import read_bounded_body, validate_declared_content_length
from .config import IngestionApiConfig
from .coordinator import create_ingestion
from .envelope import parse_envelope
from .errors import (
    FORBIDDEN,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_ALLOWED,
    NOT_FOUND,
    RATE_LIMITED,
    UNAUTHORIZED,
    UNSUPPORTED_API_VERSION,
    UNSUPPORTED_CONTENT_ENCODING,
    UNSUPPORTED_CONTENT_TYPE,
    ApiError,
)
from .limits import (
    MAX_FINDINGS_PER_REPORT,
    MAX_REPORT_BYTES,
    MAX_REQUEST_BODY_BYTES,
    SUPPORTED_REPORT_SCHEMA_VERSIONS,
)
from .logging_utils import log_request_outcome
from .report_validation import compact_report_json_bytes, validate_report
from .responses import error_response, ok_response
from .strict_json import strict_decode_json

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _format_timestamp(value: dt.datetime) -> str:
    """RFC 3339, `Z`-suffixed UTC form (e.g. `"2026-08-27T12:00:00Z"`) --
    never a bare `+00:00` offset.
    """
    text = value.astimezone(dt.UTC).isoformat()
    if text.endswith("+00:00"):
        text = text[: -len("+00:00")] + "Z"
    return text


def _raw_header_values(scope: Scope, name: bytes) -> list[bytes]:
    """**Correction pass, item 3**: reads every occurrence of a header
    directly from the raw ASGI header list (`scope["headers"]`) -- never
    `starlette.datastructures.Headers.get()`, which silently returns only
    the *first* occurrence of a repeated header and hides every other
    one (including a conflicting value) from any caller that relies on
    it. ASGI header names are already lowercased by the server per the
    ASGI specification; `name` must be given pre-lowercased (every caller
    in this module passes a literal lowercase `bytes` constant).
    """
    return [value for key, value in scope["headers"] if key == name]


def _require_exactly_one_header(scope: Scope, name: bytes, error_code: str) -> bytes:
    """Raises `ApiError(error_code)` unless `name` appears in the raw
    ASGI header list **exactly once** -- both a missing header and a
    repeated one (even with byte-identical values across every
    occurrence) are rejected, never silently resolved to "the first
    one." Used for headers a protected/POST request must carry precisely
    one of (`Authorization`, `Content-Type`).
    """
    values = _raw_header_values(scope, name)
    if len(values) != 1:
        raise ApiError(error_code)
    return values[0]


def _require_at_most_one_header(scope: Scope, name: bytes, error_code: str) -> bytes | None:
    """Raises `ApiError(error_code)` if `name` appears more than once in
    the raw ASGI header list (again, even if every occurrence agrees on
    the same value) -- returns the single value if present exactly once,
    or `None` if absent. Used for `Content-Length`, which is optional but
    must never be ambiguous.
    """
    values = _raw_header_values(scope, name)
    if len(values) > 1:
        raise ApiError(error_code)
    return values[0] if values else None


def _reject_any_occurrence(scope: Scope, name: bytes, error_code: str) -> None:
    """Raises `ApiError(error_code)` if `name` appears at all -- used for
    `Content-Encoding`, which this contract never accepts in any
    quantity (§D: "Any `Content-Encoding` header is rejected outright").
    """
    if _raw_header_values(scope, name):
        raise ApiError(error_code)


def _peer_source_identifier(request: Request) -> str:
    """Layer 2/Layer-2.5's abuse-protection source identifier, derived
    **only** from the actual ASGI-reported peer connection (`scope["client"]`)
    -- never from `X-Forwarded-For`/`Forwarded`, which this function does
    not even read, until a future deployment phase explicitly defines a
    set of trusted proxies (task 10).

    **Host only, never the client's ephemeral TCP port**: a real client
    opens a fresh, kernel-assigned source port for every TCP connection
    (and an HTTP client's connection pool routinely opens more than one
    concurrently) -- scoping by `host:port` would give every individual
    connection from the very same attacker its own abuse-protection scope
    key, defeating Layer 2/Layer 2.5 entirely for anyone willing to open
    more than one connection. This was caught by
    `test_ingestion_api_concurrency.py`'s real-loopback-server rate-limit
    test, which failed (30/30 requests succeeded against a threshold of
    15) until this function stopped including the port.
    """
    client = request.client
    if client is None:
        return "unknown"
    return client.host


def _authenticate(request: Request, config: IngestionApiConfig) -> AuthenticatedPrincipal:
    """Parses a bearer credential **only** from the `Authorization` header
    -- never a query parameter, the body, a path segment, or a cookie --
    and authenticates it via the unchanged Phase 4C
    `AuthenticationCoordinator`. Maps its typed exceptions to the fixed
    HTTP error envelope; never logs or echoes the header or token value.

    **Correction pass, item 3**: every protected endpoint requires
    *exactly one* `Authorization` header -- both zero and more than one
    (even two byte-identical copies) are `401 unauthorized`, read from
    the raw ASGI header list rather than `Headers.get()`, which would
    otherwise silently authenticate against only the first of two
    conflicting credentials without ever rejecting the ambiguity itself.
    """
    auth_header_bytes = _require_exactly_one_header(request.scope, b"authorization", UNAUTHORIZED)
    try:
        auth_header = auth_header_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError(UNAUTHORIZED) from exc
    if not auth_header.startswith("Bearer "):
        raise ApiError(UNAUTHORIZED)
    presented_token = auth_header[len("Bearer ") :]
    source_identifier = _peer_source_identifier(request)

    coordinator = AuthenticationCoordinator(
        token_store=config.token_store,
        lookup_limiter=config.lookup_limiter,
        source_limiter=config.source_limiter,
        token_rate_limiter=config.token_rate_limiter,
    )
    try:
        return coordinator.authenticate(presented_token, source_identifier)
    except AuthenticationFailed as exc:
        raise ApiError(UNAUTHORIZED) from exc
    except AuthRateLimited as exc:
        raise ApiError(RATE_LIMITED) from exc


def _authorize(principal: AuthenticatedPrincipal, required_scope: TokenScope) -> None:
    try:
        authorize(principal, required_scope)
    except AuthorizationFailed as exc:
        raise ApiError(FORBIDDEN) from exc


def _assert_exact_json_content_type(request: Request) -> None:
    """Correction pass, item 3: exactly one `Content-Type` header,
    required for `POST` -- zero or more than one is rejected, never
    silently resolved to the first.
    """
    value = _require_exactly_one_header(request.scope, b"content-type", UNSUPPORTED_CONTENT_TYPE)
    if value != b"application/json":
        raise ApiError(UNSUPPORTED_CONTENT_TYPE)


def _assert_no_content_encoding(request: Request) -> None:
    _reject_any_occurrence(request.scope, b"content-encoding", UNSUPPORTED_CONTENT_ENCODING)


def _assert_content_length_singleton(request: Request) -> None:
    """Correction pass, item 3: at most one `Content-Length` header is
    allowed -- a repeated one, even with agreeing values, is rejected
    *before* the body is read or authentication is attempted. **Second
    correction pass, item 2**: this project's own header/singleton checks
    (this function, `_assert_exact_json_content_type`,
    `_assert_no_content_encoding`) and
    `bounded_body.validate_declared_content_length` all run *before*
    authentication -- only `read_bounded_body`'s actual streamed-byte
    read (genuine socket I/O) is deferred until after authentication
    succeeds, for `POST /api/v1/reports`. The remaining single
    `Content-Length` value, if any, is independently re-checked by
    `validate_declared_content_length`/`read_bounded_body`, which can now
    safely assume there is at most one to consider.
    """
    _require_at_most_one_header(request.scope, b"content-length", INVALID_REQUEST)


def _check_capabilities_rate_limits_blocking(request: Request, config: IngestionApiConfig) -> None:
    source_identifier = _peer_source_identifier(request)
    try:
        check_capabilities_allowed(source_identifier, attempt_limiter=config.source_limiter)
        check_and_record_capabilities_request(
            source_identifier, request_rate_limiter=config.capabilities_rate_limiter
        )
    except AuthRateLimited as exc:
        raise ApiError(RATE_LIMITED) from exc


async def _handle_capabilities(
    request: Request, config: IngestionApiConfig, request_id: str
) -> Response:
    if request.method != "GET":
        raise ApiError(METHOD_NOT_ALLOWED, allow="GET")

    # Correction pass, item 5: off the event loop, even though this
    # particular check is fast -- consistent with every other handler,
    # and defensive against lock contention under real concurrent load.
    await anyio.to_thread.run_sync(_check_capabilities_rate_limits_blocking, request, config)

    return ok_response(
        {
            "api_version": "v1",
            "request_id": request_id,
            "supported_report_schema_versions": {
                platform: list(versions)
                for platform, versions in SUPPORTED_REPORT_SCHEMA_VERSIONS.items()
            },
            "max_report_bytes": MAX_REPORT_BYTES,
            "max_request_body_bytes": MAX_REQUEST_BODY_BYTES,
            "max_findings_per_report": MAX_FINDINGS_PER_REPORT,
        }
    )


def _authenticate_and_authorize_for_write(
    request: Request, config: IngestionApiConfig
) -> AuthenticatedPrincipal:
    """**Second correction pass, item 2**: the complete, synchronous
    authentication (Argon2id) and authorization check for
    `POST /api/v1/reports`, run as its own worker-thread offload,
    strictly *before* `read_bounded_body` is ever called. Returns the
    authenticated principal so `_ingest_report_blocking` never
    re-authenticates.
    """
    principal = _authenticate(request, config)
    _authorize(principal, TokenScope.REPORTS_WRITE)
    return principal


def _ingest_report_blocking(
    config: IngestionApiConfig,
    request_id: str,
    raw_body: bytes,
    principal: AuthenticatedPrincipal,
) -> tuple[dict, int]:
    """The synchronous, CPU/store-bound remainder of
    `POST /api/v1/reports` *after* authentication has already succeeded
    (correction pass, item 5; **second correction pass, item 2**: no
    longer authenticates -- `principal` is already authenticated by
    `_authenticate_and_authorize_for_write`, called and awaited earlier):
    strict JSON decoding, envelope/report schema validation, RFC 8785
    fingerprinting, and the cross-store create -- run as a single unit on
    a worker thread, so the event loop is free for the whole duration,
    and so two concurrent requests can genuinely be inside
    `MetadataStore.create_or_get_received` at the same time (proven by
    `test_ingestion_api_thread_offload_concurrency.py`). `config`/
    `request_id`/`raw_body`/`principal` are ordinary closure/argument
    captures -- no thread-local or context-var machinery is needed to
    "carry" them into this thread.
    """
    decoded = strict_decode_json(raw_body)
    envelope = parse_envelope(decoded)
    validate_report(envelope.platform, envelope.report_schema_version, envelope.report)
    report_bytes = compact_report_json_bytes(envelope.report)

    try:
        record, created = create_ingestion(
            config=config,
            tenant_id=principal.tenant_id,
            platform=envelope.platform,
            report_schema_version=envelope.report_schema_version,
            report=envelope.report,
            report_bytes=report_bytes,
            idempotency_key=envelope.idempotency_key,
        )
    except IdempotencyKeyConflict as exc:
        raise ApiError(INVALID_REQUEST) from exc

    body = {
        "ingestion_id": record.ingestion_id,
        "request_id": request_id,
        "received_at": _format_timestamp(record.received_at),
        "report_fingerprint": record.report_fingerprint,
        "status": record.status.value,
    }
    return body, (201 if created else 200)


async def _handle_reports_collection(
    request: Request, config: IngestionApiConfig, request_id: str
) -> Response:
    """**Second correction pass, item 2**: the required ordering for
    `POST /api/v1/reports` is, in order: (1) method and singleton/content
    headers -- cheap, no-I/O checks; (2) the declared `Content-Length`
    ceiling -- also cheap and read-free
    (`validate_declared_content_length`); (3) authentication and
    authorization, offloaded to a worker thread and awaited to
    completion; only *after* that succeeds does (4) the actual bounded,
    incremental body read (`read_bounded_body`'s genuine async socket
    I/O) happen; then (5) decoding/validation/fingerprinting/storage are
    offloaded as a second unit, passing the already-authenticated
    principal from step 3 rather than authenticating a second time. This
    is what makes `receive()` provably unreachable for a request that
    never authenticates -- see the ASGI receive-spy tests in
    `test_ingestion_api_reports_post.py::TestAuthenticationBeforeBodyRead`.
    """
    if request.method != "POST":
        raise ApiError(METHOD_NOT_ALLOWED, allow="POST")

    _assert_exact_json_content_type(request)
    _assert_no_content_encoding(request)
    _assert_content_length_singleton(request)
    # Cheap, no-I/O check of the declared Content-Length -- still before
    # authentication and before any body byte is read.
    validate_declared_content_length(request, MAX_REQUEST_BODY_BYTES)

    principal = await anyio.to_thread.run_sync(
        _authenticate_and_authorize_for_write, request, config
    )

    # Only now, with authentication already successful, is the body
    # actually read. Genuine async socket I/O -- stays directly on the
    # event loop.
    raw_body = await read_bounded_body(request, MAX_REQUEST_BODY_BYTES)

    # Everything else is synchronous CPU/store work (correction pass,
    # item 5) -- offloaded to a worker thread as one unit.
    body, status_code = await anyio.to_thread.run_sync(
        _ingest_report_blocking, config, request_id, raw_body, principal
    )
    return ok_response(body, status_code=status_code)


def _get_report_item_blocking(
    request: Request, config: IngestionApiConfig, request_id: str, ingestion_id: str
) -> dict:
    principal = _authenticate(request, config)
    _authorize(principal, TokenScope.REPORTS_READ)
    record = config.metadata_store.get(principal.tenant_id, ingestion_id)
    if record is None:
        raise ApiError(NOT_FOUND)
    return {
        "ingestion_id": record.ingestion_id,
        "request_id": request_id,
        "received_at": _format_timestamp(record.received_at),
        "report_fingerprint": record.report_fingerprint,
        "status": record.status.value,
    }


def _delete_report_item_blocking(
    request: Request, config: IngestionApiConfig, request_id: str, ingestion_id: str
) -> dict:
    principal = _authenticate(request, config)
    _authorize(principal, TokenScope.REPORTS_DELETE)
    record = config.metadata_store.mark_retired(
        principal.tenant_id, ingestion_id, config.clock(), RetirementReason.CUSTOMER_REQUESTED
    )
    if record is None:
        raise ApiError(NOT_FOUND)
    return {
        "ingestion_id": record.ingestion_id,
        "request_id": request_id,
        "status": record.status.value,
        "reason": record.reason.value if record.reason is not None else None,
        "retired_at": _format_timestamp(record.retired_at) if record.retired_at else None,
        "deleted_at": _format_timestamp(record.deleted_at) if record.deleted_at else None,
    }


async def _handle_report_item(
    request: Request, config: IngestionApiConfig, request_id: str, ingestion_id: str
) -> Response:
    if request.method not in ("GET", "DELETE"):
        raise ApiError(METHOD_NOT_ALLOWED, allow="GET, DELETE")

    # Correction pass, item 5: authentication, authorization, and the
    # metadata-store call are all synchronous -- run as one unit on a
    # worker thread, off the event loop.
    if request.method == "GET":
        body = await anyio.to_thread.run_sync(
            _get_report_item_blocking, request, config, request_id, ingestion_id
        )
    else:
        body = await anyio.to_thread.run_sync(
            _delete_report_item_blocking, request, config, request_id, ingestion_id
        )
    return ok_response(body)


async def _dispatch(request: Request, config: IngestionApiConfig, request_id: str) -> Response:
    """**Correction pass, item 4**: matches only the exact four declared
    routes -- `segments` is never filtered to drop empty entries, so a
    leading/trailing/internal double slash produces a *different* segment
    shape from every declared route rather than silently collapsing into
    one. `Request.url.path` (via `starlette.datastructures.URL`) reflects
    `scope["path"]` verbatim, and the ASGI specification requires a
    conformant server to have already percent-decoded that path -- this
    function decodes nothing itself, so it can never double-decode a
    percent-encoded slash (`%2F`) into a bypass: whatever string arrives
    is matched, and split, exactly once, with no further interpretation.
    `/api/v1/capabilities/`, `/api//v1/capabilities`,
    `/api/v1//capabilities`, and the equivalent trailing/double-slash
    item-path variants are therefore each a distinct shape from their
    real counterpart and fall through to `404 not_found`, never treated
    as an alias and never redirected.
    """
    segments = request.url.path.split("/")

    if segments == ["", "api", "v1", "capabilities"]:
        return await _handle_capabilities(request, config, request_id)
    if segments == ["", "api", "v1", "reports"]:
        return await _handle_reports_collection(request, config, request_id)
    if len(segments) == 5 and segments[:4] == ["", "api", "v1", "reports"] and segments[4] != "":
        return await _handle_report_item(request, config, request_id, segments[4])

    # None of the four exact routes matched. A cleanly-formed, non-empty
    # API-version segment that simply isn't "v1" is the one case that
    # gets the more specific `unsupported_api_version` code -- an empty
    # segment anywhere in that position (from a double slash) is a
    # malformed/aliased path, not a "named but unsupported" version, and
    # falls through to the generic `not_found` below instead.
    if len(segments) >= 3 and segments[0] == "" and segments[1] == "api" and segments[2] != "":
        if segments[2] != "v1":
            raise ApiError(UNSUPPORTED_API_VERSION)
    raise ApiError(NOT_FOUND)


def create_app(config: IngestionApiConfig) -> ASGIApp:
    """Builds a fresh ASGI application closed over `config`. No global or
    module-level mutable state -- two `create_app` calls with independent
    `IngestionApiConfig` instances never share storage, limiters, or a
    clock.
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return  # pragma: no cover -- unreachable, satisfies type checkers only

        if scope["type"] != "http":
            raise RuntimeError("this ASGI app only serves the 'http' and 'lifespan' protocols.")

        request = Request(scope, receive)
        request_id = config.request_id_generator()
        start = time.monotonic()

        try:
            response = await _dispatch(request, config, request_id)
        except ApiError as exc:
            response = error_response(exc, request_id)
        except Exception:
            response = error_response(ApiError(INTERNAL_ERROR), request_id)

        latency_ms = (time.monotonic() - start) * 1000
        log_request_outcome(
            request_id=request_id, http_status=response.status_code, latency_ms=latency_ms
        )
        await response(scope, receive, send)

    return app
