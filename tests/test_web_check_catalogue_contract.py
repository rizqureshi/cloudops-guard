"""Contract test between the web check catalogue and the real Python checks.

Phase 3H's website ships a project-owned catalogue of every currently
implemented check (`web/src/data/check-catalogue.json`), describing each
check's ID, title, and severity for display on `/checks` and
`/checks/[id]`. This module never modifies that JSON, any Python
production model/collector/evaluator/check/CLI/report-generator/template,
or any released report contract -- it only *compares* the two.

Findings are produced by calling the real, production evaluator entry
points (`evaluate_container`, `evaluate_container_restarts`,
`evaluate_protected_branch_checks`, `evaluate_project_setting_checks`,
`evaluate_job_timeout_check`, `evaluate_ci_image_check`) with inputs
crafted to trigger every one of the 17 currently implemented checks --
never by parsing source text and never by reimplementing a check's
condition. `GL-BR-001` cannot occur in the same protected-branch state as
`GL-BR-002`/`GL-BR-003` (see `evaluate_protected_branch_checks`'s own
docstring), so two separate, clearly-labeled snapshots are evaluated and
their resulting findings' metadata is combined -- never misrepresented as
one scan.

The expected title/severity per check ID is read from the findings the
production checks actually return, not restated as a separate hard-coded
table here -- so this test cannot pass merely by keeping two independently
maintained copies of the same expectation in sync.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from cloudops_guard.checks.gitlab import (
    evaluate_ci_image_check,
    evaluate_job_timeout_check,
    evaluate_project_setting_checks,
    evaluate_protected_branch_checks,
)
from cloudops_guard.checks.kubernetes import evaluate_container, evaluate_container_restarts
from cloudops_guard.models import (
    ContainerInfo,
    ContainerRuntimeStatus,
    GitLabCiConfigSnapshot,
    GitLabCiImageReference,
    GitLabFinding,
    GitLabProjectSettings,
    GitLabProjectSnapshot,
    GitLabProtectedBranchRule,
    GitLabResourceKind,
    PodInfo,
    ResourceKind,
    ResourceRequirements,
)

NOW = dt.datetime(2026, 8, 24, 9, 0, tzinfo=dt.UTC)

CATALOGUE_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "src" / "data" / "check-catalogue.json"
)


def _load_catalogue() -> list[dict[str, object]]:
    with CATALOGUE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


# --- Real production findings, covering all 17 checks -----------------------------


def _kubernetes_findings() -> list:
    """Covers K8S-RES-001..004 and K8S-IMG-001 via `evaluate_container`, and
    K8S-REL-001 via `evaluate_container_restarts` -- both real, public
    evaluator entry points, never a reimplementation of a check's condition.
    """
    bare_container = ContainerInfo(
        name="app",
        image="example.com/app:latest",
        resources=ResourceRequirements(),
    )
    container_findings = evaluate_container(
        ResourceKind.DEPLOYMENT, "checkout-api", "payments-demo", bare_container, "ctx", NOW
    )

    pod = PodInfo(name="checkout-api-abc123", namespace="payments-demo")
    status = ContainerRuntimeStatus(container_name="app", restart_count=10, ready=False)
    restart_finding = evaluate_container_restarts(pod, status, "ctx", threshold=5, now=NOW)
    assert restart_finding is not None

    return [*container_findings, restart_finding]


def _gitlab_branch_protection_findings() -> list[GitLabFinding]:
    """Covers GL-BR-001, GL-BR-002, and GL-BR-003.

    `GL-BR-001` fires only when *no* protected-branch rule matches the
    default branch; `GL-BR-002`/`GL-BR-003` only ever fire when at least one
    rule *does* match (see `evaluate_protected_branch_checks`'s own
    docstring: "If no protected-branch rule matches the default branch,
    only GL-BR-001 is produced"). These are therefore two distinct,
    mutually exclusive scan states, evaluated separately here and combined
    only at the metadata level below -- never presented as one scan.
    """
    unprotected_settings = GitLabProjectSettings(
        project_id=1,
        project_path="group/unprotected-project",
        default_branch="main",
        visibility="private",
        only_allow_merge_if_pipeline_succeeds=True,
        public_jobs=False,
        ci_push_repository_for_job_token_allowed=False,
        ci_pipeline_variables_minimum_override_role="maintainer",
        auto_cancel_pending_pipelines="enabled",
        ci_default_git_depth=50,
        build_timeout=3600,
    )
    unprotected_snapshot = GitLabProjectSnapshot(
        gitlab_url="https://gitlab.example.com",
        gitlab_version="18.4.1",
        enterprise=False,
        collected_at=NOW,
        project=unprotected_settings,
        protected_branches=[],
    )
    br001_findings = evaluate_protected_branch_checks(unprotected_snapshot, audited_at=NOW)

    permissive_settings = unprotected_settings.model_copy(
        update={"project_path": "group/protected-project"}
    )
    permissive_snapshot = GitLabProjectSnapshot(
        gitlab_url="https://gitlab.example.com",
        gitlab_version="18.4.1",
        enterprise=False,
        collected_at=NOW,
        project=permissive_settings,
        protected_branches=[
            GitLabProtectedBranchRule(
                name="main",
                allow_force_push=True,
                role_push_access_levels=[30],  # Developer
            )
        ],
    )
    br002_br003_findings = evaluate_protected_branch_checks(permissive_snapshot, audited_at=NOW)

    return [*br001_findings, *br002_br003_findings]


def _gitlab_project_setting_findings() -> list[GitLabFinding]:
    """Covers GL-MR-001, GL-SEC-001, GL-SEC-002, GL-SEC-003, GL-COST-001, and GL-COST-002."""
    settings = GitLabProjectSettings(
        project_id=2,
        project_path="group/settings-project",
        default_branch="main",
        visibility="internal",
        only_allow_merge_if_pipeline_succeeds=False,  # GL-MR-001
        public_jobs=True,  # GL-SEC-001 (with visibility=internal)
        ci_push_repository_for_job_token_allowed=True,  # GL-SEC-002
        ci_pipeline_variables_minimum_override_role="developer",  # GL-SEC-003
        auto_cancel_pending_pipelines="disabled",  # GL-COST-001
        ci_default_git_depth=0,  # GL-COST-002
        build_timeout=3600,
    )
    snapshot = GitLabProjectSnapshot(
        gitlab_url="https://gitlab.example.com",
        gitlab_version="18.4.1",
        enterprise=False,
        collected_at=NOW,
        project=settings,
        protected_branches=[],
    )
    return evaluate_project_setting_checks(snapshot, audited_at=NOW)


def _gitlab_job_timeout_finding() -> GitLabFinding:
    """Covers GL-REL-001."""
    settings = GitLabProjectSettings(
        project_id=3,
        project_path="group/timeout-project",
        default_branch="main",
        visibility="private",
        only_allow_merge_if_pipeline_succeeds=True,
        public_jobs=False,
        ci_push_repository_for_job_token_allowed=False,
        ci_pipeline_variables_minimum_override_role="maintainer",
        auto_cancel_pending_pipelines="enabled",
        ci_default_git_depth=50,
        build_timeout=7200,
    )
    snapshot = GitLabProjectSnapshot(
        gitlab_url="https://gitlab.example.com",
        gitlab_version="18.4.1",
        enterprise=False,
        collected_at=NOW,
        project=settings,
        protected_branches=[],
    )
    finding = evaluate_job_timeout_check(
        snapshot, audited_at=NOW, job_timeout_threshold_seconds=3600
    )
    assert finding is not None
    return finding


def _gitlab_ci_image_findings() -> list[GitLabFinding]:
    """Covers GL-CI-001."""
    snapshot = GitLabCiConfigSnapshot(
        project_path="group/ci-project",
        collected_at=NOW,
        images=[
            GitLabCiImageReference(
                job_name="build",
                resource_kind=GitLabResourceKind.CI_JOB,
                image="registry.example.com/build:latest",
                dynamic=False,
            )
        ],
    )
    return evaluate_ci_image_check(snapshot, audited_at=NOW)


def _all_representative_findings() -> list:
    return [
        *_kubernetes_findings(),
        *_gitlab_branch_protection_findings(),
        *_gitlab_project_setting_findings(),
        _gitlab_job_timeout_finding(),
        *_gitlab_ci_image_findings(),
    ]


def test_representative_findings_cover_every_currently_implemented_check() -> None:
    """Sanity check on the fixture-building helpers above, independent of
    the catalogue: proves the crafted inputs actually exercise all 17
    checks before the catalogue comparison below relies on that coverage.
    """
    expected_ids = {
        "K8S-RES-001",
        "K8S-RES-002",
        "K8S-RES-003",
        "K8S-RES-004",
        "K8S-IMG-001",
        "K8S-REL-001",
        "GL-BR-001",
        "GL-BR-002",
        "GL-BR-003",
        "GL-MR-001",
        "GL-SEC-001",
        "GL-SEC-002",
        "GL-SEC-003",
        "GL-COST-001",
        "GL-COST-002",
        "GL-REL-001",
        "GL-CI-001",
    }
    actual_ids = {finding.check_id for finding in _all_representative_findings()}
    assert actual_ids == expected_ids


def test_catalogue_has_exactly_seventeen_unique_ids() -> None:
    catalogue = _load_catalogue()
    ids = [entry["checkId"] for entry in catalogue]
    assert len(ids) == 17
    assert len(set(ids)) == 17


def test_catalogue_matches_production_findings_by_id_title_and_severity() -> None:
    catalogue = _load_catalogue()
    catalogue_by_id = {entry["checkId"]: entry for entry in catalogue}

    findings_by_id: dict[str, object] = {}
    for finding in _all_representative_findings():
        # Every check produces the same title/severity regardless of which
        # specific resource triggered it, so the first occurrence per ID is
        # representative -- this is not asserted blindly, it is exactly
        # what each check's own implementation guarantees (see
        # checks/kubernetes.py and checks/gitlab.py: title and severity are
        # fixed per check, never resource-dependent).
        findings_by_id.setdefault(finding.check_id, finding)

    catalogue_ids = set(catalogue_by_id.keys())
    finding_ids = set(findings_by_id.keys())

    missing_from_catalogue = finding_ids - catalogue_ids
    extra_in_catalogue = catalogue_ids - finding_ids
    assert not missing_from_catalogue, (
        f"checks missing from the web catalogue: {missing_from_catalogue}"
    )
    assert not extra_in_catalogue, (
        f"checks in the web catalogue that no longer exist: {extra_in_catalogue}"
    )

    mismatched_titles = {}
    mismatched_severities = {}
    for check_id, finding in findings_by_id.items():
        catalogue_entry = catalogue_by_id[check_id]
        if catalogue_entry["title"] != finding.title:
            mismatched_titles[check_id] = (catalogue_entry["title"], finding.title)
        if catalogue_entry["severity"] != finding.severity.value:
            mismatched_severities[check_id] = (catalogue_entry["severity"], finding.severity.value)

    assert not mismatched_titles, f"catalogue title differs from production: {mismatched_titles}"
    assert not mismatched_severities, (
        f"catalogue severity differs from production: {mismatched_severities}"
    )
