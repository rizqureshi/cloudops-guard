"""Deterministic GitLab checks (v0.2.0 Phase 2C-A/2C-B/2C-C/2C-D2).

Each check operates only on the normalized `GitLabProjectSnapshot` produced
by the Phase 2B collector (`cloudops_guard.collectors.gitlab.GitLabCollector`)
-- never on `GitLabClient`/`GitLabCollector` directly, and never on any raw
GitLab API response. This module performs no HTTP, filesystem,
environment-variable, or subprocess access, and never mutates the snapshot
it is given.

Phase 2C-A implements the three protected-default-branch checks
(`GL-BR-001` - `GL-BR-003`, entry point `evaluate_protected_branch_checks`).
Phase 2C-B adds five project-setting checks (`GL-MR-001`, `GL-SEC-001` -
`GL-SEC-003`, `GL-COST-001`, entry point `evaluate_project_setting_checks`) --
kept as a separate entry point rather than folded into the protected-branch
one. Phase 2C-C adds `GL-REL-001` (entry point `evaluate_job_timeout_check`),
also kept as its own separate entry point -- it requires a caller-supplied
`job_timeout_threshold_seconds` with no product-level default yet, so it is
not folded into `evaluate_project_setting_checks` either. Phase 2C-D2 adds
`GL-COST-002` into `evaluate_project_setting_checks` (it needs no additional
argument beyond the normalized snapshot). There is still no combined
all-GitLab evaluator. `GL-CI-001` image inspection, evaluator/report
integration, and CLI wiring remain separate, later work -- see
`docs/milestones/v0.2.0-gitlab-audit.md`.
"""

from __future__ import annotations

import datetime as dt
import re

from cloudops_guard.models import (
    GitLabFinding,
    GitLabProjectSnapshot,
    GitLabProtectedBranchRule,
    GitLabResourceKind,
    Severity,
)

CHECK_DEFAULT_BRANCH_NOT_PROTECTED = "GL-BR-001"
CHECK_FORCE_PUSH_ALLOWED = "GL-BR-002"
CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED = "GL-BR-003"
CHECK_PIPELINE_SUCCESS_NOT_REQUIRED = "GL-MR-001"
CHECK_BROAD_PIPELINE_VISIBILITY = "GL-SEC-001"
CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED = "GL-SEC-002"
CHECK_DEVELOPER_VARIABLE_OVERRIDE_ALLOWED = "GL-SEC-003"
CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED = "GL-COST-001"
CHECK_UNLIMITED_GIT_CLONE_DEPTH = "GL-COST-002"
CHECK_JOB_TIMEOUT_EXCEEDS_THRESHOLD = "GL-REL-001"

_DEVELOPER_ROLE_LEVEL = 30
_DEVELOPER_OVERRIDE_ROLE = "developer"
_NON_PUBLIC_VISIBILITIES = ("private", "internal")
_AUTO_CANCEL_DISABLED = "disabled"

_WILDCARD_TOKEN = "*"
_ESCAPED_WILDCARD_TOKEN = re.escape(_WILDCARD_TOKEN)


def _protected_branch_name_matches(pattern: str, branch_name: str) -> bool:
    """True if protected-branch rule name `pattern` matches `branch_name`.

    Matching is explicit and narrow, not operating-system-dependent glob
    matching:
    - Case-sensitive.
    - A `pattern` with no `*` must match `branch_name` exactly.
    - A literal `*` matches zero or more characters, including `/` -- so a
      wildcard can span path-like segments (e.g. "release/*" matches
      "release/1.2"; "feature/*/release" matches "feature/team/release").
    - Every other character in `pattern` is matched literally: `pattern` is
      regex-escaped via `re.escape` *before* the resulting escaped wildcard
      token is replaced with `.*`, so a regex-significant character in a
      real rule name (e.g. `.`, `[`, `+`, `(`) is never treated as a
      metacharacter. No other wildcard syntax (`?`, character classes,
      brace expansion, etc.) is introduced.
    """
    escaped_pattern = re.escape(pattern)
    regex_pattern = escaped_pattern.replace(_ESCAPED_WILDCARD_TOKEN, ".*")
    return re.fullmatch(regex_pattern, branch_name, flags=re.DOTALL) is not None


def _matching_protected_branch_rules(
    snapshot: GitLabProjectSnapshot,
) -> list[GitLabProtectedBranchRule]:
    """Every normalized rule whose name matches the project's default branch.

    Exact, wildcard, project-level, and inherited rules are all treated
    equally here -- a rule is never excluded because `inherited` is `True`
    or `None`. Reads `snapshot` only; never mutates it or the rules within
    it.
    """
    default_branch = snapshot.project.default_branch
    return [
        rule
        for rule in snapshot.protected_branches
        if _protected_branch_name_matches(rule.name, default_branch)
    ]


def _build_default_branch_not_protected_finding(
    snapshot: GitLabProjectSnapshot, audited_at: dt.datetime
) -> GitLabFinding:
    default_branch = snapshot.project.default_branch
    project_path = snapshot.project.project_path
    return GitLabFinding(
        check_id=CHECK_DEFAULT_BRANCH_NOT_PROTECTED,
        title="Default branch is not protected",
        severity=Severity.HIGH,
        project_path=project_path,
        resource_kind=GitLabResourceKind.PROTECTED_BRANCH,
        resource_name=default_branch,
        job_name=None,
        evidence=(
            f"Project path: '{project_path}'. Default branch: '{default_branch}'. "
            "No exact, wildcard, or inherited protected-branch rule matched the "
            "default branch."
        ),
        impact="An unprotected default branch permits force-push, deletion, and "
        "unreviewed direct pushes by anyone with push access. The default branch is "
        "typically the deployment/release source of truth.",
        recommendation="Create a protected-branch rule whose name matches the default "
        "branch, with push and merge access restricted appropriately.",
        auto_remediable=False,
        audited_at=audited_at,
    )


def _build_force_push_allowed_finding(
    snapshot: GitLabProjectSnapshot,
    matching_rules: list[GitLabProtectedBranchRule],
    audited_at: dt.datetime,
) -> GitLabFinding | None:
    # Most-permissive-matching-rule behavior: a `False` on another matching
    # rule must not cancel a `True` found on any matching rule.
    if not any(rule.allow_force_push for rule in matching_rules):
        return None
    default_branch = snapshot.project.default_branch
    return GitLabFinding(
        check_id=CHECK_FORCE_PUSH_ALLOWED,
        title="Force-push is allowed on the default branch",
        severity=Severity.HIGH,
        project_path=snapshot.project.project_path,
        resource_kind=GitLabResourceKind.PROTECTED_BRANCH,
        resource_name=default_branch,
        job_name=None,
        evidence=(
            f"Default branch: '{default_branch}'. Resolved effective force-push "
            "permission: allowed."
        ),
        impact="Force-push on the default branch can rewrite or destroy commit "
        "history, obscure prior changes, and break collaborators relying on that "
        "history -- undermining any audit trail.",
        recommendation="Disable 'Allowed to force push' on every protected-branch "
        "rule matching the default branch.",
        auto_remediable=False,
        audited_at=audited_at,
    )


def _build_developer_direct_push_finding(
    snapshot: GitLabProjectSnapshot,
    matching_rules: list[GitLabProtectedBranchRule],
    audited_at: dt.datetime,
) -> GitLabFinding | None:
    # A Developer grant on one matching rule is a finding even if another
    # matching rule is more restrictive -- most-permissive-wins, same as
    # GL-BR-002.
    if not any(_DEVELOPER_ROLE_LEVEL in rule.role_push_access_levels for rule in matching_rules):
        return None
    default_branch = snapshot.project.default_branch
    return GitLabFinding(
        check_id=CHECK_DEVELOPER_DIRECT_PUSH_ALLOWED,
        title="Developers can push directly to the default branch",
        severity=Severity.MEDIUM,
        project_path=snapshot.project.project_path,
        resource_kind=GitLabResourceKind.PROTECTED_BRANCH,
        resource_name=default_branch,
        job_name=None,
        evidence=(
            f"Default branch: '{default_branch}'. The Developer role-based "
            "direct-push permission is granted on at least one matching "
            "protected-branch rule. This reports a permission grant, not proof "
            "that a developer has actually pushed directly to the default branch. "
            "User-specific, group-specific, deploy-key-specific, and custom-role "
            "grants were not evaluated."
        ),
        impact="Allowing direct pushes at the Developer role bypasses merge "
        "request review for a broad set of contributors, reducing change "
        "visibility.",
        recommendation="Restrict default-branch push access to Maintainer or "
        "'No one', and require changes via merge request.",
        auto_remediable=False,
        audited_at=audited_at,
    )


def evaluate_protected_branch_checks(
    snapshot: GitLabProjectSnapshot,
    *,
    audited_at: dt.datetime,
) -> list[GitLabFinding]:
    """Evaluate `GL-BR-001`, `GL-BR-002`, and `GL-BR-003` against `snapshot`.

    Operates only on the already-collected, normalized `snapshot`; never
    retrieves any additional GitLab data and never mutates `snapshot`.
    `audited_at` is used exactly as supplied (never `datetime.now()`) so
    results are deterministic.

    Findings are returned in stable `GL-BR-001`, `GL-BR-002`, `GL-BR-003`
    order. If no protected-branch rule matches the default branch, only
    `GL-BR-001` is produced -- there are no matching rules for `GL-BR-002`
    or `GL-BR-003` to evaluate in that case.
    """
    matching_rules = _matching_protected_branch_rules(snapshot)

    if not matching_rules:
        return [_build_default_branch_not_protected_finding(snapshot, audited_at)]

    findings: list[GitLabFinding] = []
    force_push_finding = _build_force_push_allowed_finding(snapshot, matching_rules, audited_at)
    if force_push_finding is not None:
        findings.append(force_push_finding)
    developer_push_finding = _build_developer_direct_push_finding(
        snapshot, matching_rules, audited_at
    )
    if developer_push_finding is not None:
        findings.append(developer_push_finding)
    return findings


# --- Phase 2C-B: project-setting checks ------------------------------------------


def _build_project_setting_finding(
    *,
    check_id: str,
    title: str,
    severity: Severity,
    snapshot: GitLabProjectSnapshot,
    evidence: str,
    impact: str,
    recommendation: str,
    audited_at: dt.datetime,
) -> GitLabFinding:
    """Shared field assembly for a project-level finding (Phase 2C-B and 2C-C).

    Every project-setting check (the five in this section, plus `GL-REL-001`
    in the Phase 2C-C section below) reports the same resource identity: the
    project itself (not a specific branch/job), so `project_path`,
    `resource_kind`, `resource_name`, and `job_name` are identical across
    all of them.
    """
    project_path = snapshot.project.project_path
    return GitLabFinding(
        check_id=check_id,
        title=title,
        severity=severity,
        project_path=project_path,
        resource_kind=GitLabResourceKind.PROJECT,
        resource_name=project_path,
        job_name=None,
        evidence=evidence,
        impact=impact,
        recommendation=recommendation,
        auto_remediable=False,
        audited_at=audited_at,
    )


def _check_pipeline_success_not_required(
    snapshot: GitLabProjectSnapshot, audited_at: dt.datetime
) -> GitLabFinding | None:
    # Always evaluated -- the snapshot carries no CI-configuration-presence
    # field, and this setting matters regardless of whether CI happens to
    # be configured yet (see the milestone doc's GL-MR-001 note).
    if snapshot.project.only_allow_merge_if_pipeline_succeeds:
        return None
    return _build_project_setting_finding(
        check_id=CHECK_PIPELINE_SUCCESS_NOT_REQUIRED,
        title="Successful pipelines are not required before merge",
        severity=Severity.MEDIUM,
        snapshot=snapshot,
        evidence=(
            "The 'Pipelines must succeed' setting "
            "(only_allow_merge_if_pipeline_succeeds) is disabled."
        ),
        impact="Merge requests can be merged even when a pipeline fails, so any "
        "configured tests, builds, or security scans cannot reliably gate merges "
        "while this setting is disabled.",
        recommendation="Enable 'Pipelines must succeed' in the project's merge request settings.",
        audited_at=audited_at,
    )


def _check_public_jobs_on_non_public_project(
    snapshot: GitLabProjectSnapshot, audited_at: dt.datetime
) -> GitLabFinding | None:
    # Per GitLab's documentation, "Public pipelines" never exposes a private
    # or internal project's pipelines to unauthenticated outsiders -- but
    # the audience it does expose them to differs by visibility, and is NOT
    # uniformly "project members only":
    # - "private": all project members, including Guests -- NOT
    #   non-project members.
    # - "internal": any authenticated, non-external instance user --
    #   this DOES include non-project members, unlike "private".
    # The evidence text below is intentionally visibility-specific rather
    # than a blanket "public" claim or a blanket "members only" claim.
    project = snapshot.project
    if project.visibility not in _NON_PUBLIC_VISIBILITIES:
        return None
    if not project.public_jobs:
        return None
    if project.visibility == "private":
        audience = "all project members, including Guests"
    else:
        audience = "authenticated non-external users, including non-project members"
    return _build_project_setting_finding(
        check_id=CHECK_BROAD_PIPELINE_VISIBILITY,
        title="CI/CD pipeline details have broad visibility",
        severity=Severity.HIGH,
        snapshot=snapshot,
        evidence=(
            f"Project visibility: '{project.visibility}'. Public pipelines "
            f"(public_jobs) is enabled, exposing pipeline details to {audience}."
        ),
        impact="Pipeline logs, artifacts, and pipeline security scan information may "
        "be visible to a broader audience than intended for the project's visibility "
        "level. Enabling broad access does not prove anyone has actually viewed that "
        "information -- it describes what is possible, not what occurred. Broad "
        "pipeline visibility may be an intentional choice for the project, so this is "
        "a possible false positive rather than a certain defect. CloudOps Guard never "
        "fetches pipelines, logs, artifacts, or security scan results itself.",
        recommendation="Disable project-based pipeline visibility unless the broader "
        "audience is deliberate and approved.",
        audited_at=audited_at,
    )


def _check_job_token_repository_push_allowed(
    snapshot: GitLabProjectSnapshot, audited_at: dt.datetime
) -> GitLabFinding | None:
    if not snapshot.project.ci_push_repository_for_job_token_allowed:
        return None
    return _build_project_setting_finding(
        check_id=CHECK_JOB_TOKEN_REPOSITORY_PUSH_ALLOWED,
        title="CI job tokens are permitted to push to the repository",
        severity=Severity.HIGH,
        snapshot=snapshot,
        evidence=(
            "CI job token repository push (ci_push_repository_for_job_token_allowed) is enabled."
        ),
        impact="The CI job token is an ambient credential available to every "
        "pipeline job; if it can push to the repository, a compromised or "
        "malicious job gains a repository-write escalation path.",
        recommendation="Disable this permission unless a specifically reviewed "
        "automation requires job-token repository push access.",
        audited_at=audited_at,
    )


def _check_developer_pipeline_variable_override(
    snapshot: GitLabProjectSnapshot, audited_at: dt.datetime
) -> GitLabFinding | None:
    # The normalized model already rejects a missing or unrecognized value
    # for this field (see `GitLabProjectSettings`); no fallback is added here.
    if snapshot.project.ci_pipeline_variables_minimum_override_role != _DEVELOPER_OVERRIDE_ROLE:
        return None
    return _build_project_setting_finding(
        check_id=CHECK_DEVELOPER_VARIABLE_OVERRIDE_ALLOWED,
        title="Pipeline-variable override permissions are more permissive than Maintainer",
        severity=Severity.HIGH,
        snapshot=snapshot,
        evidence=(
            "The pipeline-variable minimum override role "
            "(ci_pipeline_variables_minimum_override_role) is 'developer'."
        ),
        impact="A role less privileged than Maintainer can override pipeline "
        "variables at trigger time, which may allow injecting values into "
        "unsafely interpolated scripts or overriding security-relevant "
        "variables -- an authorization bypass risk.",
        recommendation="Change the minimum pipeline-variable override role to "
        "Maintainer or a more restrictive value.",
        audited_at=audited_at,
    )


def _check_redundant_pipelines_not_cancelled(
    snapshot: GitLabProjectSnapshot, audited_at: dt.datetime
) -> GitLabFinding | None:
    # Treated as the normalized string enum it is -- never boolean
    # truthiness or a boolean substitute.
    if snapshot.project.auto_cancel_pending_pipelines != _AUTO_CANCEL_DISABLED:
        return None
    return _build_project_setting_finding(
        check_id=CHECK_REDUNDANT_PIPELINES_NOT_CANCELLED,
        title="Redundant pipelines are not automatically cancelled",
        severity=Severity.LOW,
        snapshot=snapshot,
        evidence=(
            "Automatic cancellation of redundant pending pipelines "
            "(auto_cancel_pending_pipelines) is 'disabled'."
        ),
        impact="With automatic cancellation disabled, superseded pending pipelines "
        "may continue consuming runner capacity rather than being cancelled in "
        "favor of a newer run. Interruptible running pipelines may also continue "
        "consuming runner capacity and compute time instead of being stopped when "
        "superseded.",
        recommendation="Enable automatic cancellation of redundant, pending "
        "pipelines, unless the project intentionally requires every pipeline to "
        "run to completion for compliance or audit-history purposes.",
        audited_at=audited_at,
    )


def _check_unlimited_git_clone_depth(
    snapshot: GitLabProjectSnapshot, audited_at: dt.datetime
) -> GitLabFinding | None:
    # Explicit equality/identity checks, not truthiness: `0` and `None` are
    # the two distinct API representations that the Phase 2C-D1
    # investigation proved both mean unlimited/full history (GitLab
    # coerces a null default to 0 before sending it to GitLab Runner). A
    # bounded positive depth (1-1000) must never trigger this. Using
    # `if not git_depth:` would conflate "0" and "None" and make the
    # distinction unreviewable; each is handled and worded separately here.
    git_depth = snapshot.project.ci_default_git_depth
    if git_depth == 0:
        evidence = "Configured project default Git clone depth: 0 (unlimited/full history)."
    elif git_depth is None:
        evidence = (
            "Configured project default Git clone depth: unset (null), which "
            "GitLab treats as unlimited/full history."
        )
    else:
        return None
    return _build_project_setting_finding(
        check_id=CHECK_UNLIMITED_GIT_CLONE_DEPTH,
        title="Git clone depth is unlimited",
        severity=Severity.LOW,
        snapshot=snapshot,
        evidence=evidence,
        impact=(
            "When this project default applies, CI jobs that obtain repository "
            "sources may fetch full history, potentially increasing network "
            "transfer, checkout duration, runner usage, and compute cost -- "
            "particularly for large or long-lived repositories. This "
            "configuration does not prove that additional cost, transfer, or "
            "delay actually occurred. CloudOps Guard did not inspect pipelines, "
            "jobs, repository history, clone traffic, or runner usage. This "
            "finding evaluates only the project-level default Git clone depth. "
            "It does not inspect job-level or pipeline-level GIT_DEPTH variable "
            "overrides, which can set an effectively unlimited depth when the "
            "project default is bounded, or a bounded depth when the project "
            "default is unlimited."
        ),
        recommendation=(
            "Set a bounded positive project default Git clone depth appropriate "
            "for the project's branching, tagging, and history requirements. "
            "Unlimited history may be intentional and legitimate for work such "
            "as versioning or changelog generation across complete tag/history "
            "information."
        ),
        audited_at=audited_at,
    )


def evaluate_git_clone_depth_check(
    snapshot: GitLabProjectSnapshot,
    *,
    audited_at: dt.datetime,
) -> GitLabFinding | None:
    """Evaluate `GL-COST-002` in isolation.

    Produces the same result as `GL-COST-002` within
    `evaluate_project_setting_checks` -- provided as its own focused,
    standalone entry point in addition to being included there, since it
    requires no additional argument beyond the normalized snapshot and
    evaluates an existing normalized project setting. Operates only on
    `snapshot.project.ci_default_git_depth`; never retrieves any
    additional GitLab data and never mutates `snapshot`. `audited_at` is
    used exactly as supplied (never `datetime.now()`).
    """
    return _check_unlimited_git_clone_depth(snapshot, audited_at)


_PROJECT_SETTING_CHECKS = (
    _check_pipeline_success_not_required,
    _check_public_jobs_on_non_public_project,
    _check_job_token_repository_push_allowed,
    _check_developer_pipeline_variable_override,
    _check_redundant_pipelines_not_cancelled,
    _check_unlimited_git_clone_depth,
)


def evaluate_project_setting_checks(
    snapshot: GitLabProjectSnapshot,
    *,
    audited_at: dt.datetime,
) -> list[GitLabFinding]:
    """Evaluate the six project-setting checks against `snapshot`.

    Covers `GL-MR-001`, `GL-SEC-001`, `GL-SEC-002`, `GL-SEC-003`,
    `GL-COST-001`, and `GL-COST-002` -- kept as a separate entry point from
    `evaluate_protected_branch_checks` (there is no combined all-GitLab
    evaluator yet). `GL-REL-001` is deliberately not included here: it
    still requires an explicit, caller-supplied threshold with no
    product-level default, so it stays on its own entry point
    (`evaluate_job_timeout_check`). Operates only on the already-collected,
    normalized `snapshot`; never retrieves any additional GitLab data (in
    particular, never calls a CI/CD variables endpoint for `GL-SEC-003`,
    and never inspects job-level `GIT_DEPTH` overrides for `GL-COST-002`)
    and never mutates `snapshot`. `audited_at` is used exactly as supplied
    (never `datetime.now()`) so results are deterministic.

    Findings are returned in stable `GL-MR-001`, `GL-SEC-001`, `GL-SEC-002`,
    `GL-SEC-003`, `GL-COST-001`, `GL-COST-002` order.
    """
    findings: list[GitLabFinding] = []
    for check in _PROJECT_SETTING_CHECKS:
        finding = check(snapshot, audited_at)
        if finding is not None:
            findings.append(finding)
    return findings


# --- Phase 2C-C: job timeout check -----------------------------------------------

_SECONDS_PER_MINUTE = 60


def _validate_job_timeout_threshold(value: object) -> int:
    """Validate a caller-supplied `job_timeout_threshold_seconds` value.

    Must be a real, non-boolean `int` greater than zero. `bool` is rejected
    even though Python treats it as an `int` subclass, matching the same
    pattern used throughout `collectors/gitlab.py`. The rejected value is
    never reproduced in the raised error.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("job_timeout_threshold_seconds must be a positive integer.")
    return value


def _format_seconds_as_minutes(total_seconds: int) -> str:
    """Format `total_seconds` as an exact "N minute(s) [M second(s)]" string.

    Uses integer `divmod` only -- no float division or decimal rounding --
    so this is exact and never raises `OverflowError` for an arbitrarily
    large positive Python `int` (Python integers are arbitrary precision;
    it is float conversion that would risk overflow/precision loss for a
    huge value). Minutes are always shown, even when zero (e.g. "0 minutes
    1 second"); the seconds part is included only when the remainder is
    non-zero. Singular/plural forms are applied independently to each part.
    """
    minutes, seconds = divmod(total_seconds, _SECONDS_PER_MINUTE)
    minutes_text = f"{minutes} minute" + ("" if minutes == 1 else "s")
    if seconds == 0:
        return minutes_text
    seconds_text = f"{seconds} second" + ("" if seconds == 1 else "s")
    return f"{minutes_text} {seconds_text}"


def evaluate_job_timeout_check(
    snapshot: GitLabProjectSnapshot,
    *,
    audited_at: dt.datetime,
    job_timeout_threshold_seconds: int,
) -> GitLabFinding | None:
    """Evaluate `GL-REL-001` (project job timeout exceeds a configurable threshold).

    Operates only on `snapshot.project.build_timeout` -- never retrieves any
    additional GitLab data (no pipelines, jobs, traces, logs, artifacts,
    variables, or runner data), and never mutates `snapshot`. `audited_at`
    is used exactly as supplied (never `datetime.now()`).

    `job_timeout_threshold_seconds` is a required, caller-supplied operator
    threshold -- no product-level default is introduced here; that is a
    later integration-phase decision. It is validated before evaluation: a
    fixed, sanitized `ValueError` is raised for anything that is not a real,
    non-boolean, positive `int`, without reproducing the rejected value, and
    before any finding could be produced.

    A finding is produced only when the configured project timeout strictly
    exceeds the threshold; a timeout equal to or below the threshold passes.

    Kept as its own public entry point rather than folded into
    `evaluate_project_setting_checks`, so existing callers/tests are not
    forced to supply a threshold, and there is still no combined all-GitLab
    evaluator.
    """
    threshold = _validate_job_timeout_threshold(job_timeout_threshold_seconds)

    build_timeout = snapshot.project.build_timeout
    if build_timeout <= threshold:
        return None

    return _build_project_setting_finding(
        check_id=CHECK_JOB_TIMEOUT_EXCEEDS_THRESHOLD,
        title="Project job timeout exceeds a configurable threshold",
        severity=Severity.MEDIUM,
        snapshot=snapshot,
        evidence=(
            f"Configured project job timeout: {build_timeout} seconds "
            f"({_format_seconds_as_minutes(build_timeout)}). Configured audit "
            f"threshold: {threshold} seconds ({_format_seconds_as_minutes(threshold)})."
        ),
        impact="An excessively long job timeout lets a hung job occupy a runner "
        "slot for longer, delaying feedback and potentially increasing compute "
        "cost.",
        recommendation="Lower the project's default job timeout to a value "
        "appropriate for normal jobs, and use job-level timeout overrides only "
        "for legitimately long-running work. Projects with intentionally long "
        "builds may choose a higher threshold.",
        audited_at=audited_at,
    )
