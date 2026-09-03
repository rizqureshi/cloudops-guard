"""Compatibility re-export (**Phase 4E**): the authoritative strict-JSON
implementation now lives in `cloudops_guard.ingestion.strict_json`,
relocated so the CLI uploader and this HTTP API share one implementation
without the uploader depending on this package's own `api`-extra-only
dependencies (starlette/uvicorn/httpx/anyio) -- see
`docs/milestones/v0.4.0-ingestion-api.md`'s Phase 4E entry for the full
rationale. This module keeps the `cloudops_guard.ingestion_api.
strict_json` import path, `strict_decode_json` signature, and
`_MAX_NESTING_DEPTH`/`_MAX_SAFE_INTEGER` constants working unchanged for
existing code and tests, translating the neutral `errors.
StrictJsonRejected` into this package's own `ApiError(INVALID_REQUEST)`
HTTP-boundary contract -- identical observable behavior to before this
move.
"""

from __future__ import annotations

from typing import Any

from cloudops_guard.ingestion.errors import StrictJsonRejected
from cloudops_guard.ingestion.strict_json import _MAX_NESTING_DEPTH, _MAX_SAFE_INTEGER
from cloudops_guard.ingestion.strict_json import strict_decode_json as _strict_decode_json

from .errors import INVALID_REQUEST, ApiError

# Re-exported so `_MAX_NESTING_DEPTH`/`_MAX_SAFE_INTEGER` remain importable
# from this module's own namespace, unchanged, for existing test code.
__all__ = ["_MAX_NESTING_DEPTH", "_MAX_SAFE_INTEGER", "strict_decode_json"]


def strict_decode_json(raw_body: bytes) -> Any:
    """See `cloudops_guard.ingestion.strict_json.strict_decode_json` for
    the full contract. Raises `ApiError(INVALID_REQUEST)` -- never a raw
    `errors.StrictJsonRejected` or any other exception type -- for every
    strict-decode violation that module documents.
    """
    try:
        return _strict_decode_json(raw_body)
    except StrictJsonRejected as exc:
        raise ApiError(INVALID_REQUEST) from exc
