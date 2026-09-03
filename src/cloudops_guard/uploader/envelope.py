"""Request-envelope construction for `cloudops-guard upload`.

Builds the exact closed envelope the ingestion API's own
`ingestion_api.envelope.parse_envelope` accepts -- `platform`,
`report_schema_version`, `report`, nothing else -- and serializes it
deterministically to bounded UTF-8 JSON bytes. Never invoked before local
validation, fingerprinting, and (outside `--dry-run`) confirmation have
already succeeded.
"""

from __future__ import annotations

import json
from typing import Any

from cloudops_guard.ingestion_api.limits import MAX_REQUEST_BODY_BYTES

from .errors import LocalReportError
from .local_report import REPORT_SCHEMA_VERSION


def build_request_body(platform: str, report: dict[str, Any]) -> bytes:
    """Serializes the exact envelope
    `{"platform": platform, "report_schema_version": 1, "report": report}`
    -- no additional fields -- to compact, deterministic UTF-8 JSON bytes
    (`separators=(",", ":")`, no insignificant whitespace; the same
    Python `dict` always serializes to the same bytes, since `report`'s
    own key order is whatever `strict_json.strict_decode_json` produced
    from the original file, itself insertion-order-preserving).
    `allow_nan=False` makes serialization itself fail loudly (`ValueError`,
    wrapped below) rather than emit non-standard `NaN`/`Infinity` tokens,
    as a defensive backstop -- `report` has already been strict-JSON
    decoded and contract-validated by this point, so a non-finite value
    should never actually reach this function.

    Enforces `MAX_REQUEST_BODY_BYTES` on the serialized byte length,
    *after* serialization, before this function returns -- the caller
    must never send bytes this function itself would reject. Raises
    `LocalReportError` for a serialization failure or an oversized
    envelope; never returns a body exceeding the limit.
    """
    envelope = {
        "platform": platform,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "report": report,
    }
    try:
        body = json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except ValueError as exc:
        raise LocalReportError(f"unable to serialize the request envelope: {exc}") from None
    body_bytes = body.encode("utf-8")
    if len(body_bytes) > MAX_REQUEST_BODY_BYTES:
        raise LocalReportError(
            f"the serialized request envelope is {len(body_bytes)} bytes, exceeding the "
            f"{MAX_REQUEST_BODY_BYTES}-byte request size limit."
        )
    return body_bytes
