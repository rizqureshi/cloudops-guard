"""Deterministic GitLab checks (v0.2.0 Phase 2C-A: protected-default-branch checks).

Each check operates only on the normalized `GitLabProjectSnapshot` produced
by the Phase 2B collector (`cloudops_guard.collectors.gitlab.GitLabCollector`)
-- never on `GitLabClient`/`GitLabCollector` directly, and never on any raw
GitLab API response. This module performs no HTTP, filesystem,
environment-variable, or subprocess access, and never mutates the snapshot
it is given. It implements only the three protected-default-branch checks
(`GL-BR-001` - `GL-BR-003`); the remaining project-setting checks, CI Lint
image inspection, evaluator/report integration, and CLI wiring are separate,
later work -- see `docs/milestones/v0.2.0-gitlab-audit.md`.
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

_DEVELOPER_ROLE_LEVEL = 30

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
