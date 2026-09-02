"""Server-side report validation (`docs/milestones/v0.4.0-ingestion-api.md`
§E.2/§H): reuses the existing, released `AuditReport`/`GitLabAuditReport`
Pydantic models directly -- no third, parallel schema. Mirrors
`web/src/features/report-import/parsers.ts`'s
`assertFindingsCountWithinLimit`/`recomputeAndVerifySummary` server-side,
in Python, against the same two guarantees: a findings-count ceiling
checked before expensive per-finding validation, and a supplied `summary`
that must match what the findings themselves imply -- never trusted as
given.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from cloudops_guard.models import (
    AuditReport,
    AuditSummary,
    GitLabAuditReport,
    GitLabFinding,
    Severity,
)

from .errors import (
    INVALID_REPORT,
    PAYLOAD_TOO_LARGE,
    UNSUPPORTED_REPORT_SCHEMA_VERSION,
    ApiError,
)
from .limits import MAX_FINDINGS_PER_REPORT, MAX_REPORT_BYTES, SUPPORTED_REPORT_SCHEMA_VERSIONS


def compact_report_json_bytes(report: dict[str, Any]) -> bytes:
    """The exact, documented compact serialization used both for the
    `MAX_REPORT_BYTES` size check and for the bytes actually persisted to
    `ReportBlobStore` -- `json.dumps(report, separators=(",", ":"),
    ensure_ascii=False)`, UTF-8 encoded: valid JSON with no insignificant
    whitespace, non-ASCII characters kept as literal UTF-8 rather than
    `\\uXXXX`-escaped (so the measured/stored byte count reflects the
    content's actual UTF-8 size, not an inflated ASCII-escaped one).
    Deterministic and preserves the received report's parsed JSON value
    exactly (including original key order) -- never a normalized Pydantic
    re-dump, which could silently drop, reorder, or reformat fields the
    original request actually sent. Distinct from the RFC 8785 canonical
    form `fingerprint.py` uses -- these are two independent,
    purpose-specific representations of the same parsed value.
    """
    return json.dumps(report, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _assert_findings_count_within_limit(report: dict[str, Any]) -> None:
    findings = report.get("findings")
    if isinstance(findings, list) and len(findings) > MAX_FINDINGS_PER_REPORT:
        raise ApiError(INVALID_REPORT)


def _assert_report_bytes_within_limit(report: dict[str, Any]) -> None:
    if len(compact_report_json_bytes(report)) > MAX_REPORT_BYTES:
        raise ApiError(PAYLOAD_TOO_LARGE)


def _severity_counts(severities: list[Severity]) -> dict[Severity, int]:
    counts = {Severity.CRITICAL: 0, Severity.HIGH: 0, Severity.MEDIUM: 0, Severity.LOW: 0}
    for severity in severities:
        counts[severity] += 1
    return counts


def _assert_summary_matches(summary: AuditSummary, severities: list[Severity]) -> None:
    counts = _severity_counts(severities)
    if (
        summary.critical != counts[Severity.CRITICAL]
        or summary.high != counts[Severity.HIGH]
        or summary.medium != counts[Severity.MEDIUM]
        or summary.low != counts[Severity.LOW]
    ):
        raise ApiError(INVALID_REPORT)


def validate_report(
    platform: str, report_schema_version: int | float, report: dict[str, Any]
) -> None:
    """Raises `ApiError(UNSUPPORTED_REPORT_SCHEMA_VERSION)`,
    `ApiError(INVALID_REPORT)`, or `ApiError(PAYLOAD_TOO_LARGE)` -- never
    lets an invalid or oversized report reach fingerprinting or storage.
    `platform` is assumed already validated by `envelope.parse_envelope`
    (exactly `"kubernetes"` or `"gitlab"`).
    """
    if report_schema_version not in SUPPORTED_REPORT_SCHEMA_VERSIONS[platform]:
        raise ApiError(UNSUPPORTED_REPORT_SCHEMA_VERSION)

    # Cheap checks (a list length; a compact re-serialization) before the
    # expensive one (full per-finding Pydantic validation) below.
    _assert_findings_count_within_limit(report)
    _assert_report_bytes_within_limit(report)

    try:
        if platform == "kubernetes":
            parsed_k8s = AuditReport(**report)
            _assert_summary_matches(parsed_k8s.summary, [f.severity for f in parsed_k8s.findings])
        else:
            parsed_gitlab = GitLabAuditReport(**report)
            findings: list[GitLabFinding] = parsed_gitlab.findings
            _assert_summary_matches(parsed_gitlab.summary, [f.severity for f in findings])
    except ValidationError as exc:
        raise ApiError(INVALID_REPORT) from exc
