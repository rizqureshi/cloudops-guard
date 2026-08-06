"""Regression protection for the released v0.1.0 Kubernetes report contract.

These tests exist specifically to catch any accidental change to the
Kubernetes `Finding`/`AuditReport` models or `report.json`/`report.html`
output introduced while adding the platform-specific GitLab models in
v0.2.0 Phase 1 (see `docs/milestones/v0.2.0-gitlab-audit.md`). They must keep
passing, unmodified, for as long as the Kubernetes report format is a
released contract.

The golden fixtures under `tests/fixtures/golden_kubernetes_report.*` were
generated once from the unmodified v0.1.0 baseline behavior using fixed,
entirely synthetic values (no real hostnames, credentials, or cluster data).
A failing byte-for-byte comparison against them means the Kubernetes report
output changed and must be explained -- never "fixed" by regenerating the
fixture to match new output.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from cloudops_guard.models import AuditReport, AuditSummary, Finding, ResourceKind, Severity
from cloudops_guard.reports.generator import generate_reports

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_JSON = FIXTURES_DIR / "golden_kubernetes_report.json"
GOLDEN_HTML = FIXTURES_DIR / "golden_kubernetes_report.html"

NOW = dt.datetime(2026, 1, 15, 9, 30, 0, tzinfo=dt.UTC)


def _golden_report() -> AuditReport:
    """Build the exact deterministic report the golden fixtures were generated from."""
    findings = [
        Finding(
            check_id="K8S-RES-001",
            title="Container has no CPU request",
            severity=Severity.MEDIUM,
            cluster_context="prod-demo-cluster",
            namespace="payments",
            resource_kind=ResourceKind.DEPLOYMENT,
            resource_name="checkout-api",
            container_name="api",
            evidence="Container 'api' (image: example.com/checkout-api:2.3.1) does not set "
            "resources.requests.cpu",
            impact="Without a CPU request the scheduler cannot reserve capacity for this "
            "container.",
            recommendation="Set resources.requests.cpu based on observed usage.",
            auto_remediable=False,
            audited_at=NOW,
        ),
        Finding(
            check_id="K8S-RES-004",
            title="Container has no memory limit",
            severity=Severity.HIGH,
            cluster_context="prod-demo-cluster",
            namespace="payments",
            resource_kind=ResourceKind.DEPLOYMENT,
            resource_name="checkout-api",
            container_name="api",
            evidence="Container 'api' (image: example.com/checkout-api:2.3.1) does not set "
            "resources.limits.memory",
            impact="Without a memory limit this container can consume unbounded memory.",
            recommendation="Set resources.limits.memory to cap worst-case memory usage.",
            auto_remediable=False,
            audited_at=NOW,
        ),
        Finding(
            check_id="K8S-IMG-001",
            title="Container image uses a mutable tag",
            severity=Severity.HIGH,
            cluster_context="prod-demo-cluster",
            namespace="payments",
            resource_kind=ResourceKind.POD,
            resource_name="wébapp-batch-7f9c2",
            container_name="worker",
            evidence="Container 'worker' uses image 'example.com/batch:latest' (tag: latest)",
            impact="A mutable tag such as 'latest' undermines reproducibility and rollback safety.",
            recommendation="Pin the image to a specific version tag at minimum.",
            auto_remediable=False,
            audited_at=NOW,
        ),
        Finding(
            check_id="K8S-REL-001",
            title="Container has an excessive restart count",
            severity=Severity.HIGH,
            cluster_context="prod-demo-cluster",
            namespace="payments",
            resource_kind=ResourceKind.POD,
            resource_name="cache-5d8f1",
            container_name=None,
            evidence="Container 'redis' in pod 'cache-5d8f1' has restarted 9 time(s) "
            "(cumulative for the pod's current lifetime), meeting or exceeding the "
            "configured threshold of 5",
            impact="Frequent restarts typically indicate crash looping or resource exhaustion.",
            recommendation="Inspect this container's events and state for the root cause.",
            auto_remediable=False,
            audited_at=NOW,
        ),
    ]
    summary = AuditSummary(critical=0, high=3, medium=1, low=0)
    return AuditReport(
        cluster_context="prod-demo-cluster",
        namespace_filter="payments",
        generated_at=NOW,
        findings=findings,
        summary=summary,
    )


# --- 1 & 2: exact ordered field names ----------------------------------------


def test_kubernetes_finding_field_order_is_unchanged() -> None:
    assert list(Finding.model_fields) == [
        "check_id",
        "title",
        "severity",
        "cluster_context",
        "namespace",
        "resource_kind",
        "resource_name",
        "container_name",
        "evidence",
        "impact",
        "recommendation",
        "auto_remediable",
        "audited_at",
    ]


def test_kubernetes_audit_report_field_order_is_unchanged() -> None:
    assert list(AuditReport.model_fields) == [
        "cluster_context",
        "namespace_filter",
        "generated_at",
        "findings",
        "summary",
    ]


# --- 3: exact existing ResourceKind values -----------------------------------


def test_resource_kind_values_are_unchanged() -> None:
    assert {member.name: member.value for member in ResourceKind} == {
        "NAMESPACE": "Namespace",
        "POD": "Pod",
        "DEPLOYMENT": "Deployment",
    }


# --- 4 & 6: deterministic serialization, byte-for-byte against golden fixtures


def test_golden_json_report_matches_fixture_byte_for_byte(tmp_path: Path) -> None:
    json_path, _ = generate_reports(_golden_report(), tmp_path)
    actual = json_path.read_bytes()
    expected = GOLDEN_JSON.read_bytes()
    assert actual == expected


def test_golden_html_report_matches_fixture_byte_for_byte(tmp_path: Path) -> None:
    _, html_path = generate_reports(_golden_report(), tmp_path)
    actual = html_path.read_bytes()
    expected = GOLDEN_HTML.read_bytes()
    assert actual == expected


# --- 5: existing Kubernetes report JSON still validates back into AuditReport


def test_golden_json_fixture_validates_back_into_audit_report() -> None:
    raw = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    reloaded = AuditReport.model_validate(raw)

    assert reloaded == _golden_report()


# --- 6 (cont'd): the golden HTML fixture itself remains JavaScript-free -----


def test_golden_html_fixture_is_javascript_free() -> None:
    html = GOLDEN_HTML.read_text(encoding="utf-8")
    assert "<script" not in html.lower()
    assert "<!doctype html>" in html.lower()
