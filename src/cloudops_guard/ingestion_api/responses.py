"""Fixed JSON response envelopes (`docs/milestones/v0.4.0-ingestion-api.md`
§E). Every response is `application/json`; every error response is
exactly `{"ok": false, "error": "<code>", "request_id": "<id>"}` -- no
extra fields, ever. These two functions are the only place a response is
constructed anywhere in this package.
"""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

from .errors import ApiError


def error_response(error: ApiError, request_id: str) -> JSONResponse:
    headers = {"Allow": error.allow} if error.allow is not None else None
    return JSONResponse(
        {"ok": False, "error": error.code, "request_id": request_id},
        status_code=error.http_status,
        headers=headers,
    )


def ok_response(body: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    """`body` must not itself define `ok` -- this function is the only
    place that field is ever set, always `True` (an error always goes
    through `error_response` instead).
    """
    if "ok" in body:
        raise ValueError("body must not itself define 'ok'.")
    return JSONResponse({"ok": True, **body}, status_code=status_code)
