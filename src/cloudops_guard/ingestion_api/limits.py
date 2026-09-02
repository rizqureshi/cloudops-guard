"""Fixed protocol-level size and count ceilings
(`docs/milestones/v0.4.0-ingestion-api.md` §D) -- part of the `/api/v1`
HTTP contract itself, not a per-deployment configuration value, so these
are plain module constants rather than `IngestionApiConfig` fields.
`GET /api/v1/capabilities` (`app.py`) reports all three by name.
"""

from __future__ import annotations

#: The maximum size of the `report` field's own value: the UTF-8 byte
#: length of a *compact* re-serialization of the parsed value (see
#: `report_validation.compact_report_json_bytes`) -- never the RFC 8785
#: canonical form used for fingerprinting, and never a raw substring of
#: the request body.
MAX_REPORT_BYTES = 10 * 1024 * 1024  # 10,485,760

#: The maximum size of the entire HTTP request body -- `MAX_REPORT_BYTES`
#: plus a fixed 4 KiB envelope-overhead allowance. Enforced first, on raw
#: wire bytes, before any JSON parsing (`bounded_body.read_bounded_body`).
MAX_REQUEST_BODY_BYTES = MAX_REPORT_BYTES + 4096  # 10,489,856

#: Checked before expensive per-finding Pydantic validation
#: (`report_validation.py`), mirroring
#: `web/src/features/report-import/constants.ts`'s existing browser-side
#: ceiling of the same name and value.
MAX_FINDINGS_PER_REPORT = 10_000

#: The only `report_schema_version` value either platform currently
#: supports (§D) -- the field exists so a future report-contract change
#: can add a value here without an API version bump.
SUPPORTED_REPORT_SCHEMA_VERSIONS: dict[str, tuple[int, ...]] = {
    "kubernetes": (1,),
    "gitlab": (1,),
}
