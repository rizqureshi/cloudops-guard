"""Sanitized, allowlist-only structured logging
(`docs/milestones/v0.4.0-ingestion-api.md` §C's logging requirements,
task 14). `log_request_outcome`'s signature is a closed set of typed
parameters -- there is no `**kwargs` or free-form `extra` escape hatch,
so report content, finding text, a bearer token, or any other
non-allowlisted field cannot be logged through this module even by
accident. This is the only function anywhere in this package that writes
a log line.
"""

from __future__ import annotations

import json
import logging

_logger = logging.getLogger("cloudops_guard.ingestion_api")


def log_request_outcome(
    *,
    request_id: str,
    http_status: int,
    latency_ms: float,
    ingestion_id: str | None = None,
    tenant_id: str | None = None,
    report_fingerprint: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    byte_count: int | None = None,
) -> None:
    """Logs exactly the fields §C allows: `request_id`, `ingestion_id`, an
    opaque `tenant_id`, `report_fingerprint`, lifecycle `status`/`reason`,
    `byte_count`, HTTP status, and latency -- never report content,
    finding text, a resource/cluster/project name, a bearer token value,
    or any other request-body field. Optional fields are omitted from the
    logged line entirely when not given, rather than logged as `null`.
    """
    fields: dict[str, object] = {
        "request_id": request_id,
        "http_status": http_status,
        "latency_ms": round(latency_ms, 3),
    }
    if ingestion_id is not None:
        fields["ingestion_id"] = ingestion_id
    if tenant_id is not None:
        fields["tenant_id"] = tenant_id
    if report_fingerprint is not None:
        fields["report_fingerprint"] = report_fingerprint
    if status is not None:
        fields["status"] = status
    if reason is not None:
        fields["reason"] = reason
    if byte_count is not None:
        fields["byte_count"] = byte_count
    _logger.info(json.dumps(fields, sort_keys=True))
