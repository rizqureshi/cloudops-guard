"""Renders an AuditReport/GitLabAuditReport to report.json and report.html.

Report generation depends only on the AuditReport/GitLabAuditReport models,
not on how a report was collected or evaluated, so it can be tested against
hand-built reports.

Kubernetes (`write_json_report`/`write_html_report`/`generate_reports`) and
GitLab (`write_gitlab_json_report`/`write_gitlab_html_report`/
`generate_gitlab_reports`, v0.2.0 Phase 2D-B) are two separate, explicit
rendering paths through this same module -- not a shared, loosely typed
`object` handler and not a broad report abstraction. They share only the
small platform-neutral mechanics below (`_atomic_write`, `_environment`,
`_SEVERITY_ORDER`) that were already generic. The Kubernetes functions, the
existing `report.html.j2` template, and the released `report.json`/
`report.html` byte-for-byte output are unchanged by the GitLab addition.
The already-built `GitLabAuditReport` it is given is the sole audit-data
input to GitLab report generation -- it performs no HTTP,
environment-variable, collection, or evaluation access itself. Its
filesystem/package-resource activity is limited to: loading the packaged
GitLab HTML template (via Jinja's `PackageLoader`, in `_environment()`);
creating the output directory and any missing parents; atomically writing
and, if a report already exists, replacing `report.json` and `report.html`;
and cleaning up a leftover temp file after a failed write (`_atomic_write`).
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path

from jinja2 import Environment, PackageLoader

from cloudops_guard.models import AuditReport, GitLabAuditReport

_SEVERITY_ORDER = ("critical", "high", "medium", "low")


def _environment() -> Environment:
    # autoescape=True (rather than select_autoescape, which matches on the
    # ".html" extension and would miss our ".html.j2" template name) since
    # every template this environment renders is HTML.
    return Environment(
        loader=PackageLoader("cloudops_guard.reports", "templates"),
        autoescape=True,
    )


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path via a same-directory temp file + atomic rename.

    This ensures a crash or write failure part-way through never leaves a
    truncated report.json/report.html behind — the final path either has its
    previous content or the new content, never a partial write. Any temp file
    left behind by a failed write is cleaned up before the error propagates.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def write_json_report(report: AuditReport, output_dir: Path) -> Path:
    path = output_dir / "report.json"
    _atomic_write(path, report.model_dump_json(indent=2) + "\n")
    return path


def write_html_report(report: AuditReport, output_dir: Path) -> Path:
    env = _environment()
    template = env.get_template("report.html.j2")
    findings_by_severity = {
        severity: [f for f in report.findings if f.severity.value == severity]
        for severity in _SEVERITY_ORDER
    }
    html = template.render(
        report=report,
        severity_order=_SEVERITY_ORDER,
        findings_by_severity=findings_by_severity,
    )
    path = output_dir / "report.html"
    _atomic_write(path, html)
    return path


def generate_reports(report: AuditReport, output_dir: Path) -> tuple[Path, Path]:
    """Write report.json and report.html into output_dir, creating it if needed."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json_report(report, output_dir)
    html_path = write_html_report(report, output_dir)
    return json_path, html_path


# --- GitLab (v0.2.0 Phase 2D-B): separate report rendering path -------------------


def write_gitlab_json_report(report: GitLabAuditReport, output_dir: Path) -> Path:
    """Serialize `report` straight through Pydantic -- no wrapper, no extra fields.

    `model_dump_json` preserves `GitLabAuditReport`'s declared field order
    and each finding's declared field order exactly, and reproduces
    `report.findings` in the exact order it was given -- this function adds
    nothing to and reorders nothing in that output; it only appends the
    trailing newline and writes it atomically.
    """
    path = output_dir / "report.json"
    _atomic_write(path, report.model_dump_json(indent=2) + "\n")
    return path


def write_gitlab_html_report(report: GitLabAuditReport, output_dir: Path) -> Path:
    """Render `report` through the separate `gitlab_report.html.j2` template.

    Groups findings by severity (`_SEVERITY_ORDER`: critical, high, medium,
    low) for the template exactly as `write_html_report` does for
    Kubernetes, preserving each group's relative finding order. Every
    displayed value is rendered through Jinja's `autoescape=True` (see
    `_environment`), so untrusted content (a GitLab-provided project path,
    resource name, evidence string, etc.) is never rendered as active HTML.
    """
    env = _environment()
    template = env.get_template("gitlab_report.html.j2")
    findings_by_severity = {
        severity: [f for f in report.findings if f.severity.value == severity]
        for severity in _SEVERITY_ORDER
    }
    html = template.render(
        report=report,
        severity_order=_SEVERITY_ORDER,
        findings_by_severity=findings_by_severity,
    )
    path = output_dir / "report.html"
    _atomic_write(path, html)
    return path


def generate_gitlab_reports(report: GitLabAuditReport, output_dir: Path) -> tuple[Path, Path]:
    """Write report.json and report.html for `report` into output_dir.

    Mirrors `generate_reports` exactly: creates `output_dir` (and any
    missing parents) if needed, writes both files via the same
    same-directory-temp-file-plus-atomic-rename mechanism
    (`_atomic_write`), and returns the two final paths. Accepts only
    `report` and `output_dir` -- no client, collector, token, URL, or
    evaluator argument -- and performs no collection or evaluation itself.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_gitlab_json_report(report, output_dir)
    html_path = write_gitlab_html_report(report, output_dir)
    return json_path, html_path
