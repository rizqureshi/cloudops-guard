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
    GitLabProjectSettings,
    GitLabProjectSnapshot,
    GitLabProtectedBranchRule,
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


# --- GitLab normalized collection models (v0.2.0 Phase 2B) -------------------
#
# These models hold the normalized instance/project/protected-branch
# snapshot the collector produces; see `docs/milestones/v0.2.0-gitlab-
# audit.md`. All values used here are synthetic and non-sensitive.


def make_project_settings(**overrides: object) -> GitLabProjectSettings:
    defaults: dict[str, object] = {
        "project_id": 42,
        "project_path": "group/subgroup/project",
        "default_branch": "main",
        "visibility": "private",
        "only_allow_merge_if_pipeline_succeeds": False,
        "public_jobs": False,
        "ci_push_repository_for_job_token_allowed": False,
        "ci_pipeline_variables_minimum_override_role": "maintainer",
        "auto_cancel_pending_pipelines": "enabled",
        "ci_default_git_depth": 50,
        "build_timeout": 3600,
    }
    defaults.update(overrides)
    return GitLabProjectSettings(**defaults)


def make_protected_branch_rule(**overrides: object) -> GitLabProtectedBranchRule:
    defaults: dict[str, object] = {
        "name": "main",
        "allow_force_push": False,
        "role_push_access_levels": [40],
    }
    defaults.update(overrides)
    return GitLabProtectedBranchRule(**defaults)


def make_project_snapshot(**overrides: object) -> GitLabProjectSnapshot:
    defaults: dict[str, object] = {
        "gitlab_url": "https://gitlab.example.com",
        "gitlab_version": "18.4.1-ee",
        "enterprise": True,
        "collected_at": NOW,
        "project": make_project_settings(),
        "protected_branches": [make_protected_branch_rule()],
    }
    defaults.update(overrides)
    return GitLabProjectSnapshot(**defaults)


# --- GitLabProjectSettings: valid construction --------------------------------


def test_project_settings_constructs_with_all_required_fields() -> None:
    settings = make_project_settings()
    assert settings.project_id == 42
    assert settings.project_path == "group/subgroup/project"
    assert settings.default_branch == "main"
    assert settings.visibility == "private"
    assert settings.ci_default_git_depth == 50


def test_project_settings_accepts_null_ci_default_git_depth() -> None:
    settings = make_project_settings(ci_default_git_depth=None)
    assert settings.ci_default_git_depth is None


def test_project_settings_accepts_zero_ci_default_git_depth() -> None:
    settings = make_project_settings(ci_default_git_depth=0)
    assert settings.ci_default_git_depth == 0


@pytest.mark.parametrize("visibility", ["private", "internal", "public"])
def test_project_settings_accepts_every_documented_visibility(visibility: str) -> None:
    assert make_project_settings(visibility=visibility).visibility == visibility


@pytest.mark.parametrize("role", ["no_one_allowed", "owner", "maintainer", "developer"])
def test_project_settings_accepts_every_documented_override_role(role: str) -> None:
    settings = make_project_settings(ci_pipeline_variables_minimum_override_role=role)
    assert settings.ci_pipeline_variables_minimum_override_role == role


@pytest.mark.parametrize("value", ["enabled", "disabled"])
def test_project_settings_accepts_every_documented_auto_cancel_value(value: str) -> None:
    settings = make_project_settings(auto_cancel_pending_pipelines=value)
    assert settings.auto_cancel_pending_pipelines == value


# --- GitLabProjectSettings: required fields, no defaults ----------------------


@pytest.mark.parametrize(
    "field",
    [
        "project_id",
        "project_path",
        "default_branch",
        "visibility",
        "only_allow_merge_if_pipeline_succeeds",
        "public_jobs",
        "ci_push_repository_for_job_token_allowed",
        "ci_pipeline_variables_minimum_override_role",
        "auto_cancel_pending_pipelines",
        "ci_default_git_depth",
        "build_timeout",
    ],
)
def test_project_settings_every_field_is_required(field: str) -> None:
    data = make_project_settings().model_dump()
    del data[field]
    with pytest.raises(ValidationError):
        GitLabProjectSettings(**data)


# --- GitLabProjectSettings: no silent type coercion ----------------------------


@pytest.mark.parametrize("value", [True, False])
def test_project_settings_rejects_boolean_project_id(value: bool) -> None:
    with pytest.raises(ValidationError):
        make_project_settings(project_id=value)


@pytest.mark.parametrize("bad_project_id", [0, -1, -100])
def test_project_settings_rejects_non_positive_project_id(bad_project_id: int) -> None:
    with pytest.raises(ValidationError):
        make_project_settings(project_id=bad_project_id)


@pytest.mark.parametrize(
    "field",
    [
        "only_allow_merge_if_pipeline_succeeds",
        "public_jobs",
        "ci_push_repository_for_job_token_allowed",
    ],
)
@pytest.mark.parametrize("value", [0, 1, "false", "true", None])
def test_project_settings_rejects_non_boolean_for_boolean_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_project_settings(**{field: value})


def test_project_settings_rejects_boolean_ci_default_git_depth() -> None:
    with pytest.raises(ValidationError):
        make_project_settings(ci_default_git_depth=True)


def test_project_settings_rejects_negative_ci_default_git_depth() -> None:
    with pytest.raises(ValidationError):
        make_project_settings(ci_default_git_depth=-1)


def test_project_settings_rejects_boolean_build_timeout() -> None:
    with pytest.raises(ValidationError):
        make_project_settings(build_timeout=True)


@pytest.mark.parametrize("bad_build_timeout", [0, -1])
def test_project_settings_rejects_non_positive_build_timeout(bad_build_timeout: int) -> None:
    with pytest.raises(ValidationError):
        make_project_settings(build_timeout=bad_build_timeout)


def test_project_settings_rejects_unknown_visibility() -> None:
    with pytest.raises(ValidationError):
        make_project_settings(visibility="unknown")


def test_project_settings_rejects_unknown_override_role() -> None:
    with pytest.raises(ValidationError):
        make_project_settings(ci_pipeline_variables_minimum_override_role="admin")


def test_project_settings_rejects_boolean_auto_cancel_pending_pipelines() -> None:
    # auto_cancel_pending_pipelines is documented as a string enum, not a
    # boolean -- a boolean here must never be silently treated as equivalent.
    with pytest.raises(ValidationError):
        make_project_settings(auto_cancel_pending_pipelines=True)


def test_project_settings_rejects_unknown_auto_cancel_value() -> None:
    with pytest.raises(ValidationError):
        make_project_settings(auto_cancel_pending_pipelines="maybe")


@pytest.mark.parametrize("field", ["project_path", "default_branch"])
def test_project_settings_rejects_empty_required_identifiers(field: str) -> None:
    with pytest.raises(ValidationError):
        make_project_settings(**{field: ""})


# --- GitLabProjectSettings: field order ----------------------------------------


def test_project_settings_field_order() -> None:
    assert list(GitLabProjectSettings.model_fields) == [
        "project_id",
        "project_path",
        "default_branch",
        "visibility",
        "only_allow_merge_if_pipeline_succeeds",
        "public_jobs",
        "ci_push_repository_for_job_token_allowed",
        "ci_pipeline_variables_minimum_override_role",
        "auto_cancel_pending_pipelines",
        "ci_default_git_depth",
        "build_timeout",
    ]


# --- GitLabProjectSettings: JSON round-trip ------------------------------------


def test_project_settings_json_round_trip() -> None:
    settings = make_project_settings()
    raw = json.loads(settings.model_dump_json())
    reloaded = GitLabProjectSettings.model_validate(raw)
    assert reloaded == settings


def test_project_settings_json_round_trip_with_null_git_depth() -> None:
    settings = make_project_settings(ci_default_git_depth=None)
    raw = json.loads(settings.model_dump_json())
    assert raw["ci_default_git_depth"] is None
    reloaded = GitLabProjectSettings.model_validate(raw)
    assert reloaded == settings


# --- GitLabProtectedBranchRule: valid construction -----------------------------


def test_protected_branch_rule_constructs_with_required_fields() -> None:
    rule = make_protected_branch_rule()
    assert rule.name == "main"
    assert rule.allow_force_push is False
    assert rule.role_push_access_levels == [40]


def test_protected_branch_rule_inherited_defaults_to_none() -> None:
    rule = make_protected_branch_rule()
    assert rule.inherited is None


@pytest.mark.parametrize("value", [True, False])
def test_protected_branch_rule_accepts_a_boolean_inherited(value: bool) -> None:
    rule = make_protected_branch_rule(inherited=value)
    assert rule.inherited is value


def test_protected_branch_rule_rejects_non_boolean_inherited() -> None:
    with pytest.raises(ValidationError):
        make_protected_branch_rule(inherited="true")


@pytest.mark.parametrize("level", [0, 30, 40, 60])
def test_protected_branch_rule_accepts_every_documented_role_level(level: int) -> None:
    rule = make_protected_branch_rule(role_push_access_levels=[level])
    assert rule.role_push_access_levels == [level]


@pytest.mark.parametrize("level", [1, 10, 20, 50, 70, 100])
def test_protected_branch_rule_rejects_undocumented_role_levels(level: int) -> None:
    with pytest.raises(ValidationError):
        make_protected_branch_rule(role_push_access_levels=[level])


# --- role_push_access_levels: strict integer typing, not just value equality --


@pytest.mark.parametrize(
    "value",
    [
        False,  # bool is an int subclass; False == 0 must not slip through
        True,  # True == 1 is not even a documented level, but must still be
        # rejected as a bool, not silently compared as an int
        0.0,  # float(0.0) == 0 must not slip through
        30.0,  # float(30.0) == 30 must not slip through
        "30",  # numeric string must not slip through
        99,  # undocumented integer
    ],
)
def test_protected_branch_rule_rejects_non_strict_role_levels(value: object) -> None:
    with pytest.raises(ValidationError):
        make_protected_branch_rule(role_push_access_levels=[value])


def test_protected_branch_rule_role_push_access_levels_defaults_to_empty_list() -> None:
    rule = GitLabProtectedBranchRule(name="main", allow_force_push=False)
    assert rule.role_push_access_levels == []


def test_protected_branch_rule_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        make_protected_branch_rule(name="")


def test_protected_branch_rule_rejects_non_boolean_allow_force_push() -> None:
    with pytest.raises(ValidationError):
        make_protected_branch_rule(allow_force_push="false")


# --- GitLabProtectedBranchRule: field order and default_factory independence --


def test_protected_branch_rule_field_order() -> None:
    assert list(GitLabProtectedBranchRule.model_fields) == [
        "name",
        "allow_force_push",
        "inherited",
        "role_push_access_levels",
    ]


def test_protected_branch_rule_default_role_levels_are_independent_across_instances() -> None:
    rule_a = GitLabProtectedBranchRule(name="a", allow_force_push=False)
    rule_b = GitLabProtectedBranchRule(name="b", allow_force_push=False)
    rule_a.role_push_access_levels.append(40)
    assert rule_a.role_push_access_levels != rule_b.role_push_access_levels
    assert rule_b.role_push_access_levels == []


# --- GitLabProtectedBranchRule: JSON round-trip --------------------------------


def test_protected_branch_rule_json_round_trip() -> None:
    rule = make_protected_branch_rule(inherited=True, role_push_access_levels=[0, 40])
    raw = json.loads(rule.model_dump_json())
    reloaded = GitLabProtectedBranchRule.model_validate(raw)
    assert reloaded == rule


# --- GitLabProjectSnapshot: valid construction ---------------------------------


def test_project_snapshot_constructs_with_required_fields() -> None:
    snapshot = make_project_snapshot()
    assert snapshot.gitlab_url == "https://gitlab.example.com"
    assert snapshot.gitlab_version == "18.4.1-ee"
    assert snapshot.enterprise is True
    assert snapshot.collected_at == NOW
    assert snapshot.project == make_project_settings()
    assert snapshot.protected_branches == [make_protected_branch_rule()]


def test_project_snapshot_protected_branches_defaults_to_empty_list() -> None:
    snapshot = make_project_snapshot(protected_branches=[])
    assert snapshot.protected_branches == []


def test_project_snapshot_default_protected_branches_is_independent_across_instances() -> None:
    snapshot_a = GitLabProjectSnapshot(
        gitlab_url="https://gitlab.example.com",
        gitlab_version="18.4",
        enterprise=False,
        collected_at=NOW,
        project=make_project_settings(),
    )
    snapshot_b = GitLabProjectSnapshot(
        gitlab_url="https://gitlab.example.com",
        gitlab_version="18.4",
        enterprise=False,
        collected_at=NOW,
        project=make_project_settings(),
    )
    snapshot_a.protected_branches.append(make_protected_branch_rule())
    assert snapshot_a.protected_branches != snapshot_b.protected_branches
    assert snapshot_b.protected_branches == []


def test_project_snapshot_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        make_project_snapshot(collected_at=dt.datetime(2026, 2, 1, 12, 0))


class _BrokenTzInfo(dt.tzinfo):
    """A `tzinfo` that is attached (so `.tzinfo is not None`) but whose
    `utcoffset()` returns `None` -- Python's own datetime documentation
    describes this as behaving like a naive datetime for arithmetic and
    comparison, so it must be rejected exactly like a bare naive datetime.
    """

    def utcoffset(self, __dt: dt.datetime | None) -> dt.timedelta | None:
        return None

    def dst(self, __dt: dt.datetime | None) -> dt.timedelta | None:
        return None

    def tzname(self, __dt: dt.datetime | None) -> str | None:
        return "broken"


def test_project_snapshot_rejects_tzinfo_with_none_utcoffset() -> None:
    broken = dt.datetime(2026, 2, 1, 12, 0, tzinfo=_BrokenTzInfo())
    assert broken.tzinfo is not None  # tzinfo is attached...
    assert broken.utcoffset() is None  # ...but behaves as naive
    with pytest.raises(ValidationError):
        make_project_snapshot(collected_at=broken)


def test_project_snapshot_rejects_non_boolean_enterprise() -> None:
    with pytest.raises(ValidationError):
        make_project_snapshot(enterprise=1)


@pytest.mark.parametrize("field", ["gitlab_url", "gitlab_version"])
def test_project_snapshot_rejects_empty_required_identifiers(field: str) -> None:
    with pytest.raises(ValidationError):
        make_project_snapshot(**{field: ""})


def test_project_snapshot_has_no_raw_response_or_catch_all_field() -> None:
    assert set(GitLabProjectSnapshot.model_fields) == {
        "gitlab_url",
        "gitlab_version",
        "enterprise",
        "collected_at",
        "project",
        "protected_branches",
    }


# --- GitLabProjectSnapshot: field order -----------------------------------------


def test_project_snapshot_field_order() -> None:
    assert list(GitLabProjectSnapshot.model_fields) == [
        "gitlab_url",
        "gitlab_version",
        "enterprise",
        "collected_at",
        "project",
        "protected_branches",
    ]


# --- GitLabProjectSnapshot: JSON round-trip -------------------------------------


def test_project_snapshot_json_round_trip() -> None:
    snapshot = make_project_snapshot()
    raw = json.loads(snapshot.model_dump_json())
    reloaded = GitLabProjectSnapshot.model_validate(raw)
    assert reloaded == snapshot


# --- Isolation from Phase 1 GitLab report models and Kubernetes models --------


def test_project_snapshot_is_distinct_from_gitlab_audit_report() -> None:
    assert GitLabProjectSnapshot is not GitLabAuditReport
    assert not issubclass(GitLabProjectSnapshot, GitLabAuditReport)
    assert not issubclass(GitLabAuditReport, GitLabProjectSnapshot)
