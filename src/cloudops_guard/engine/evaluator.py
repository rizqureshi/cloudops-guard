"""Orchestrates running checks over a ClusterSnapshot and builds the AuditReport.

Deployment-managed pods are evaluated once, at the Deployment, to avoid
reporting the same missing-resources or mutable-tag finding once per
replica. Pods without a matching, collected Deployment (e.g. bare pods) are
evaluated directly so nothing running is skipped. Restart counts are
runtime data and are always evaluated per pod.
"""

from __future__ import annotations

import datetime as dt

from cloudops_guard.checks.kubernetes import evaluate_container, evaluate_pod_restarts
from cloudops_guard.config import DEFAULT_RESTART_THRESHOLD
from cloudops_guard.models import (
    AuditReport,
    AuditSummary,
    ClusterSnapshot,
    Finding,
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
        if pod.owning_deployment is None:
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
        restart_finding = evaluate_pod_restarts(pod, snapshot.context, restart_threshold, now)
        if restart_finding is not None:
            findings.append(restart_finding)

    return AuditReport(
        cluster_context=snapshot.context,
        namespace_filter=namespace_filter,
        generated_at=now,
        findings=findings,
        summary=_summarize(findings),
    )
