"""Bounded, incremental HTTP request-body reading -- mirrors
`web/worker/readBoundedBody.ts`'s exact two-stage discipline (a declared
`Content-Length` above the limit rejected before any read; actual
streamed bytes hard-stopped the instant they exceed the limit, covering a
chunked or dishonest request alike) at the ASGI layer instead of a
Cloudflare Worker's `ReadableStream`. `request.body()`/`.text()`/
`.json()` (an unbounded whole-body read) must never be called anywhere in
this package.
"""

from __future__ import annotations

import re

from starlette.requests import Request

from .errors import INVALID_REQUEST, PAYLOAD_TOO_LARGE, ApiError

_DIGITS_ONLY_RE = re.compile(rb"^[0-9]+$")


def validate_declared_content_length(request: Request, max_bytes: int) -> None:
    """Cheap, no-I/O check of the declared `Content-Length` header against
    `max_bytes` -- raises `ApiError(INVALID_REQUEST)` for a malformed
    (non-digit, negative, or otherwise unparseable) or duplicated
    `Content-Length` header, or `ApiError(PAYLOAD_TOO_LARGE)` if a
    well-formed single declaration exceeds `max_bytes`. Reads directly
    from the raw ASGI header list, `scope["headers"]`, never
    `Headers.get()`, which would otherwise silently use only the first of
    two conflicting declared lengths.

    **Second correction pass, item 2**: factored out of `read_bounded_body`
    so a caller that must authenticate a request *before* reading its body
    (`app.py`'s `POST /api/v1/reports` handler) can perform this cheap,
    read-free check first, strictly before authentication -- `receive()`
    must never be called for a request this check alone already rejects.
    """
    declared_values = [value for key, value in request.scope["headers"] if key == b"content-length"]
    if len(declared_values) > 1:
        raise ApiError(INVALID_REQUEST)
    if declared_values:
        declared = declared_values[0]
        if not _DIGITS_ONLY_RE.match(declared):
            raise ApiError(INVALID_REQUEST)
        if int(declared) > max_bytes:
            raise ApiError(PAYLOAD_TOO_LARGE)


async def read_bounded_body(request: Request, max_bytes: int) -> bytes:
    """Raises `ApiError(PAYLOAD_TOO_LARGE)` if a declared `Content-Length`
    exceeds `max_bytes` (before any body bytes are read at all), or if
    actual streamed bytes exceed it regardless of what was declared.
    Raises `ApiError(INVALID_REQUEST)` for a malformed (non-digit,
    negative, or otherwise unparseable) `Content-Length` header, or for
    more than one `Content-Length` header (**correction pass, item 3**:
    read directly from the raw ASGI header list, `scope["headers"]`,
    never `Headers.get()`, which would otherwise silently use only the
    first of two conflicting declared lengths -- a caller in `app.py`
    already rejects this ambiguity earlier, before authentication, but
    this function independently re-checks it via
    `validate_declared_content_length`, so it stays correct even if
    invoked from a future call site that skipped that earlier check).
    """
    validate_declared_content_length(request, max_bytes)

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ApiError(PAYLOAD_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)
