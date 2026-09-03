"""Local report loading and validation for `cloudops-guard upload`
(entirely local, no network access -- see this package's own `__init__.py`
for the pre-confirmation privacy boundary this module is part of).

Reuses the existing Phase 4D report-contract validation
(`cloudops_guard.ingestion_api.report_validation.validate_report`) and
size ceilings (`cloudops_guard.ingestion_api.limits`) rather than
introducing a second, subtly different implementation. Importing those
two modules here does **not** require the `api` optional-dependency
group: `report_validation.py` imports only `pydantic`/`cloudops_guard.
models`/stdlib, and `limits.py` imports nothing beyond `__future__` --
neither pulls in `ingestion_api`'s own `api`-extra-only dependencies
(starlette/uvicorn/httpx/anyio), and Python only imports the exact
submodule requested (plus its package's own import-free `__init__.py`),
never sibling modules like `app.py` that do depend on them.
`tests/test_uploader_dependency_boundary.py` proves this with a real,
fresh-subprocess import-time module-name audit.
"""

from __future__ import annotations

import os
import stat as stat_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cloudops_guard.ingestion.errors import ReportFingerprintError, StrictJsonRejected
from cloudops_guard.ingestion.fingerprint import compute_report_fingerprint
from cloudops_guard.ingestion.strict_json import strict_decode_json
from cloudops_guard.ingestion_api.errors import ApiError
from cloudops_guard.ingestion_api.limits import MAX_REPORT_BYTES
from cloudops_guard.ingestion_api.report_validation import validate_report

from .errors import LocalReportError

#: The envelope's `report_schema_version` this uploader always sends --
#: not read from `report.json` (which has no such field of its own; it is
#: an envelope-level concept the uploader assigns, mirroring the server's
#: own §E.0 contract). `1` is the only value either platform currently
#: supports (`ingestion_api.limits.SUPPORTED_REPORT_SCHEMA_VERSIONS`).
REPORT_SCHEMA_VERSION = 1

REPORT_FILE_NAME = "report.json"

_SEVERITY_KEYS = ("critical", "high", "medium", "low")


@dataclass(frozen=True, slots=True)
class LocalReport:
    """The result of successfully loading and validating a local
    `report.json` -- everything the confirmation summary and the
    eventual upload request need, computed once, locally, before any
    network access.

    **Correction pass, item 6.** `report` carries the report's own,
    potentially sensitive content (finding evidence, resource names,
    arbitrary customer-supplied strings) -- `field(repr=False)` excludes
    it from this dataclass's auto-generated `__repr__`, so `repr(
    LocalReport(...))`, and therefore `repr(UploadResult(...))` (which
    nests it), never leaks report content just because something prints
    or logs the object itself. Every other field here is a filename,
    byte count, or fixed validation-category label -- all safe to show
    in full, and left visible in the repr for that reason.
    """

    platform: str
    report: dict[str, Any] = field(repr=False)
    file_size_bytes: int
    finding_count: int
    severity_counts: dict[str, int]
    fingerprint: str


def _dispatch_platform(decoded: Any) -> str:
    """Deterministic platform dispatch: an own top-level `platform` value
    of exactly `"gitlab"` selects the GitLab model; its absence selects
    the Kubernetes model; any other explicit value is rejected outright.
    Never retries a GitLab validation failure as Kubernetes -- this
    function decides the platform once, before `validate_report` is ever
    called, and that decision is final.

    **Correction pass, item 6**: the rejected `platform` value itself is
    report-supplied content (an attacker/report author fully controls
    it) and is never included in the raised message -- only the fixed,
    generic fact that the marker was unsupported.
    """
    if not isinstance(decoded, dict):
        raise LocalReportError("report.json must contain a JSON object at the top level.")
    if "platform" not in decoded:
        return "kubernetes"
    platform = decoded["platform"]
    if platform == "gitlab":
        return "gitlab"
    raise LocalReportError("report.json has an unsupported platform marker.")


def _severity_counts(findings: Any) -> dict[str, int]:
    counts = dict.fromkeys(_SEVERITY_KEYS, 0)
    if not isinstance(findings, list):
        return counts
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        severity = finding.get("severity")
        if severity in counts:
            counts[severity] += 1
    return counts


def load_and_validate_local_report(report_dir: Path) -> LocalReport:
    """Locates exactly `<report_dir>/report.json`, requires a readable
    regular file, enforces the existing local report size ceiling
    (`MAX_REPORT_BYTES`), strict-JSON decodes it (rejecting invalid
    UTF-8/JSON, duplicate object keys, `NaN`/infinity, malformed Unicode,
    excessive nesting, and numbers outside RFC 8785's domain), dispatches
    platform deterministically, validates the result against the
    existing `AuditReport`/`GitLabAuditReport` contract (including the
    findings-count ceiling, the compact-report-byte-size ceiling, and
    summary-consistency checking), and computes its RFC 8785/SHA-256
    fingerprint. Every step is local; nothing here ever accesses the
    network.

    **Correction pass, item 4.** The original implementation checked the
    file's size via a separate `Path.stat()` call and then read it via a
    separate, unbounded `Path.read_bytes()` call -- two independent
    filesystem operations, each resolving `path` on its own, with an
    unavoidable window between them in which the file on disk could
    change (grow past the size ceiling, be replaced, or stop being a
    regular file) without the size check ever seeing it: a classic
    time-of-check-to-time-of-use (TOCTOU) gap. This is now a single
    `path.open("rb")` call, and every subsequent check -- "is this a
    regular file", "is it within the size ceiling", and the read itself
    -- uses that **one** open file handle's own file descriptor
    (`os.fstat(handle.fileno())`, then `handle.read(...)`), never a
    second, independent path-based lookup. The size ceiling is still
    checked against the declared `st_size` first, as a cheap early
    rejection for an obviously oversized file, but that check is never
    trusted alone: the read itself is bounded to at most
    `MAX_REPORT_BYTES + 1` bytes, and the *actual* number of bytes read
    -- not the earlier, possibly-stale `st_size` -- is what is compared
    against the ceiling and recorded as `file_size_bytes`, so a file that
    changes between the metadata check and the read can never bypass the
    ceiling by presenting misleading metadata.

    Raises `LocalReportError` for every failure mode, and only ever
    that type -- never a raw `OSError`, `StrictJsonRejected`, `ApiError`,
    or `ReportFingerprintError`. Never partially succeeds: a
    `LocalReport` is returned only once every check above has passed.
    """
    path = report_dir / REPORT_FILE_NAME

    try:
        with path.open("rb") as handle:
            try:
                file_stat = os.fstat(handle.fileno())
            except OSError as exc:
                raise LocalReportError(f"unable to read file metadata for {path}: {exc}") from None
            if not stat_module.S_ISREG(file_stat.st_mode):
                raise LocalReportError(f"{path} is not a regular file.")
            if file_stat.st_size > MAX_REPORT_BYTES:
                raise LocalReportError(
                    f"{path} is {file_stat.st_size} bytes, exceeding the "
                    f"{MAX_REPORT_BYTES}-byte local report size limit."
                )
            raw_bytes = handle.read(MAX_REPORT_BYTES + 1)
    except IsADirectoryError:
        raise LocalReportError(f"{path} is not a regular file.") from None
    except FileNotFoundError:
        raise LocalReportError(f"{path} does not exist or is not a regular file.") from None
    except OSError as exc:
        raise LocalReportError(f"unable to read {path}: {exc}") from None

    if len(raw_bytes) > MAX_REPORT_BYTES:
        raise LocalReportError(
            f"{path} is {len(raw_bytes)} bytes, exceeding the "
            f"{MAX_REPORT_BYTES}-byte local report size limit."
        )
    file_size = len(raw_bytes)

    try:
        decoded = strict_decode_json(raw_bytes)
    except StrictJsonRejected:
        # Correction pass, item 6: StrictJsonRejected's own message can
        # itself carry report-supplied content (e.g. a duplicate object
        # key's literal name) -- never forwarded here. Every strict-JSON
        # rejection, regardless of its specific cause, maps to this one
        # fixed, generic category.
        raise LocalReportError("report.json failed strict JSON validation.") from None

    platform = _dispatch_platform(decoded)

    try:
        validate_report(platform, REPORT_SCHEMA_VERSION, decoded)
    except ApiError as exc:
        raise LocalReportError(
            f"report.json failed report-contract validation ({exc.code})."
        ) from None

    try:
        fingerprint = compute_report_fingerprint(platform, REPORT_SCHEMA_VERSION, decoded)
    except ReportFingerprintError:
        # Correction pass, item 6: as with StrictJsonRejected above,
        # never forward this exception's own message -- it can describe
        # the specific report-supplied value that failed to canonicalize.
        raise LocalReportError("unable to compute report fingerprint.") from None

    findings = decoded.get("findings")
    return LocalReport(
        platform=platform,
        report=decoded,
        file_size_bytes=file_size,
        finding_count=len(findings) if isinstance(findings, list) else 0,
        severity_counts=_severity_counts(findings),
        fingerprint=fingerprint,
    )
