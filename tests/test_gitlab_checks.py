"""Tests for the deterministic GitLab checks (v0.2.0 Phase 2C-A/2C-B,
`cloudops_guard.checks.gitlab`): the protected-default-branch checks
(`GL-BR-001` - `GL-BR-003`) and the project-setting checks (`GL-MR-001`,
`GL-SEC-001` - `GL-SEC-003`, `GL-COST-001`).

Only synthetic `GitLabProjectSnapshot`/`GitLabProtectedBranchRule`/
`GitLabProjectSettings` objects are used -- no `GitLabClient` or
`GitLabCollector` is instantiated, and no network access occurs. Rule
matching is exercised only through the public `evaluate_protected_branch_checks`
entry point (via whether `GL-BR-001` is suppressed or not), consistent with
this project's convention of testing checks through their public entry
point rather than private helpers.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cloudops_guard.checks.gitlab import (
    CHECK_BROAD_PIPELINE_VISIBILITY,
    CHECK_DEFAULT_BRANCH_NOT_PROTECTED,
    CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED,
    CHECK_DEVELOPER_VARIABLE_OVERRIDE_ALLOWED,
    CHECK_FORCE_PUSH_ALLOWED,
    CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED,
    CHECK_PIPELINE_SUCCESS_NOT_REQUIRED,
    CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED,
    evaluate_project_setting_checks,
    evaluate_protected_branch_checks,
)
from cloudops_guard.models import (
    GitLabFinding,
    GitLabProjectSettings,
    GitLabProjectSnapshot,
    GitLabProtectedBranchRule,
    GitLabResourceKind,
    Severity,
)

NOW = dt.datetime(2026, 8, 13, 9, 0, tzinfo=dt.UTC)


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


def make_rule(**overrides: object) -> GitLabProtectedBranchRule:
    defaults: dict[str, object] = {
        "name": "main",
        "allow_force_push": False,
        "role_push_access_levels": [],
    }
    defaults.update(overrides)
    return GitLabProtectedBranchRule(**defaults)


def make_snapshot(
    *,
    default_branch: str = "main",
    rules: list[GitLabProtectedBranchRule] | None = None,
    **project_overrides: object,
) -> GitLabProjectSnapshot:
    return GitLabProjectSnapshot(
        gitlab_url="https://gitlab.example.com",
        gitlab_version="18.4.1",
        enterprise=False,
        collected_at=NOW,
        project=make_project_settings(default_branch=default_branch, **project_overrides),
        protected_branches=[] if rules is None else rules,
    )


def make_project_setting_snapshot(**overrides: object) -> GitLabProjectSnapshot:
    """Build a snapshot where every Phase 2C-B project-setting check's
    condition is "safe" by default; pass overrides to make a specific
    setting trigger its finding.
    """
    defaults: dict[str, object] = {
        "only_allow_merge_if_pipeline_succeeds": True,
        "visibility": "private",
        "public_jobs": False,
        "ci_push_repository_for_job_token_allowed": False,
        "ci_pipeline_variables_minimum_override_role": "maintainer",
        "auto_cancel_pending_pipelines": "enabled",
    }
    defaults.update(overrides)
    return make_snapshot(rules=[], **defaults)


def check_ids(findings: list) -> list[str]:
    return [f.check_id for f in findings]


# --- Rule matching -----------------------------------------------------------
#
# Exercised through `evaluate_protected_branch_checks`: a single-rule
# snapshot suppresses GL-BR-001 exactly when that rule's name matches the
# default branch, so GL-BR-001's presence/absence is a direct signal of the
# matching outcome.


def test_exact_rule_matches() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[make_rule(name="main")])
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_exact_matching_is_case_sensitive() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[make_rule(name="Main")])
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_wildcard_matching_is_case_sensitive() -> None:
    snapshot = make_snapshot(default_branch="Release/1.2", rules=[make_rule(name="release/*")])
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_wildcard_matches_zero_characters() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[make_rule(name="main*")])
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_wildcard_matches_characters_across_slash() -> None:
    snapshot = make_snapshot(
        default_branch="master/gitlab/production", rules=[make_rule(name="*gitlab*")]
    )
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_prefix_wildcard() -> None:
    snapshot = make_snapshot(default_branch="release/1.2", rules=[make_rule(name="release/*")])
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_suffix_wildcard() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[make_rule(name="*main")])
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_middle_wildcard() -> None:
    snapshot = make_snapshot(
        default_branch="feature/team/release", rules=[make_rule(name="feature/*/release")]
    )
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_multiple_wildcard_characters() -> None:
    snapshot = make_snapshot(
        default_branch="feature/team/x/release/final", rules=[make_rule(name="feature/*/release/*")]
    )
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


@pytest.mark.parametrize(
    ("pattern", "branch"),
    [
        ("release.1", "releaseX1"),  # "." must be literal, not "any char"
        ("a+b", "aab"),  # "+" must be literal, not "one or more"
        ("a[b]c", "abc"),  # "[b]" must be literal, not a character class
        ("a(b)c", "abc"),  # "(b)" must be literal, not a group
        ("a$b", "ab"),  # "$" must be literal, not end-of-string anchor
        ("a^b", "ab"),  # "^" must be literal, not start-of-string anchor
    ],
)
def test_regex_significant_characters_are_treated_literally(pattern: str, branch: str) -> None:
    snapshot = make_snapshot(default_branch=branch, rules=[make_rule(name=pattern)])
    # The pattern does NOT match the branch under literal-character
    # semantics (only exact-equal or true-regex semantics would differ).
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


@pytest.mark.parametrize("pattern", ["release.1", "a+b", "a[b]c", "a(b)c", "a$b", "a^b"])
def test_regex_significant_characters_still_match_their_literal_self(pattern: str) -> None:
    snapshot = make_snapshot(default_branch=pattern, rules=[make_rule(name=pattern)])
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_inherited_matching_rule_is_included() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[make_rule(name="main", inherited=True)])
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


@pytest.mark.parametrize("inherited", [False, True, None])
def test_inherited_value_does_not_change_matching(inherited: bool | None) -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", inherited=inherited)]
    )
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_nonmatching_rules_are_ignored() -> None:
    snapshot = make_snapshot(
        default_branch="main",
        rules=[make_rule(name="develop"), make_rule(name="release/*")],
    )
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_snapshot_and_rules_are_not_mutated() -> None:
    rule = make_rule(name="main", allow_force_push=True, role_push_access_levels=[30])
    snapshot = make_snapshot(default_branch="main", rules=[rule])
    original_rules = list(snapshot.protected_branches)
    original_rule_copy = rule.model_copy(deep=True)

    evaluate_protected_branch_checks(snapshot, audited_at=NOW)

    assert snapshot.protected_branches == original_rules
    assert snapshot.protected_branches[0] == original_rule_copy
    assert snapshot.project.default_branch == "main"


# --- GL-BR-001 -----------------------------------------------------------------


def test_empty_protected_branch_list_produces_exactly_gl_br_001() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[])
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert check_ids(findings) == [CHECK_DEFAULT_BRANCH_NOT_PROTECTED]


def test_nonmatching_exact_rules_produce_gl_br_001() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[make_rule(name="develop")])
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert check_ids(findings) == [CHECK_DEFAULT_BRANCH_NOT_PROTECTED]


def test_nonmatching_wildcard_rules_produce_gl_br_001() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[make_rule(name="release/*")])
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert check_ids(findings) == [CHECK_DEFAULT_BRANCH_NOT_PROTECTED]


def test_matching_exact_rule_suppresses_gl_br_001() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[make_rule(name="main")])
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(findings)


def test_matching_wildcard_rule_suppresses_gl_br_001() -> None:
    snapshot = make_snapshot(default_branch="release/1.2", rules=[make_rule(name="release/*")])
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(findings)


def test_matching_inherited_rule_suppresses_gl_br_001() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[make_rule(name="main", inherited=True)])
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED not in check_ids(findings)


def test_gl_br_001_finding_fields_are_correct() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[])
    (finding,) = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert finding.check_id == "GL-BR-001"
    assert finding.title == "Default branch is not protected"
    assert finding.severity == Severity.HIGH
    assert finding.project_path == "group/subgroup/project"
    assert finding.resource_kind == GitLabResourceKind.PROTECTED_BRANCH
    assert finding.resource_name == "main"
    assert finding.job_name is None
    assert finding.auto_remediable is False
    assert finding.audited_at == NOW


def test_gl_br_001_evidence_contains_only_permitted_content() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[])
    (finding,) = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert "group/subgroup/project" in finding.evidence
    assert "main" in finding.evidence
    assert "no" in finding.evidence.lower() and "matched" in finding.evidence.lower()


def test_gl_br_001_recommendation_advises_creating_a_rule() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[])
    (finding,) = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert "protected-branch rule" in finding.recommendation


def test_gl_br_001_suppresses_gl_br_002_and_gl_br_003() -> None:
    # No matching rules at all -- there is nothing for GL-BR-002/003 to
    # evaluate, so only GL-BR-001 may appear, regardless of what a
    # nonmatching rule's own fields say.
    snapshot = make_snapshot(
        default_branch="main",
        rules=[make_rule(name="develop", allow_force_push=True, role_push_access_levels=[30])],
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert check_ids(findings) == [CHECK_DEFAULT_BRANCH_NOT_PROTECTED]


# --- GL-BR-002 -----------------------------------------------------------------


def test_matching_rule_with_force_push_true_produces_gl_br_002() -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", allow_force_push=True)]
    )
    assert CHECK_FORCE_PUSH_ALLOWED in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_matching_rule_with_force_push_false_does_not_produce_gl_br_002() -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", allow_force_push=False)]
    )
    assert CHECK_FORCE_PUSH_ALLOWED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_multiple_matching_false_rules_do_not_produce_gl_br_002() -> None:
    snapshot = make_snapshot(
        default_branch="main",
        rules=[
            make_rule(name="main", allow_force_push=False),
            make_rule(name="*", allow_force_push=False),
        ],
    )
    assert CHECK_FORCE_PUSH_ALLOWED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_true_plus_false_matching_rules_produce_exactly_one_gl_br_002() -> None:
    snapshot = make_snapshot(
        default_branch="main",
        rules=[
            make_rule(name="main", allow_force_push=False),
            make_rule(name="*", allow_force_push=True),
        ],
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert check_ids(findings).count(CHECK_FORCE_PUSH_ALLOWED) == 1


def test_force_push_true_on_a_nonmatching_rule_does_not_produce_gl_br_002() -> None:
    snapshot = make_snapshot(
        default_branch="main",
        rules=[
            make_rule(name="main", allow_force_push=False),
            make_rule(name="develop", allow_force_push=True),
        ],
    )
    assert CHECK_FORCE_PUSH_ALLOWED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_gl_br_002_finding_fields_and_evidence_are_correct() -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", allow_force_push=True)]
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    (finding,) = [f for f in findings if f.check_id == CHECK_FORCE_PUSH_ALLOWED]
    assert finding.title == "Force-push is allowed on the default branch"
    assert finding.severity == Severity.HIGH
    assert finding.project_path == "group/subgroup/project"
    assert finding.resource_kind == GitLabResourceKind.PROTECTED_BRANCH
    assert finding.resource_name == "main"
    assert finding.job_name is None
    assert finding.auto_remediable is False
    assert finding.audited_at == NOW
    assert "main" in finding.evidence
    assert "allowed" in finding.evidence.lower()
    assert "force-push" in finding.evidence.lower() or "force push" in finding.evidence.lower()
    assert "disable" in finding.recommendation.lower()


# --- GL-BR-003 -----------------------------------------------------------------


def test_developer_level_on_matching_rule_produces_gl_br_003() -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", role_push_access_levels=[30])]
    )
    assert CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


@pytest.mark.parametrize("level", [0, 40, 60])
def test_non_developer_levels_do_not_produce_gl_br_003(level: int) -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", role_push_access_levels=[level])]
    )
    assert CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_empty_role_list_does_not_produce_gl_br_003() -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", role_push_access_levels=[])]
    )
    assert CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_developer_plus_restrictive_matching_rules_produce_exactly_one_gl_br_003() -> None:
    snapshot = make_snapshot(
        default_branch="main",
        rules=[
            make_rule(name="main", role_push_access_levels=[40]),
            make_rule(name="*", role_push_access_levels=[30]),
        ],
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert check_ids(findings).count(CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED) == 1


def test_developer_on_a_nonmatching_rule_does_not_produce_gl_br_003() -> None:
    snapshot = make_snapshot(
        default_branch="main",
        rules=[
            make_rule(name="main", role_push_access_levels=[40]),
            make_rule(name="develop", role_push_access_levels=[30]),
        ],
    )
    assert CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED not in check_ids(
        evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    )


def test_gl_br_003_finding_fields_and_evidence_are_correct() -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", role_push_access_levels=[30])]
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    (finding,) = [f for f in findings if f.check_id == CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED]
    assert finding.title == "Developers can push directly to the default branch"
    assert finding.severity == Severity.MEDIUM
    assert finding.project_path == "group/subgroup/project"
    assert finding.resource_kind == GitLabResourceKind.PROTECTED_BRANCH
    assert finding.resource_name == "main"
    assert finding.job_name is None
    assert finding.auto_remediable is False
    assert finding.audited_at == NOW


def test_gl_br_003_evidence_states_a_grant_not_observed_usage() -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", role_push_access_levels=[30])]
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    (finding,) = [f for f in findings if f.check_id == CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED]
    assert "granted" in finding.evidence.lower()
    assert "not proof" in finding.evidence.lower() or "not observed" in finding.evidence.lower()


def test_gl_br_003_evidence_limits_scope_to_role_based_developer_access() -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", role_push_access_levels=[30])]
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    (finding,) = [f for f in findings if f.check_id == CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED]
    evidence_lower = finding.evidence.lower()
    assert "user-specific" in evidence_lower
    assert "group-specific" in evidence_lower
    assert "deploy-key-specific" in evidence_lower
    assert "custom-role" in evidence_lower
    assert "not evaluated" in evidence_lower


def test_gl_br_003_evidence_contains_no_identifiers() -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", role_push_access_levels=[30])]
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    (finding,) = [f for f in findings if f.check_id == CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED]
    for forbidden in ("user_id", "group_id", "deploy_key_id", "member_role_id"):
        assert forbidden not in finding.evidence


def test_gl_br_003_recommendation_advises_restricting_to_maintainer() -> None:
    snapshot = make_snapshot(
        default_branch="main", rules=[make_rule(name="main", role_push_access_levels=[30])]
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    (finding,) = [f for f in findings if f.check_id == CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED]
    assert "maintainer" in finding.recommendation.lower()
    assert "no one" in finding.recommendation.lower()
    assert "merge request" in finding.recommendation.lower()


# --- Combined behavior ---------------------------------------------------------


def test_matching_rule_with_force_push_and_developer_produces_both_in_stable_order() -> None:
    snapshot = make_snapshot(
        default_branch="main",
        rules=[make_rule(name="main", allow_force_push=True, role_push_access_levels=[30])],
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert check_ids(findings) == [CHECK_FORCE_PUSH_ALLOWED, CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED]


def test_no_matching_rule_produces_only_gl_br_001() -> None:
    snapshot = make_snapshot(default_branch="main", rules=[])
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert check_ids(findings) == [CHECK_DEFAULT_BRANCH_NOT_PROTECTED]


def test_safe_matching_rules_produce_no_findings() -> None:
    snapshot = make_snapshot(
        default_branch="main",
        rules=[make_rule(name="main", allow_force_push=False, role_push_access_levels=[40])],
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert findings == []


def test_every_finding_uses_the_supplied_audited_at_exactly() -> None:
    when = dt.datetime(2030, 1, 1, 3, 30, tzinfo=dt.UTC)
    snapshot = make_snapshot(
        default_branch="main",
        rules=[make_rule(name="main", allow_force_push=True, role_push_access_levels=[30])],
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=when)
    assert len(findings) == 2
    for finding in findings:
        assert finding.audited_at == when


def test_duplicate_matching_rules_never_create_duplicate_findings() -> None:
    snapshot = make_snapshot(
        default_branch="main",
        rules=[
            make_rule(name="main", allow_force_push=True, role_push_access_levels=[30]),
            make_rule(name="main", allow_force_push=True, role_push_access_levels=[30]),
            make_rule(name="*", allow_force_push=True, role_push_access_levels=[30]),
        ],
    )
    findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    assert check_ids(findings) == [CHECK_FORCE_PUSH_ALLOWED, CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED]


def test_check_constants_are_exact_and_stable() -> None:
    assert CHECK_DEFAULT_BRANCH_NOT_PROTECTED == "GL-BR-001"
    assert CHECK_FORCE_PUSH_ALLOWED == "GL-BR-002"
    assert CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED == "GL-BR-003"


def test_check_titles_are_exact_and_stable() -> None:
    no_rules_snapshot = make_snapshot(default_branch="main", rules=[])
    (br001,) = evaluate_protected_branch_checks(no_rules_snapshot, audited_at=NOW)
    assert br001.title == "Default branch is not protected"

    unsafe_snapshot = make_snapshot(
        default_branch="main",
        rules=[make_rule(name="main", allow_force_push=True, role_push_access_levels=[30])],
    )
    br002, br003 = evaluate_protected_branch_checks(unsafe_snapshot, audited_at=NOW)
    assert br002.title == "Force-push is allowed on the default branch"
    assert br003.title == "Developers can push directly to the default branch"


def _common_fields_are_correct(finding: GitLabFinding, expected_check_id: str) -> None:
    assert finding.check_id == expected_check_id
    assert finding.project_path == "group/subgroup/project"
    assert finding.resource_kind == GitLabResourceKind.PROJECT
    assert finding.resource_name == "group/subgroup/project"
    assert finding.job_name is None
    assert finding.auto_remediable is False
    assert finding.audited_at == NOW


# --- GL-MR-001 -----------------------------------------------------------------


def test_pipeline_success_not_required_false_produces_exactly_one_finding() -> None:
    snapshot = make_project_setting_snapshot(only_allow_merge_if_pipeline_succeeds=False)
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert [f.check_id for f in findings] == [CHECK_PIPELINE_SUCCESS_NOT_REQUIRED]


def test_pipeline_success_required_true_does_not_produce_finding() -> None:
    snapshot = make_project_setting_snapshot(only_allow_merge_if_pipeline_succeeds=True)
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert CHECK_PIPELINE_SUCCESS_NOT_REQUIRED not in [f.check_id for f in findings]


def test_gl_mr_001_is_independent_of_protected_branch_contents() -> None:
    unprotected = make_snapshot(
        default_branch="main",
        rules=[],
        only_allow_merge_if_pipeline_succeeds=False,
        visibility="private",
        public_jobs=False,
        ci_push_repository_for_job_token_allowed=False,
        ci_pipeline_variables_minimum_override_role="maintainer",
        auto_cancel_pending_pipelines="enabled",
    )
    protected = make_snapshot(
        default_branch="main",
        rules=[make_rule(name="main")],
        only_allow_merge_if_pipeline_succeeds=False,
        visibility="private",
        public_jobs=False,
        ci_push_repository_for_job_token_allowed=False,
        ci_pipeline_variables_minimum_override_role="maintainer",
        auto_cancel_pending_pipelines="enabled",
    )
    for snapshot in (unprotected, protected):
        findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
        assert [f.check_id for f in findings] == [CHECK_PIPELINE_SUCCESS_NOT_REQUIRED]


def test_gl_mr_001_finding_fields_severity_evidence_impact_recommendation() -> None:
    snapshot = make_project_setting_snapshot(only_allow_merge_if_pipeline_succeeds=False)
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    _common_fields_are_correct(finding, CHECK_PIPELINE_SUCCESS_NOT_REQUIRED)
    assert finding.title == "Successful pipelines are not required before merge"
    assert finding.severity == Severity.MEDIUM
    assert "disabled" in finding.evidence.lower()
    assert "pipelines must succeed" in finding.evidence.lower()
    assert (
        "gate merges" in finding.impact.lower() or "cannot reliably gate" in finding.impact.lower()
    )
    assert "enable" in finding.recommendation.lower()
    assert "pipelines must succeed" in finding.recommendation.lower()


def test_gl_mr_001_evidence_does_not_claim_ci_existence() -> None:
    snapshot = make_project_setting_snapshot(only_allow_merge_if_pipeline_succeeds=False)
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    evidence_lower = finding.evidence.lower()
    # Must not assert or deny the presence of CI configuration.
    assert "no ci" not in evidence_lower
    assert "has ci" not in evidence_lower
    assert ".gitlab-ci.yml" not in evidence_lower
    assert "pipeline exists" not in evidence_lower


# --- GL-SEC-001 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("visibility", "public_jobs", "expect_finding"),
    [
        ("private", True, True),
        ("private", False, False),
        ("internal", True, True),
        ("internal", False, False),
        ("public", True, False),
        ("public", False, False),
    ],
)
def test_public_jobs_visibility_combinations(
    visibility: str, public_jobs: bool, expect_finding: bool
) -> None:
    snapshot = make_project_setting_snapshot(visibility=visibility, public_jobs=public_jobs)
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    check_id_present = CHECK_BROAD_PIPELINE_VISIBILITY in [f.check_id for f in findings]
    assert check_id_present is expect_finding


def test_gl_sec_001_finding_fields_severity_title_and_recommendation() -> None:
    snapshot = make_project_setting_snapshot(visibility="private", public_jobs=True)
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    (finding,) = [f for f in findings if f.check_id == CHECK_BROAD_PIPELINE_VISIBILITY]
    _common_fields_are_correct(finding, CHECK_BROAD_PIPELINE_VISIBILITY)
    assert finding.title == "CI/CD pipeline details have broad visibility"
    assert finding.severity == Severity.HIGH
    assert "disable" in finding.recommendation.lower()
    assert "deliberate and approved" in finding.recommendation.lower()


def test_gl_sec_001_private_evidence_mentions_all_members_including_guests() -> None:
    snapshot = make_project_setting_snapshot(visibility="private", public_jobs=True)
    (finding,) = [
        f
        for f in evaluate_project_setting_checks(snapshot, audited_at=NOW)
        if f.check_id == CHECK_BROAD_PIPELINE_VISIBILITY
    ]
    evidence_lower = finding.evidence.lower()
    assert "private" in evidence_lower
    assert "all project members" in evidence_lower
    assert "guests" in evidence_lower


def test_gl_sec_001_internal_evidence_mentions_authenticated_non_external_users() -> None:
    snapshot = make_project_setting_snapshot(visibility="internal", public_jobs=True)
    (finding,) = [
        f
        for f in evaluate_project_setting_checks(snapshot, audited_at=NOW)
        if f.check_id == CHECK_BROAD_PIPELINE_VISIBILITY
    ]
    evidence_lower = finding.evidence.lower()
    assert "internal" in evidence_lower
    assert "authenticated" in evidence_lower
    assert "non-external" in evidence_lower
    assert "non-project members" in evidence_lower


def test_gl_sec_001_evidence_never_claims_private_project_is_public_or_outside_reach() -> None:
    snapshot = make_project_setting_snapshot(visibility="private", public_jobs=True)
    (finding,) = [
        f
        for f in evaluate_project_setting_checks(snapshot, audited_at=NOW)
        if f.check_id == CHECK_BROAD_PIPELINE_VISIBILITY
    ]
    combined_lower = (finding.title + " " + finding.evidence + " " + finding.impact).lower()
    assert "is public" not in combined_lower
    assert "outsider" not in combined_lower
    assert "non-project members" not in combined_lower  # only true for internal, not private


def test_gl_sec_001_impact_states_configuration_does_not_prove_viewing() -> None:
    snapshot = make_project_setting_snapshot(visibility="private", public_jobs=True)
    (finding,) = [
        f
        for f in evaluate_project_setting_checks(snapshot, audited_at=NOW)
        if f.check_id == CHECK_BROAD_PIPELINE_VISIBILITY
    ]
    impact_lower = finding.impact.lower()
    assert "does not prove" in impact_lower
    assert "viewed" in impact_lower


def test_gl_sec_001_impact_notes_possible_false_positive() -> None:
    snapshot = make_project_setting_snapshot(visibility="private", public_jobs=True)
    (finding,) = [
        f
        for f in evaluate_project_setting_checks(snapshot, audited_at=NOW)
        if f.check_id == CHECK_BROAD_PIPELINE_VISIBILITY
    ]
    impact_lower = finding.impact.lower()
    assert "intentional" in impact_lower
    assert "false positive" in impact_lower


def test_gl_sec_001_impact_states_no_pipeline_content_is_fetched() -> None:
    snapshot = make_project_setting_snapshot(visibility="private", public_jobs=True)
    (finding,) = [
        f
        for f in evaluate_project_setting_checks(snapshot, audited_at=NOW)
        if f.check_id == CHECK_BROAD_PIPELINE_VISIBILITY
    ]
    impact_lower = finding.impact.lower()
    assert "never fetches" in impact_lower
    assert "logs" in impact_lower
    assert "artifact" in impact_lower
    assert "security" in impact_lower


def test_gl_sec_001_impact_states_the_actual_risk() -> None:
    # Task 3: the impact must actually name the risk (logs/artifacts/security
    # scan info may be seen by a broader audience), not just the three
    # qualifications around it.
    snapshot = make_project_setting_snapshot(visibility="private", public_jobs=True)
    (finding,) = [
        f
        for f in evaluate_project_setting_checks(snapshot, audited_at=NOW)
        if f.check_id == CHECK_BROAD_PIPELINE_VISIBILITY
    ]
    impact_lower = finding.impact.lower()
    assert "pipeline logs" in impact_lower
    assert "artifacts" in impact_lower
    assert "pipeline security scan information" in impact_lower
    assert "broader audience than intended" in impact_lower


@pytest.mark.parametrize("visibility", ["private", "internal"])
def test_gl_sec_001_evidence_contains_no_member_identity(visibility: str) -> None:
    snapshot = make_project_setting_snapshot(visibility=visibility, public_jobs=True)
    (finding,) = [
        f
        for f in evaluate_project_setting_checks(snapshot, audited_at=NOW)
        if f.check_id == CHECK_BROAD_PIPELINE_VISIBILITY
    ]
    for forbidden in ("user_id", "username", "email", "@", "name:"):
        assert forbidden not in finding.evidence.lower()


# --- GL-SEC-002 -----------------------------------------------------------------


def test_job_token_push_true_produces_exactly_one_finding() -> None:
    snapshot = make_project_setting_snapshot(ci_push_repository_for_job_token_allowed=True)
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert [f.check_id for f in findings] == [CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED]


def test_job_token_push_false_does_not_produce_finding() -> None:
    snapshot = make_project_setting_snapshot(ci_push_repository_for_job_token_allowed=False)
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED not in [f.check_id for f in findings]


def test_gl_sec_002_evidence_describes_permission_not_observed_usage() -> None:
    snapshot = make_project_setting_snapshot(ci_push_repository_for_job_token_allowed=True)
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    evidence_lower = finding.evidence.lower()
    assert "enabled" in evidence_lower
    assert "pushed" not in evidence_lower and "was used" not in evidence_lower


@pytest.mark.parametrize(
    "forbidden",
    [
        "glpat-",
        "token=",
        "Authorization",
        "PRIVATE-TOKEN",
        "pipeline_id",
        "job_id",
        "trace",
        "log",
    ],
)
def test_gl_sec_002_evidence_contains_no_token_or_credential_data(forbidden: str) -> None:
    snapshot = make_project_setting_snapshot(ci_push_repository_for_job_token_allowed=True)
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert forbidden.lower() not in finding.evidence.lower()


def test_gl_sec_002_finding_fields_severity_recommendation() -> None:
    snapshot = make_project_setting_snapshot(ci_push_repository_for_job_token_allowed=True)
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    _common_fields_are_correct(finding, CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED)
    assert finding.title == "CI job tokens are permitted to push to the repository"
    assert finding.severity == Severity.HIGH
    assert "disable" in finding.recommendation.lower()


# --- GL-SEC-003 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "expect_finding"),
    [
        ("developer", True),
        ("maintainer", False),
        ("owner", False),
        ("no_one_allowed", False),
    ],
)
def test_override_role_values(role: str, expect_finding: bool) -> None:
    snapshot = make_project_setting_snapshot(ci_pipeline_variables_minimum_override_role=role)
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    check_id_present = CHECK_DEVELOPER_VARIABLE_OVERRIDE_ALLOWED in [f.check_id for f in findings]
    assert check_id_present is expect_finding


def test_gl_sec_003_evidence_reports_permission_not_actual_use() -> None:
    snapshot = make_project_setting_snapshot(
        ci_pipeline_variables_minimum_override_role="developer"
    )
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    evidence_lower = finding.evidence.lower()
    assert "developer" in evidence_lower
    assert "overrode" not in evidence_lower and "was overridden" not in evidence_lower


def test_gl_sec_003_evidence_contains_no_variable_name_value_or_script() -> None:
    snapshot = make_project_setting_snapshot(
        ci_pipeline_variables_minimum_override_role="developer"
    )
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    for forbidden in ("CI_", "$", "script:", "echo ", "export "):
        assert forbidden not in finding.evidence


def test_gl_sec_003_finding_fields_severity_recommendation() -> None:
    snapshot = make_project_setting_snapshot(
        ci_pipeline_variables_minimum_override_role="developer"
    )
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    _common_fields_are_correct(finding, CHECK_DEVELOPER_VARIABLE_OVERRIDE_ALLOWED)
    assert (
        finding.title
        == "Pipeline-variable override permissions are more permissive than Maintainer"
    )
    assert finding.severity == Severity.HIGH
    assert "maintainer" in finding.recommendation.lower()


# --- GL-COST-001 -----------------------------------------------------------------


def test_auto_cancel_disabled_produces_exactly_one_finding() -> None:
    snapshot = make_project_setting_snapshot(auto_cancel_pending_pipelines="disabled")
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert [f.check_id for f in findings] == [CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED]


def test_auto_cancel_enabled_does_not_produce_finding() -> None:
    snapshot = make_project_setting_snapshot(auto_cancel_pending_pipelines="enabled")
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED not in [f.check_id for f in findings]


def test_gl_cost_001_evidence_reflects_the_string_setting() -> None:
    snapshot = make_project_setting_snapshot(auto_cancel_pending_pipelines="disabled")
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert "disabled" in finding.evidence.lower()


def test_gl_cost_001_wording_does_not_claim_observed_waste() -> None:
    snapshot = make_project_setting_snapshot(auto_cancel_pending_pipelines="disabled")
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    combined_lower = (finding.evidence + " " + finding.impact).lower()
    assert "wasted" not in combined_lower
    assert "was consumed" not in combined_lower
    assert "occurred" not in combined_lower


def test_gl_cost_001_finding_fields_severity_recommendation() -> None:
    snapshot = make_project_setting_snapshot(auto_cancel_pending_pipelines="disabled")
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    _common_fields_are_correct(finding, CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED)
    assert finding.title == "Redundant pipelines are not automatically cancelled"
    assert finding.severity == Severity.LOW
    assert "enable" in finding.recommendation.lower()


def test_gl_cost_001_impact_mentions_pending_pipelines() -> None:
    snapshot = make_project_setting_snapshot(auto_cancel_pending_pipelines="disabled")
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert "pending pipelines" in finding.impact.lower()


def test_gl_cost_001_impact_mentions_interruptible_running_pipelines() -> None:
    snapshot = make_project_setting_snapshot(auto_cancel_pending_pipelines="disabled")
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    impact_lower = finding.impact.lower()
    assert "interruptible" in impact_lower
    assert "running pipelines" in impact_lower


def test_gl_cost_001_impact_uses_conditional_not_observed_wording() -> None:
    snapshot = make_project_setting_snapshot(auto_cancel_pending_pipelines="disabled")
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    impact_lower = finding.impact.lower()
    # Conditional ("may") language for both categories of pipeline named above.
    assert impact_lower.count(" may ") == 2
    assert impact_lower.count("continue consuming") == 2
    assert "wasted" not in impact_lower
    assert "was consumed" not in impact_lower
    assert "occurred" not in impact_lower


def test_gl_cost_001_recommendation_recognizes_compliance_audit_history_exception() -> None:
    snapshot = make_project_setting_snapshot(auto_cancel_pending_pipelines="disabled")
    (finding,) = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    recommendation_lower = finding.recommendation.lower()
    assert "enable" in recommendation_lower
    assert "compliance" in recommendation_lower
    assert "audit-history" in recommendation_lower or "audit history" in recommendation_lower
    assert "intentionally requires" in recommendation_lower


# --- Combined behavior (project-setting checks) ---------------------------------


def test_all_five_unsafe_settings_produce_findings_in_stable_order() -> None:
    snapshot = make_project_setting_snapshot(
        only_allow_merge_if_pipeline_succeeds=False,
        visibility="private",
        public_jobs=True,
        ci_push_repository_for_job_token_allowed=True,
        ci_pipeline_variables_minimum_override_role="developer",
        auto_cancel_pending_pipelines="disabled",
    )
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert [f.check_id for f in findings] == [
        CHECK_PIPELINE_SUCCESS_NOT_REQUIRED,
        CHECK_BROAD_PIPELINE_VISIBILITY,
        CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED,
        CHECK_DEVELOPER_VARIABLE_OVERRIDE_ALLOWED,
        CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED,
    ]


def test_all_safe_settings_produce_no_findings() -> None:
    snapshot = make_project_setting_snapshot()
    assert evaluate_project_setting_checks(snapshot, audited_at=NOW) == []


def test_every_project_setting_finding_uses_the_supplied_audited_at_exactly() -> None:
    when = dt.datetime(2031, 5, 5, 6, 0, tzinfo=dt.UTC)
    snapshot = make_project_setting_snapshot(
        only_allow_merge_if_pipeline_succeeds=False,
        visibility="private",
        public_jobs=True,
        ci_push_repository_for_job_token_allowed=True,
        ci_pipeline_variables_minimum_override_role="developer",
        auto_cancel_pending_pipelines="disabled",
    )
    findings = evaluate_project_setting_checks(snapshot, audited_at=when)
    assert len(findings) == 5
    for finding in findings:
        assert finding.audited_at == when


def test_project_setting_checks_do_not_mutate_snapshot() -> None:
    snapshot = make_project_setting_snapshot(only_allow_merge_if_pipeline_succeeds=False)
    original_project = snapshot.project.model_copy(deep=True)
    evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert snapshot.project == original_project


def test_project_setting_check_constants_are_exact_and_stable() -> None:
    assert CHECK_PIPELINE_SUCCESS_NOT_REQUIRED == "GL-MR-001"
    assert CHECK_BROAD_PIPELINE_VISIBILITY == "GL-SEC-001"
    assert CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED == "GL-SEC-002"
    assert CHECK_DEVELOPER_VARIABLE_OVERRIDE_ALLOWED == "GL-SEC-003"
    assert CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED == "GL-COST-001"


def test_project_setting_check_titles_are_exact_and_stable() -> None:
    snapshot = make_project_setting_snapshot(
        only_allow_merge_if_pipeline_succeeds=False,
        visibility="private",
        public_jobs=True,
        ci_push_repository_for_job_token_allowed=True,
        ci_pipeline_variables_minimum_override_role="developer",
        auto_cancel_pending_pipelines="disabled",
    )
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    titles = {f.check_id: f.title for f in findings}
    assert titles[CHECK_PIPELINE_SUCCESS_NOT_REQUIRED] == (
        "Successful pipelines are not required before merge"
    )
    assert titles[CHECK_BROAD_PIPELINE_VISIBILITY] == (
        "CI/CD pipeline details have broad visibility"
    )
    assert titles[CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED] == (
        "CI job tokens are permitted to push to the repository"
    )
    assert titles[CHECK_DEVELOPER_VARIABLE_OVERRIDE_ALLOWED] == (
        "Pipeline-variable override permissions are more permissive than Maintainer"
    )
    assert titles[CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED] == (
        "Redundant pipelines are not automatically cancelled"
    )


@pytest.mark.parametrize(
    ("override", "expected_check_id"),
    [
        ({"only_allow_merge_if_pipeline_succeeds": False}, CHECK_PIPELINE_SUCCESS_NOT_REQUIRED),
        (
            {"visibility": "private", "public_jobs": True},
            CHECK_BROAD_PIPELINE_VISIBILITY,
        ),
        (
            {"ci_push_repository_for_job_token_allowed": True},
            CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED,
        ),
        (
            {"ci_pipeline_variables_minimum_override_role": "developer"},
            CHECK_DEVELOPER_VARIABLE_OVERRIDE_ALLOWED,
        ),
        ({"auto_cancel_pending_pipelines": "disabled"}, CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED),
    ],
)
def test_one_unsafe_field_does_not_trigger_unrelated_checks(
    override: dict[str, object], expected_check_id: str
) -> None:
    snapshot = make_project_setting_snapshot(**override)
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert [f.check_id for f in findings] == [expected_check_id]


def test_every_project_setting_finding_has_resource_kind_project() -> None:
    snapshot = make_project_setting_snapshot(
        only_allow_merge_if_pipeline_succeeds=False,
        visibility="private",
        public_jobs=True,
        ci_push_repository_for_job_token_allowed=True,
        ci_pipeline_variables_minimum_override_role="developer",
        auto_cancel_pending_pipelines="disabled",
    )
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert len(findings) == 5
    for finding in findings:
        assert finding.resource_kind == GitLabResourceKind.PROJECT


def test_every_project_setting_finding_uses_project_path_as_resource_name() -> None:
    snapshot = make_project_setting_snapshot(
        only_allow_merge_if_pipeline_succeeds=False,
        visibility="private",
        public_jobs=True,
        ci_push_repository_for_job_token_allowed=True,
        ci_pipeline_variables_minimum_override_role="developer",
        auto_cancel_pending_pipelines="disabled",
    )
    findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert len(findings) == 5
    for finding in findings:
        assert finding.resource_name == snapshot.project.project_path


def test_project_setting_findings_do_not_depend_on_protected_branch_rules() -> None:
    common_overrides: dict[str, object] = {
        "only_allow_merge_if_pipeline_succeeds": False,
        "visibility": "private",
        "public_jobs": True,
        "ci_push_repository_for_job_token_allowed": True,
        "ci_pipeline_variables_minimum_override_role": "developer",
        "auto_cancel_pending_pipelines": "disabled",
    }
    no_rules = make_snapshot(default_branch="main", rules=[], **common_overrides)
    with_rules = make_snapshot(
        default_branch="main",
        rules=[make_rule(name="main", allow_force_push=True, role_push_access_levels=[30])],
        **common_overrides,
    )
    findings_no_rules = evaluate_project_setting_checks(no_rules, audited_at=NOW)
    findings_with_rules = evaluate_project_setting_checks(with_rules, audited_at=NOW)
    assert [f.check_id for f in findings_no_rules] == [f.check_id for f in findings_with_rules]


def test_duplicate_evaluation_does_not_mutate_state_or_change_output() -> None:
    snapshot = make_project_setting_snapshot(
        only_allow_merge_if_pipeline_succeeds=False,
        visibility="private",
        public_jobs=True,
        ci_push_repository_for_job_token_allowed=True,
        ci_pipeline_variables_minimum_override_role="developer",
        auto_cancel_pending_pipelines="disabled",
    )
    original_project = snapshot.project.model_copy(deep=True)
    first = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    second = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert [f.check_id for f in first] == [f.check_id for f in second]
    assert first == second
    assert snapshot.project == original_project


def test_protected_branch_checks_are_unaffected_by_project_setting_evaluation() -> None:
    # Phase 2C-A behavior must remain unchanged: evaluating the new
    # project-setting checks on a snapshot must not influence, and must not
    # be influenced by, the protected-branch check results for that same
    # snapshot.
    snapshot = make_snapshot(
        default_branch="main",
        rules=[make_rule(name="main", allow_force_push=True, role_push_access_levels=[30])],
        only_allow_merge_if_pipeline_succeeds=False,
        visibility="private",
        public_jobs=True,
        ci_push_repository_for_job_token_allowed=True,
        ci_pipeline_variables_minimum_override_role="developer",
        auto_cancel_pending_pipelines="disabled",
    )
    branch_findings = evaluate_protected_branch_checks(snapshot, audited_at=NOW)
    setting_findings = evaluate_project_setting_checks(snapshot, audited_at=NOW)
    assert [f.check_id for f in branch_findings] == [
        CHECK_FORCE_PUSH_ALLOWED,
        CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED,
    ]
    assert [f.check_id for f in setting_findings] == [
        CHECK_PIPELINE_SUCCESS_NOT_REQUIRED,
        CHECK_BROAD_PIPELINE_VISIBILITY,
        CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED,
        CHECK_DEVELOPER_VARIABLE_OVERRIDE_ALLOWED,
        CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED,
    ]
