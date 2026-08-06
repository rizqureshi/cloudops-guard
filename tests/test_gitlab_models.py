"""Tests for the additive, platform-specific GitLab models (v0.2.0 Phase 1).

These models are not wired into any collector, HTTP client, check, or CLI
command yet -- see `docs/milestones/v0.2.0-gitlab-audit.md`. These tests only
prove the model shapes, validation, and isolation from the Kubernetes models
are correct. All values used are synthetic and non-sensitive.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from pydantic import ValidationError

from cloudops_guard.models import (
    AuditReport,
    Finding,
    GitLabAuditReport,
    GitLabFinding,
    GitLabResourceKind,
    ResourceKind,
    Severity,
)

NOW = dt.datetime(2026, 2, 1, 12, 0, tzinfo=dt.UTC)


def make_gitlab_finding(**overrides: object) -> GitLabFinding:
    defaults: dict[str, object] = {
        "check_id": "GL-BR-001",
        "title": "Default branch is not protected",
        "severity": Severity.HIGH,
        "project_path": "group/subgroup/project",
        "resource_kind": GitLabResourceKind.PROTECTED_BRANCH,
        "resource_name": "main",
        "evidence": "No protection rule matched the default branch.",
        "impact": "The default branch can be force-pushed or deleted by anyone with push access.",
        "recommendation": "Create a protected-branch rule for the default branch.",
        "auto_remediable": False,
        "audited_at": NOW,
    }
    defaults.update(overrides)
    return GitLabFinding(**defaults)


def make_gitlab_report(**overrides: object) -> GitLabAuditReport:
    defaults: dict[str, object] = {
        "gitlab_url": "https://gitlab.example.com",
        "project_id": 42,
        "project_path": "group/subgroup/project",
        "default_branch": "main",
        "generated_at": NOW,
    }
    defaults.update(overrides)
    return GitLabAuditReport(**defaults)


# --- Valid construction across resource kinds --------------------------------


@pytest.mark.parametrize(
    "resource_kind",
    [
        GitLabResourceKind.PROJECT,
        GitLabResourceKind.PROTECTED_BRANCH,
        GitLabResourceKind.CI_JOB,
        GitLabResourceKind.CI_SERVICE,
    ],
)
def test_gitlab_finding_constructs_for_every_resource_kind(
    resource_kind: GitLabResourceKind,
) -> None:
    finding = make_gitlab_finding(resource_kind=resource_kind)
    assert finding.resource_kind == resource_kind


def test_gitlab_resource_kind_values() -> None:
    assert {member.name: member.value for member in GitLabResourceKind} == {
        "PROJECT": "Project",
        "PROTECTED_BRANCH": "ProtectedBranch",
        "CI_JOB": "CIJob",
        "CI_SERVICE": "CIService",
    }


def test_gitlab_finding_job_name_defaults_to_none() -> None:
    finding = make_gitlab_finding()
    assert finding.job_name is None


def test_gitlab_finding_accepts_a_job_name() -> None:
    finding = make_gitlab_finding(
        resource_kind=GitLabResourceKind.CI_SERVICE,
        resource_name="postgres",
        job_name="build",
    )
    assert finding.job_name == "build"


def test_gitlab_finding_job_name_rejects_empty_string() -> None:
    with pytest.raises(ValidationError):
        make_gitlab_finding(job_name="")


def test_gitlab_audit_report_constructs_with_required_fields() -> None:
    report = make_gitlab_report()
    assert report.gitlab_url == "https://gitlab.example.com"
    assert report.project_id == 42
    assert report.project_path == "group/subgroup/project"
    assert report.default_branch == "main"
    assert report.findings == []
    assert report.summary.total == 0


def test_gitlab_audit_report_platform_defaults_to_gitlab() -> None:
    report = make_gitlab_report()
    assert report.platform == "gitlab"


def test_gitlab_audit_report_platform_rejects_other_values() -> None:
    with pytest.raises(ValidationError):
        make_gitlab_report(platform="kubernetes")


# --- Field ordering (locks the new GitLab JSON field order) ------------------


def test_gitlab_finding_field_order() -> None:
    assert list(GitLabFinding.model_fields) == [
        "check_id",
        "title",
        "severity",
        "project_path",
        "resource_kind",
        "resource_name",
        "job_name",
        "evidence",
        "impact",
        "recommendation",
        "auto_remediable",
        "audited_at",
    ]


def test_gitlab_audit_report_field_order() -> None:
    assert list(GitLabAuditReport.model_fields) == [
        "platform",
        "gitlab_url",
        "project_id",
        "project_path",
        "default_branch",
        "generated_at",
        "findings",
        "summary",
    ]


# --- Serialization and round-trip --------------------------------------------


def test_gitlab_finding_json_round_trip() -> None:
    finding = make_gitlab_finding()
    raw = json.loads(finding.model_dump_json())
    reloaded = GitLabFinding.model_validate(raw)
    assert reloaded == finding


def test_gitlab_audit_report_json_round_trip() -> None:
    report = make_gitlab_report(findings=[make_gitlab_finding()])
    raw = json.loads(report.model_dump_json())
    reloaded = GitLabAuditReport.model_validate(raw)
    assert reloaded == report
    assert raw["platform"] == "gitlab"


# --- Validation: project_id ---------------------------------------------------


@pytest.mark.parametrize("bad_project_id", [0, -1, -100])
def test_gitlab_audit_report_rejects_non_positive_project_id(bad_project_id: int) -> None:
    with pytest.raises(ValidationError):
        make_gitlab_report(project_id=bad_project_id)


def test_gitlab_audit_report_accepts_positive_project_id() -> None:
    report = make_gitlab_report(project_id=1)
    assert report.project_id == 1


# --- Validation: empty required identifiers -----------------------------------


@pytest.mark.parametrize("field", ["gitlab_url", "project_path", "default_branch"])
def test_gitlab_audit_report_rejects_empty_required_identifiers(field: str) -> None:
    with pytest.raises(ValidationError):
        make_gitlab_report(**{field: ""})


@pytest.mark.parametrize("field", ["check_id", "project_path", "resource_name"])
def test_gitlab_finding_rejects_empty_required_identifiers(field: str) -> None:
    with pytest.raises(ValidationError):
        make_gitlab_finding(**{field: ""})


# --- Independent mutable defaults across instances ----------------------------


def test_gitlab_audit_report_default_findings_are_independent_across_instances() -> None:
    report_a = make_gitlab_report()
    report_b = make_gitlab_report()
    report_a.findings.append(make_gitlab_finding())
    assert report_a.findings != report_b.findings
    assert report_b.findings == []


def test_gitlab_audit_report_default_summary_is_independent_across_instances() -> None:
    report_a = make_gitlab_report()
    report_b = make_gitlab_report()
    report_a.summary.high += 1
    assert report_a.summary.high == 1
    assert report_b.summary.high == 0


# --- Isolation from the Kubernetes models -------------------------------------


def make_kubernetes_finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "check_id": "K8S-RES-001",
        "title": "Container has no CPU request",
        "severity": Severity.MEDIUM,
        "cluster_context": "test-ctx",
        "namespace": "default",
        "resource_kind": ResourceKind.POD,
        "resource_name": "web-1",
        "evidence": "e",
        "impact": "i",
        "recommendation": "r",
        "auto_remediable": False,
        "audited_at": NOW,
    }
    defaults.update(overrides)
    return Finding(**defaults)


def test_gitlab_audit_report_rejects_a_kubernetes_finding() -> None:
    k8s_finding = make_kubernetes_finding()
    with pytest.raises(ValidationError):
        make_gitlab_report(findings=[k8s_finding])


def test_kubernetes_audit_report_rejects_a_gitlab_finding() -> None:
    gitlab_finding = make_gitlab_finding()
    with pytest.raises(ValidationError):
        AuditReport(
            cluster_context="test-ctx",
            namespace_filter=None,
            generated_at=NOW,
            findings=[gitlab_finding],
        )


def test_kubernetes_finding_and_gitlab_finding_are_distinct_types() -> None:
    assert Finding is not GitLabFinding
    assert not issubclass(GitLabFinding, Finding)
    assert not issubclass(Finding, GitLabFinding)
