"""Orchestrates running checks over a snapshot and builds the audit report.

Kubernetes: `evaluate(...)` runs over a `ClusterSnapshot` and builds an
`AuditReport`. Deployment-managed pods are evaluated once, at the
Deployment, to avoid reporting the same missing-resources or mutable-tag
finding once per replica. Pods without a matching, collected Deployment
(e.g. bare pods) are evaluated directly so nothing running is skipped.
Restart counts are runtime data and are always evaluated per pod.

As defense in depth, a pod's `owning_deployment` name is not trusted on its
own here: it is re-verified against the Deployments actually present in this
snapshot before its own container checks are skipped. This means a
collector-level attribution bug (or a future refactor that weakens it)
cannot silently cause a running pod to go unchecked.

GitLab (v0.2.0 Phase 2D-A): `evaluate_gitlab(...)` is a second, independent
public entry point in this same module -- not a separate GitLab evaluator
module, since there is no concrete architectural reason for one. It is pure
orchestration only: it performs no HTTP, filesystem, environment-variable,
logging, or subprocess access itself, accepts no client/collector/token/URL/
output-path/raw-response argument, never mutates either snapshot it is
given, and never duplicates any check's condition or finding wording --
it delegates exclusively to the existing public GitLab check entry points
(`evaluate_protected_branch_checks`, `evaluate_project_setting_checks`,
`evaluate_job_timeout_check`, `evaluate_ci_image_check`) and assembles their
results into a `GitLabAuditReport`. It does not rename or otherwise change
the Kubernetes `evaluate(...)` contract above. JSON/HTML report-file
rendering and CLI integration are not implemented by this module in either
platform's evaluator -- see `docs/milestones/v0.2.0-gitlab-audit.md`.
"""

from __future__ import annotations

import datetime as dt

from cloudops_guard.checks.gitlab import (
    evaluate_ci_image_check,
    evaluate_job_timeout_check,
    evaluate_project_setting_checks,
    evaluate_protected_branch_checks,
)
from cloudops_guard.checks.kubernetes import evaluate_container, evaluate_container_restarts
from cloudops_guard.config import DEFAULT_RESTART_THRESHOLD
from cloudops_guard.models import (
    AuditReport,
    AuditSummary,
    ClusterSnapshot,
    Finding,
    GitLabAuditReport,
    GitLabCiConfigSnapshot,
    GitLabFinding,
    GitLabProjectSnapshot,
    ResourceKind,
    Severity,
)

_SEVERITY_FIELD = {
    Severity.CRITICAL: "critical",
    Severity.HIGH: "high",
    Severity.MEDIUM: "medium",
    Severity.LOW: "low",
}


def _summarize(findings: list[Finding]) -> AuditSummary:
    summary = AuditSummary()
    for finding in findings:
        field = _SEVERITY_FIELD[finding.severity]
        setattr(summary, field, getattr(summary, field) + 1)
    return summary


def evaluate(
    snapshot: ClusterSnapshot,
    *,
    restart_threshold: int = DEFAULT_RESTART_THRESHOLD,
    namespace_filter: str | None = None,
) -> AuditReport:
    now = dt.datetime.now(dt.UTC)
    findings: list[Finding] = []
    deployment_keys = {
        (deployment.namespace, deployment.name) for deployment in snapshot.deployments
    }

    for deployment in snapshot.deployments:
        for container in deployment.containers:
            findings.extend(
                evaluate_container(
                    ResourceKind.DEPLOYMENT,
                    deployment.name,
                    deployment.namespace,
                    container,
                    snapshot.context,
                    now,
                )
            )

    for pod in snapshot.pods:
        is_verified_deployment_owned = (
            pod.owning_deployment is not None
            and (pod.namespace, pod.owning_deployment) in deployment_keys
        )
        if not is_verified_deployment_owned:
            for container in pod.containers:
                findings.extend(
                    evaluate_container(
                        ResourceKind.POD,
                        pod.name,
                        pod.namespace,
                        container,
                        snapshot.context,
                        now,
                    )
                )
        for status in pod.container_statuses:
            restart_finding = evaluate_container_restarts(
                pod, status, snapshot.context, restart_threshold, now
            )
            if restart_finding is not None:
                findings.append(restart_finding)

    return AuditReport(
        cluster_context=snapshot.context,
        namespace_filter=namespace_filter,
        generated_at=now,
        findings=findings,
        summary=_summarize(findings),
    )


# --- GitLab (v0.2.0 Phase 2D-A): combined evaluator and report construction -------


def _summarize_gitlab(findings: list[GitLabFinding]) -> AuditSummary:
    # Deliberately not a reuse of `_summarize` above: `Finding` and
    # `GitLabFinding` are intentionally separate, non-inheriting models
    # (see models.py), and this small duplication keeps the two platforms'
    # evaluators independent of one another rather than coupling them
    # through a shared helper's type.
    summary = AuditSummary()
    for finding in findings:
        field = _SEVERITY_FIELD[finding.severity]
        setattr(summary, field, getattr(summary, field) + 1)
    return summary


def _validate_gitlab_audited_at(value: object) -> dt.datetime:
    """Require a real, timezone-aware `datetime`.

    Rejects a non-`datetime` value, a naive `datetime`, and a `datetime`
    whose `tzinfo` is attached but returns `None` from `utcoffset()` --
    Python's own datetime docs describe the latter as behaving like a
    naive datetime for arithmetic/comparison purposes, so `tzinfo is not
    None` alone is not a sufficient check. The rejected value is never
    reproduced in the raised error.
    """
    if not isinstance(value, dt.datetime):
        raise ValueError("audited_at must be a timezone-aware datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("audited_at must be timezone-aware.")
    return value


def _validate_gitlab_snapshot_identity(
    project_snapshot: GitLabProjectSnapshot,
    ci_config_snapshot: GitLabCiConfigSnapshot,
) -> None:
    """Require the two snapshots to identify the same project.

    Checked by exact `project_path` equality only -- the two snapshots'
    `collected_at` values are never compared or required to match, since
    they originate from separate sequential collection operations
    (`collect_project_snapshot` and `collect_ci_config_snapshot`) and may
    legitimately differ. Neither project path is reproduced in the raised
    error.
    """
    if ci_config_snapshot.project_path != project_snapshot.project.project_path:
        raise ValueError(
            "GitLab evaluation failed: the CI config snapshot's project path "
            "does not match the project snapshot's project path."
        )


def evaluate_gitlab(
    project_snapshot: GitLabProjectSnapshot,
    ci_config_snapshot: GitLabCiConfigSnapshot,
    *,
    audited_at: dt.datetime,
    job_timeout_threshold_seconds: int,
) -> GitLabAuditReport:
    """Evaluate every implemented GitLab check and build a `GitLabAuditReport`.

    Pure orchestration only: performs no HTTP, filesystem,
    environment-variable, logging, or subprocess access; accepts no
    client, collector, token, URL, output path, or raw API response; never
    mutates `project_snapshot` or `ci_config_snapshot`. Delegates
    exclusively to the existing public GitLab check entry points --
    `evaluate_protected_branch_checks`, `evaluate_project_setting_checks`,
    `evaluate_job_timeout_check`, `evaluate_ci_image_check` -- never
    reimplementing or duplicating any check's condition or finding
    wording.

    `audited_at` must be a real, timezone-aware `datetime`
    (`_validate_gitlab_audited_at`); a naive value, a non-`datetime` value,
    or a `datetime` whose `tzinfo` returns `None` from `utcoffset()` raises
    `ValueError` before any check runs. The exact same `audited_at` value
    is then passed to every delegated evaluator, so every returned
    finding's `audited_at` and the report's `generated_at` are all equal
    to the supplied value -- `datetime.now()` is never called here.

    `project_snapshot.project.project_path` and
    `ci_config_snapshot.project_path` must be exactly equal
    (`_validate_gitlab_snapshot_identity`); a mismatch raises `ValueError`
    -- reproducing neither path -- before any check evaluator is invoked.
    The two snapshots' `collected_at` values are never compared.

    `job_timeout_threshold_seconds` is forwarded to
    `evaluate_job_timeout_check` exactly as supplied -- its existing
    validation contract (a real, non-boolean, positive `int`; a fixed,
    sanitized `ValueError` otherwise) is not reimplemented here.

    Findings are combined in a stable, documented order -- protected-branch
    findings, then project-setting findings, then the optional `GL-REL-001`
    timeout finding, then CI-image findings -- preserving each delegated
    evaluator's own internal ordering exactly, including every
    per-occurrence `GL-CI-001` finding with no deduplication.

    The returned report's identity fields (`gitlab_url`, `project_id`,
    `project_path`, `default_branch`) come exclusively from
    `project_snapshot` -- never from `ci_config_snapshot`, and never a raw
    response, GitLab version, enterprise flag, collection timestamp,
    merged YAML, script, variable, credential, token, warning, error,
    include, job, pipeline, log, trace, artifact, or repository content.
    """
    when = _validate_gitlab_audited_at(audited_at)
    _validate_gitlab_snapshot_identity(project_snapshot, ci_config_snapshot)

    findings: list[GitLabFinding] = []
    findings.extend(evaluate_protected_branch_checks(project_snapshot, audited_at=when))
    findings.extend(evaluate_project_setting_checks(project_snapshot, audited_at=when))
    timeout_finding = evaluate_job_timeout_check(
        project_snapshot,
        audited_at=when,
        job_timeout_threshold_seconds=job_timeout_threshold_seconds,
    )
    if timeout_finding is not None:
        findings.append(timeout_finding)
    findings.extend(evaluate_ci_image_check(ci_config_snapshot, audited_at=when))

    return GitLabAuditReport(
        platform="gitlab",
        gitlab_url=project_snapshot.gitlab_url,
        project_id=project_snapshot.project.project_id,
        project_path=project_snapshot.project.project_path,
        default_branch=project_snapshot.project.default_branch,
        generated_at=when,
        findings=findings,
        summary=_summarize_gitlab(findings),
    )
