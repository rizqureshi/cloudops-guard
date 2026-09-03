"""Compatibility re-export (**Phase 4E**): the authoritative fingerprint
implementation now lives in `cloudops_guard.ingestion.fingerprint`,
relocated for the same reason as `cloudops_guard.ingestion_api.
strict_json` (see that module's own docstring) -- so the CLI uploader and
this HTTP API share one implementation without the uploader depending on
this package's own `api`-extra-only dependencies. `rfc8785` itself moved
from the `api` optional-dependency group into the base runtime
dependencies as part of this relocation. This module keeps the
`cloudops_guard.ingestion_api.fingerprint` import path and
`compute_report_fingerprint` signature working unchanged for existing
code and tests, translating the neutral `errors.ReportFingerprintError`
into this package's own `ApiError(INVALID_REQUEST)` HTTP-boundary
contract -- identical observable behavior to before this move.
"""

from __future__ import annotations

from typing import Any

from cloudops_guard.ingestion.errors import ReportFingerprintError
from cloudops_guard.ingestion.fingerprint import (
    compute_report_fingerprint as _compute_report_fingerprint,
)

from .errors import INVALID_REQUEST, ApiError


def compute_report_fingerprint(
    platform: str, report_schema_version: int | float, report: dict[str, Any]
) -> str:
    """See `cloudops_guard.ingestion.fingerprint.compute_report_fingerprint`
    for the full contract. Raises `ApiError(INVALID_REQUEST)` -- never a
    raw `errors.ReportFingerprintError` or any other exception type.
    """
    try:
        return _compute_report_fingerprint(platform, report_schema_version, report)
    except ReportFingerprintError as exc:
        raise ApiError(INVALID_REQUEST) from exc
