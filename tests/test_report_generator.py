"""Tests for JSON and HTML report generation."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from cloudops_guard.models import AuditReport, AuditSummary, Finding, ResourceKind, Severity
from cloudops_guard.reports.generator import generate_reports

NOW = dt.datetime(2026, 7, 27, 12, 0, tzinfo=dt.UTC)


def make_finding(
    check_id: str = "K8S-RES-001",
    severity: Severity = Severity.MEDIUM,
    resource_name: str = "web",
    namespace: str = "default",
) -> Finding:
    return Finding(
        check_id=check_id,
        title="Container has no CPU request",
        severity=severity,
        cluster_context="test-ctx",
        namespace=namespace,
        resource_kind=ResourceKind.DEPLOYMENT,
        resource_name=resource_name,
        container_name="app",
        evidence="Container 'app' does not set resources.requests.cpu",
        impact="Scheduler cannot reserve CPU capacity.",
        recommendation="Set resources.requests.cpu.",
        auto_remediable=False,
        audited_at=NOW,
    )


def make_report(findings: list[Finding] | None = None) -> AuditReport:
    findings = findings if findings is not None else [make_finding()]
    summary = AuditSummary()
    for f in findings:
        setattr(summary, f.severity.value, getattr(summary, f.severity.value) + 1)
    return AuditReport(
        cluster_context="test-ctx",
        namespace_filter=None,
        generated_at=NOW,
        findings=findings,
        summary=summary,
    )


def test_generate_reports_writes_both_files(tmp_path: Path) -> None:
    report = make_report()
    json_path, html_path = generate_reports(report, tmp_path)
    assert json_path == tmp_path / "report.json"
    assert html_path == tmp_path / "report.html"
    assert json_path.is_file()
    assert html_path.is_file()


def test_generate_reports_creates_output_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir"
    generate_reports(make_report(), nested)
    assert (nested / "report.json").is_file()
    assert (nested / "report.html").is_file()


def test_json_report_round_trips_through_pydantic(tmp_path: Path) -> None:
    report = make_report(
        [
            make_finding(check_id="K8S-RES-001", severity=Severity.MEDIUM),
            make_finding(check_id="K8S-IMG-001", severity=Severity.HIGH),
        ]
    )
    json_path, _ = generate_reports(report, tmp_path)

    raw = json.loads(json_path.read_text())
    reloaded = AuditReport.model_validate(raw)

    assert reloaded.cluster_context == "test-ctx"
    assert len(reloaded.findings) == 2
    assert reloaded.summary.medium == 1
    assert reloaded.summary.high == 1
    assert reloaded.summary.total == 2


def test_html_report_contains_no_javascript(tmp_path: Path) -> None:
    _, html_path = generate_reports(make_report(), tmp_path)
    html = html_path.read_text()
    assert "<script" not in html.lower()
    assert "<!doctype html>" in html.lower()


def test_html_report_shows_finding_details_and_severity_counts(tmp_path: Path) -> None:
    report = make_report(
        [
            make_finding(
                check_id="K8S-RES-001", severity=Severity.MEDIUM, resource_name="checkout"
            ),
            make_finding(check_id="K8S-IMG-001", severity=Severity.HIGH, resource_name="checkout"),
        ]
    )
    _, html_path = generate_reports(report, tmp_path)
    html = html_path.read_text()

    assert "checkout" in html
    assert "K8S-RES-001" in html
    assert "K8S-IMG-001" in html
    assert "test-ctx" in html


def test_html_report_escapes_untrusted_resource_names(tmp_path: Path) -> None:
    malicious = make_finding(resource_name="<script>alert(1)</script>")
    report = make_report([malicious])
    _, html_path = generate_reports(report, tmp_path)
    html = html_path.read_text()

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_html_report_handles_empty_findings(tmp_path: Path) -> None:
    _, html_path = generate_reports(make_report([]), tmp_path)
    html = html_path.read_text()
    assert "No findings" in html


def test_json_report_handles_empty_findings(tmp_path: Path) -> None:
    json_path, _ = generate_reports(make_report([]), tmp_path)
    raw = json.loads(json_path.read_text())
    assert raw["findings"] == []
    assert raw["summary"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}


def test_reports_handle_unicode_content(tmp_path: Path) -> None:
    finding = make_finding(resource_name="wébapp-中文-\U0001f680")
    json_path, html_path = generate_reports(make_report([finding]), tmp_path)

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    assert raw["findings"][0]["resource_name"] == "wébapp-中文-\U0001f680"

    html = html_path.read_text(encoding="utf-8")
    assert "wébapp-中文-\U0001f680" in html


def test_generate_reports_replaces_existing_files(tmp_path: Path) -> None:
    generate_reports(make_report([make_finding(check_id="K8S-RES-001")]), tmp_path)
    first_json = (tmp_path / "report.json").read_text()

    generate_reports(make_report([make_finding(check_id="K8S-IMG-001")]), tmp_path)
    second_json = (tmp_path / "report.json").read_text()

    assert first_json != second_json
    assert "K8S-IMG-001" in second_json
    assert "K8S-RES-001" not in second_json


def test_write_failure_does_not_leave_a_truncated_report(tmp_path: Path, monkeypatch) -> None:
    generate_reports(make_report([make_finding(check_id="K8S-RES-001")]), tmp_path)
    original_json = (tmp_path / "report.json").read_text()
    original_html = (tmp_path / "report.html").read_text()

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr("cloudops_guard.reports.generator.os.replace", boom)

    with pytest.raises(OSError):
        generate_reports(make_report([make_finding(check_id="K8S-IMG-001")]), tmp_path)

    # Final files are untouched -- never partially overwritten.
    assert (tmp_path / "report.json").read_text() == original_json
    assert (tmp_path / "report.html").read_text() == original_html
    # No leftover temp files from the failed write attempt.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".report")]
    assert leftovers == []


def test_report_template_is_packaged() -> None:
    """The HTML template must be resolvable through the installed package,

    not just present in the source tree (a build misconfiguration could
    otherwise drop it from an installed wheel silently).
    """
    from importlib.resources import files

    template_path = files("cloudops_guard.reports") / "templates" / "report.html.j2"
    assert template_path.is_file()
