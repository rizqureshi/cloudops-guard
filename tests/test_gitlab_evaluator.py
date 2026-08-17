"""Tests for the combined GitLab evaluator and report construction (v0.2.0
Phase 2D-A, `evaluate_gitlab` in `src/cloudops_guard/engine/evaluator.py`).

Only synthetic `GitLabProjectSnapshot`/`GitLabCiConfigSnapshot` objects are
used -- no `GitLabClient`/`GitLabCollector` is instantiated, and no network
or filesystem access occurs. `evaluate_gitlab` is exercised through its
public contract; individual check conditions and wording are already
covered by `tests/test_gitlab_checks.py` and are not re-tested here --
these tests instead prove the *orchestration* contract: identity binding,
timestamp propagation, stable combined ordering, purity, and that combined
results are exactly equivalent to concatenating the four existing public
evaluator results.
"""

from __future__ import annotations

import datetime as dt
import inspect

import pytest
import urllib3

import cloudops_guard.engine.evaluator as evaluator_module
from cloudops_guard.checks.gitlab import (
    evaluate_ci_image_check,
    evaluate_job_timeout_check,
    evaluate_project_setting_checks,
    evaluate_protected_branch_checks,
)
from cloudops_guard.engine.evaluator import evaluate_gitlab
from cloudops_guard.models import (
    AuditSummary,
    GitLabCiConfigSnapshot,
    GitLabCiImageReference,
    GitLabProjectSettings,
    GitLabProjectSnapshot,
    GitLabProtectedBranchRule,
    GitLabResourceKind,
    Severity,
)

PROJECT_PATH = "group/subgroup/project"
AUDITED_AT = dt.datetime(2026, 8, 17, 12, 0, tzinfo=dt.UTC)
# Deliberately different from AUDITED_AT and from each other: the two
# snapshots' own collected_at values must never be required to match one
# another, and neither is ever compared against audited_at.
COLLECTED_AT_PROJECT = dt.datetime(2026, 8, 17, 9, 0, tzinfo=dt.UTC)
COLLECTED_AT_CI = dt.datetime(2026, 8, 17, 10, 30, tzinfo=dt.UTC)


def make_project_settings(**overrides: object) -> GitLabProjectSettings:
    defaults: dict[str, object] = {
        "project_id": 42,
        "project_path": PROJECT_PATH,
        "default_branch": "main",
        "visibility": "private",
        "only_allow_merge_if_pipeline_succeeds": True,
        "public_jobs": False,
        "ci_push_repository_for_job_token_allowed": False,
        "ci_pipeline_variables_minimum_override_role": "maintainer",
        "auto_cancel_pending_pipelines": "enabled",
        "ci_default_git_depth": 50,
        "build_timeout": 600,
    }
    defaults.update(overrides)
    return GitLabProjectSettings(**defaults)


def make_rule(**overrides: object) -> GitLabProtectedBranchRule:
    defaults: dict[str, object] = {
        "name": "main",
        "allow_force_push": False,
        "role_push_access_levels": [],
    }
    defaults.update(overrides)
    return GitLabProtectedBranchRule(**defaults)


def make_project_snapshot(
    *,
    rules: list[GitLabProtectedBranchRule] | None = None,
    collected_at: dt.datetime | None = None,
    **project_overrides: object,
) -> GitLabProjectSnapshot:
    return GitLabProjectSnapshot(
        gitlab_url="https://gitlab.example.com",
        gitlab_version="18.4.1",
        enterprise=False,
        collected_at=collected_at if collected_at is not None else COLLECTED_AT_PROJECT,
        project=make_project_settings(**project_overrides),
        protected_branches=[make_rule()] if rules is None else rules,
    )


def make_ci_image_ref(**overrides: object) -> GitLabCiImageReference:
    defaults: dict[str, object] = {
        "job_name": "build",
        "resource_kind": GitLabResourceKind.CI_JOB,
        "image": "alpine:3.19",
        "dynamic": False,
    }
    defaults.update(overrides)
    return GitLabCiImageReference(**defaults)


def make_ci_snapshot(
    images: list[GitLabCiImageReference] | None = None,
    *,
    project_path: str = PROJECT_PATH,
    collected_at: dt.datetime | None = None,
) -> GitLabCiConfigSnapshot:
    return GitLabCiConfigSnapshot(
        project_path=project_path,
        collected_at=collected_at if collected_at is not None else COLLECTED_AT_CI,
        images=[] if images is None else images,
    )


class _BrokenTzInfo(dt.tzinfo):
    def utcoffset(self, __dt: dt.datetime | None) -> dt.timedelta | None:
        return None

    def dst(self, __dt: dt.datetime | None) -> dt.timedelta | None:
        return None

    def tzname(self, __dt: dt.datetime | None) -> str | None:
        return "broken"


def _forbid_check_evaluator_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def _spy(*args: object, **kwargs: object) -> None:
        raise AssertionError("a check evaluator must not be invoked before validation passes")

    monkeypatch.setattr(evaluator_module, "evaluate_protected_branch_checks", _spy)
    monkeypatch.setattr(evaluator_module, "evaluate_project_setting_checks", _spy)
    monkeypatch.setattr(evaluator_module, "evaluate_job_timeout_check", _spy)
    monkeypatch.setattr(evaluator_module, "evaluate_ci_image_check", _spy)


# --- 1: safe snapshots -----------------------------------------------------------


def test_safe_snapshots_produce_an_empty_report_and_zero_summary() -> None:
    project = make_project_snapshot()
    ci = make_ci_snapshot()
    report = evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)
    assert report.findings == []
    assert report.summary == AuditSummary()
    assert report.summary.total == 0


# --- 2: report identity fields come exclusively from GitLabProjectSnapshot --------


def test_report_identity_fields_come_exclusively_from_project_snapshot() -> None:
    project = make_project_snapshot(
        project_id=999,
        project_path="group/other-project",
        default_branch="release",
    )
    ci = make_ci_snapshot(project_path="group/other-project")
    report = evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)
    assert report.platform == "gitlab"
    assert report.gitlab_url == project.gitlab_url
    assert report.project_id == 999
    assert report.project_path == "group/other-project"
    assert report.default_branch == "release"


# --- 3: timestamp propagation -----------------------------------------------------


def test_generated_at_and_every_finding_audited_at_equal_the_supplied_value() -> None:
    project = make_project_snapshot(only_allow_merge_if_pipeline_succeeds=False)
    ci = make_ci_snapshot([make_ci_image_ref(image="alpine:latest")])
    report = evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)
    assert report.generated_at == AUDITED_AT
    assert len(report.findings) >= 2
    for finding in report.findings:
        assert finding.audited_at == AUDITED_AT


# --- 4: combined results equal concatenating the four public evaluators -----------


def test_combined_results_equal_concatenation_of_the_four_public_evaluators() -> None:
    project = make_project_snapshot(
        rules=[make_rule(allow_force_push=True)],
        only_allow_merge_if_pipeline_succeeds=False,
        build_timeout=7200,
    )
    ci = make_ci_snapshot(
        [
            make_ci_image_ref(job_name="first", image="alpine:latest"),
            make_ci_image_ref(job_name="second", image="app:1.0"),
        ]
    )
    threshold = 3600

    report = evaluate_gitlab(
        project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=threshold
    )

    expected = []
    expected.extend(evaluate_protected_branch_checks(project, audited_at=AUDITED_AT))
    expected.extend(evaluate_project_setting_checks(project, audited_at=AUDITED_AT))
    timeout_finding = evaluate_job_timeout_check(
        project, audited_at=AUDITED_AT, job_timeout_threshold_seconds=threshold
    )
    if timeout_finding is not None:
        expected.append(timeout_finding)
    expected.extend(evaluate_ci_image_check(ci, audited_at=AUDITED_AT))

    assert report.findings == expected


# --- 5 & 6: stable family ordering, preserving each family's own order ------------


def test_stable_family_ordering_and_internal_family_order_is_preserved() -> None:
    project = make_project_snapshot(
        rules=[make_rule(allow_force_push=True, role_push_access_levels=[30])],
        only_allow_merge_if_pipeline_succeeds=False,
        visibility="private",
        public_jobs=True,
        build_timeout=7200,
    )
    ci = make_ci_snapshot([make_ci_image_ref(image="alpine:latest")])
    report = evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)

    check_ids = [f.check_id for f in report.findings]
    br_ids = [c for c in check_ids if c.startswith("GL-BR-")]
    setting_ids = [
        c
        for c in check_ids
        if c.startswith("GL-MR-") or c.startswith("GL-SEC-") or c.startswith("GL-COST-")
    ]
    timeout_ids = [c for c in check_ids if c == "GL-REL-001"]
    ci_ids = [c for c in check_ids if c == "GL-CI-001"]

    # Every finding belongs to exactly one of the four families, and the
    # families appear in the documented order.
    assert check_ids == br_ids + setting_ids + timeout_ids + ci_ids
    # GL-BR-001 is suppressed since a matching rule exists; GL-BR-002
    # precedes GL-BR-003 -- evaluate_protected_branch_checks' own order.
    assert br_ids == ["GL-BR-002", "GL-BR-003"]
    assert setting_ids == ["GL-MR-001", "GL-SEC-001"]
    assert timeout_ids == ["GL-REL-001"]
    assert ci_ids == ["GL-CI-001"]


# --- 7: multiple mutable CI image occurrences remain separate, in order -----------


def test_multiple_mutable_ci_image_occurrences_remain_separate_findings_in_order() -> None:
    project = make_project_snapshot()
    ci = make_ci_snapshot(
        [
            make_ci_image_ref(job_name="first", image="alpine:latest"),
            make_ci_image_ref(
                job_name="first", resource_kind=GitLabResourceKind.CI_SERVICE, image="redis:latest"
            ),
            make_ci_image_ref(job_name="second", image="app:latest"),
        ]
    )
    report = evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)
    ci_findings = [f for f in report.findings if f.check_id == "GL-CI-001"]
    assert len(ci_findings) == 3
    assert [(f.job_name, f.resource_kind) for f in ci_findings] == [
        ("first", GitLabResourceKind.CI_JOB),
        ("first", GitLabResourceKind.CI_SERVICE),
        ("second", GitLabResourceKind.CI_JOB),
    ]


# --- 8: severity summary counts and total are exact --------------------------------


def test_summary_counts_and_total_are_exact() -> None:
    project = make_project_snapshot(
        only_allow_merge_if_pipeline_succeeds=False,  # GL-MR-001, medium
        auto_cancel_pending_pipelines="disabled",  # GL-COST-001, low
        build_timeout=7200,  # GL-REL-001 (threshold 3600), medium
    )
    ci = make_ci_snapshot([make_ci_image_ref(image="alpine:latest")])  # GL-CI-001, high
    report = evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)

    severities = [f.severity for f in report.findings]
    assert report.summary.critical == severities.count(Severity.CRITICAL)
    assert report.summary.high == severities.count(Severity.HIGH)
    assert report.summary.medium == severities.count(Severity.MEDIUM)
    assert report.summary.low == severities.count(Severity.LOW)
    assert report.summary.total == len(report.findings)
    assert report.summary.total > 0


# --- 9: timeout equal/below/above threshold behavior preserved --------------------


@pytest.mark.parametrize(
    ("build_timeout", "threshold", "expect_finding"),
    [
        (1800, 3600, False),
        (3600, 3600, False),
        (3601, 3600, True),
    ],
)
def test_timeout_threshold_boundary_behavior_is_preserved(
    build_timeout: int, threshold: int, expect_finding: bool
) -> None:
    project = make_project_snapshot(build_timeout=build_timeout)
    ci = make_ci_snapshot()
    report = evaluate_gitlab(
        project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=threshold
    )
    has_timeout_finding = any(f.check_id == "GL-REL-001" for f in report.findings)
    assert has_timeout_finding is expect_finding


# --- 10: invalid threshold retains the existing fixed sanitized error -------------


@pytest.mark.parametrize("bad_threshold", [0, -1, -100, True, False, 1.5, "3600", "", None])
def test_invalid_threshold_retains_existing_fixed_sanitized_error(bad_threshold: object) -> None:
    project = make_project_snapshot()
    ci = make_ci_snapshot()
    with pytest.raises(
        ValueError, match="job_timeout_threshold_seconds must be a positive integer"
    ):
        evaluate_gitlab(
            project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=bad_threshold
        )  # type: ignore[arg-type]


# --- 11: project-path mismatch: one fixed sanitized error, no path leaked ---------


def test_project_path_mismatch_fails_with_one_fixed_sanitized_error() -> None:
    project = make_project_snapshot(project_path="group/project-a")
    ci = make_ci_snapshot(project_path="group/project-b")
    with pytest.raises(ValueError) as excinfo:
        evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)
    message = str(excinfo.value)
    assert "group/project-a" not in message
    assert "group/project-b" not in message
    assert message == (
        "GitLab evaluation failed: the CI config snapshot's project path "
        "does not match the project snapshot's project path."
    )


# --- 12: project-path mismatch rejected before any check evaluator runs -----------


def test_project_path_mismatch_is_rejected_before_any_evaluator_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_check_evaluator_calls(monkeypatch)
    project = make_project_snapshot(project_path="group/project-a")
    ci = make_ci_snapshot(project_path="group/project-b")
    with pytest.raises(ValueError, match="does not match"):
        evaluator_module.evaluate_gitlab(
            project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600
        )


# --- 13: naive/non-datetime/broken-tzinfo audited_at is rejected ------------------


@pytest.mark.parametrize(
    "bad_audited_at",
    [
        dt.datetime(2026, 1, 1, 12, 0),  # naive
        "2026-01-01T12:00:00Z",  # not a datetime at all
        None,
        dt.date(2026, 1, 1),  # date, not datetime
        12345,
    ],
)
def test_invalid_audited_at_is_rejected(bad_audited_at: object) -> None:
    project = make_project_snapshot()
    ci = make_ci_snapshot()
    with pytest.raises(ValueError):
        evaluate_gitlab(project, ci, audited_at=bad_audited_at, job_timeout_threshold_seconds=3600)  # type: ignore[arg-type]


def test_broken_tzinfo_with_none_utcoffset_is_rejected() -> None:
    project = make_project_snapshot()
    ci = make_ci_snapshot()
    broken = dt.datetime(2026, 1, 1, 12, 0, tzinfo=_BrokenTzInfo())
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_gitlab(project, ci, audited_at=broken, job_timeout_threshold_seconds=3600)


def test_invalid_audited_at_is_rejected_before_any_evaluator_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_check_evaluator_calls(monkeypatch)
    project = make_project_snapshot()
    ci = make_ci_snapshot()
    with pytest.raises(ValueError):
        evaluator_module.evaluate_gitlab(
            project,
            ci,
            audited_at=dt.datetime(2026, 1, 1, 12, 0),
            job_timeout_threshold_seconds=3600,
        )


# --- 14: neither snapshot is mutated -----------------------------------------------


def test_does_not_mutate_either_snapshot() -> None:
    project = make_project_snapshot(rules=[make_rule(allow_force_push=True)])
    ci = make_ci_snapshot([make_ci_image_ref(image="alpine:latest")])
    original_project = project.model_copy(deep=True)
    original_ci = ci.model_copy(deep=True)
    evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)
    assert project == original_project
    assert ci == original_ci
    assert project.model_dump() == original_project.model_dump()
    assert ci.model_dump() == original_ci.model_dump()


# --- 15: repeated evaluation is deterministic --------------------------------------


def test_repeated_evaluation_with_identical_inputs_is_deterministic() -> None:
    project = make_project_snapshot(rules=[make_rule(allow_force_push=True)])
    ci = make_ci_snapshot([make_ci_image_ref(image="alpine:latest")])
    first = evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)
    second = evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)
    assert first == second


# --- 16: structural signature guard -------------------------------------------------


def test_function_signature_has_only_the_documented_parameters() -> None:
    signature = inspect.signature(evaluate_gitlab)
    assert list(signature.parameters) == [
        "project_snapshot",
        "ci_config_snapshot",
        "audited_at",
        "job_timeout_threshold_seconds",
    ]
    assert signature.parameters["audited_at"].kind == inspect.Parameter.KEYWORD_ONLY
    assert (
        signature.parameters["job_timeout_threshold_seconds"].kind == inspect.Parameter.KEYWORD_ONLY
    )


# --- 17: no I/O guard ----------------------------------------------------------------


def test_evaluate_gitlab_performs_no_network_or_filesystem_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("evaluate_gitlab must not perform network or filesystem I/O")

    monkeypatch.setattr(urllib3.PoolManager, "request", _forbidden)
    monkeypatch.setattr(urllib3.PoolManager, "urlopen", _forbidden)
    monkeypatch.setattr("builtins.open", _forbidden)

    project = make_project_snapshot(rules=[make_rule(allow_force_push=True)])
    ci = make_ci_snapshot([make_ci_image_ref(image="alpine:latest")])
    report = evaluate_gitlab(project, ci, audited_at=AUDITED_AT, job_timeout_threshold_seconds=3600)
    assert len(report.findings) >= 2


# --- 18: Kubernetes evaluate(...) remains exported and untouched by this module ---


def test_kubernetes_evaluate_remains_exported_alongside_evaluate_gitlab() -> None:
    from cloudops_guard.engine.evaluator import evaluate

    assert callable(evaluate)
    assert evaluate is not evaluate_gitlab


# --- 19: no check-specific condition/wording duplicated in the combined evaluator -


def test_combined_evaluator_source_contains_no_check_specific_finding_wording() -> None:
    source = "".join(
        inspect.getsource(func)
        for func in (
            evaluator_module.evaluate_gitlab,
            evaluator_module._validate_gitlab_snapshot_identity,
            evaluator_module._validate_gitlab_audited_at,
            evaluator_module._summarize_gitlab,
        )
    ).lower()
    forbidden_snippets = (
        "mutable tag",
        "force-push",
        "force push",
        "pipeline succeed",
        "job token",
        "pipeline-variable",
        "clone depth",
        "job timeout",
        "latest",
    )
    for snippet in forbidden_snippets:
        assert snippet not in source


# --- Structural: no client/collector/token/URL/output argument exists -------------


def test_evaluate_gitlab_accepts_no_client_collector_or_credential_argument() -> None:
    signature = inspect.signature(evaluate_gitlab)
    forbidden_names = {"client", "collector", "token", "url", "gitlab_url", "output", "path"}
    assert not (set(signature.parameters) & forbidden_names)
