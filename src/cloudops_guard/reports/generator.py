"""Renders an AuditReport to report.json and report.html.

Report generation depends only on the AuditReport model, not on how it was
collected or evaluated, so it can be tested against hand-built reports.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, PackageLoader

from cloudops_guard.models import AuditReport

_SEVERITY_ORDER = ("critical", "high", "medium", "low")


def _environment() -> Environment:
    # autoescape=True (rather than select_autoescape, which matches on the
    # ".html" extension and would miss our ".html.j2" template name) since
    # every template this environment renders is HTML.
    return Environment(
        loader=PackageLoader("cloudops_guard.reports", "templates"),
        autoescape=True,
    )


def write_json_report(report: AuditReport, output_dir: Path) -> Path:
    path = output_dir / "report.json"
    path.write_text(report.model_dump_json(indent=2) + "\n")
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
    path.write_text(html)
    return path


def generate_reports(report: AuditReport, output_dir: Path) -> tuple[Path, Path]:
    """Write report.json and report.html into output_dir, creating it if needed."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json_report(report, output_dir)
    html_path = write_html_report(report, output_dir)
    return json_path, html_path
