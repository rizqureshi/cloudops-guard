"""Tests for the deterministic Kubernetes checks and the evaluator that runs them."""

from __future__ import annotations

import datetime as dt

import pytest

from cloudops_guard.checks.kubernetes import (
    CHECK_EXCESSIVE_RESTARTS,
    CHECK_MUTABLE_IMAGE_TAG,
    CHECK_NO_CPU_LIMIT,
    CHECK_NO_CPU_REQUEST,
    CHECK_NO_MEMORY_LIMIT,
    CHECK_NO_MEMORY_REQUEST,
    evaluate_container,
    evaluate_container_restarts,
)
from cloudops_guard.engine.evaluator import evaluate
from cloudops_guard.models import (
    ClusterSnapshot,
    ContainerInfo,
    ContainerRuntimeStatus,
    DeploymentInfo,
    PodInfo,
    ResourceKind,
    ResourceRequirements,
    Severity,
)

NOW = dt.datetime(2026, 7, 27, tzinfo=dt.UTC)


def container(**overrides: object) -> ContainerInfo:
    defaults: dict[str, object] = {
        "name": "app",
        "image": "example.com/app:1.2.3",
        "resources": ResourceRequirements(
            cpu_request="100m",
            memory_request="128Mi",
            cpu_limit="500m",
            memory_limit="256Mi",
        ),
    }
    defaults.update(overrides)
    return ContainerInfo(**defaults)


def container_status(**overrides: object) -> ContainerRuntimeStatus:
    defaults: dict[str, object] = {"container_name": "app", "restart_count": 0, "ready": True}
    defaults.update(overrides)
    return ContainerRuntimeStatus(**defaults)


def check_ids(findings) -> set[str]:
    return {f.check_id for f in findings}


def test_fully_configured_container_has_no_findings() -> None:
    findings = evaluate_container(ResourceKind.POD, "web-1", "default", container(), "ctx", NOW)
    assert findings == []


def test_missing_cpu_request_is_flagged() -> None:
    c = container(
        resources=ResourceRequirements(
            memory_request="128Mi", cpu_limit="500m", memory_limit="256Mi"
        )
    )
    findings = evaluate_container(ResourceKind.POD, "web-1", "default", c, "ctx", NOW)
    assert CHECK_NO_CPU_REQUEST in check_ids(findings)
    finding = next(f for f in findings if f.check_id == CHECK_NO_CPU_REQUEST)
    assert finding.severity == Severity.MEDIUM
    assert finding.container_name == "app"


def test_missing_memory_request_is_flagged() -> None:
    c = container(
        resources=ResourceRequirements(cpu_request="100m", cpu_limit="500m", memory_limit="256Mi")
    )
    findings = evaluate_container(ResourceKind.POD, "web-1", "default", c, "ctx", NOW)
    assert CHECK_NO_MEMORY_REQUEST in check_ids(findings)


def test_missing_cpu_limit_is_flagged() -> None:
    c = container(
        resources=ResourceRequirements(
            cpu_request="100m", memory_request="128Mi", memory_limit="256Mi"
        )
    )
    findings = evaluate_container(ResourceKind.POD, "web-1", "default", c, "ctx", NOW)
    assert CHECK_NO_CPU_LIMIT in check_ids(findings)


def test_missing_memory_limit_is_flagged_as_high() -> None:
    c = container(
        resources=ResourceRequirements(cpu_request="100m", memory_request="128Mi", cpu_limit="500m")
    )
    findings = evaluate_container(ResourceKind.POD, "web-1", "default", c, "ctx", NOW)
    finding = next(f for f in findings if f.check_id == CHECK_NO_MEMORY_LIMIT)
    assert finding.severity == Severity.HIGH


# --- Image tag checks (K8S-IMG-001, ID kept stable) --------------------------


@pytest.mark.parametrize(
    "image",
    ["example.com/app:latest", "example.com/app", "app", ""],
)
def test_mutable_or_missing_tag_is_flagged(image: str) -> None:
    c = container(image=image)
    findings = evaluate_container(ResourceKind.POD, "web-1", "default", c, "ctx", NOW)
    assert CHECK_MUTABLE_IMAGE_TAG in check_ids(findings)


@pytest.mark.parametrize(
    "image",
    [
        "example.com/app:1.2.3",
        "registry:5000/team/app:2.0.0",
        "example.com/app@sha256:" + "a" * 64,
        "example.com/app:1.2.3@sha256:" + "a" * 64,  # tag plus digest
    ],
)
def test_versioned_tag_or_digest_is_not_flagged(image: str) -> None:
    c = container(image=image)
    findings = evaluate_container(ResourceKind.POD, "web-1", "default", c, "ctx", NOW)
    assert CHECK_MUTABLE_IMAGE_TAG not in check_ids(findings)


def test_image_tag_check_id_is_stable() -> None:
    c = container(image="example.com/app:latest")
    finding = next(
        f
        for f in evaluate_container(ResourceKind.POD, "web-1", "default", c, "ctx", NOW)
        if f.check_id == CHECK_MUTABLE_IMAGE_TAG
    )
    assert finding.check_id == "K8S-IMG-001"


def test_image_tag_finding_does_not_claim_version_tags_are_immutable() -> None:
    """Only a digest is content-addressed; the wording must not overstate a plain tag."""
    c = container(image="example.com/app:latest")
    finding = next(
        f
        for f in evaluate_container(ResourceKind.POD, "web-1", "default", c, "ctx", NOW)
        if f.check_id == CHECK_MUTABLE_IMAGE_TAG
    )
    assert "immutable" not in finding.impact.lower() or "digest" in finding.impact.lower()
    assert "overwritten" in finding.impact.lower() or "overwrite" in finding.impact.lower()
    assert "digest" in finding.recommendation.lower()


def test_finding_attribution_uses_given_kind_namespace_and_resource_name() -> None:
    c = container(resources=ResourceRequirements())
    findings = evaluate_container(
        ResourceKind.DEPLOYMENT, "checkout", "payments", c, "prod-ctx", NOW
    )
    assert findings
    for finding in findings:
        assert finding.resource_kind == ResourceKind.DEPLOYMENT
        assert finding.resource_name == "checkout"
        assert finding.namespace == "payments"
        assert finding.cluster_context == "prod-ctx"
        assert finding.audited_at == NOW


# --- Per-container restart checks (K8S-REL-001) ------------------------------


def test_restart_check_below_threshold_returns_none() -> None:
    pod = PodInfo(name="web-1", namespace="default", containers=[container()])
    status = container_status(restart_count=2)
    assert evaluate_container_restarts(pod, status, "ctx", threshold=5, now=NOW) is None


def test_restart_check_at_threshold_is_flagged() -> None:
    pod = PodInfo(name="web-1", namespace="default", containers=[container()])
    status = container_status(container_name="app", restart_count=5)
    finding = evaluate_container_restarts(pod, status, "ctx", threshold=5, now=NOW)
    assert finding is not None
    assert finding.check_id == CHECK_EXCESSIVE_RESTARTS
    assert finding.check_id == "K8S-REL-001"
    assert finding.severity == Severity.HIGH
    assert finding.resource_kind == ResourceKind.POD
    assert finding.resource_name == "web-1"
    assert finding.container_name == "app"


def test_one_container_above_threshold_is_flagged_independently() -> None:
    pod = PodInfo(name="multi", namespace="default", containers=[container()])
    hot = container_status(container_name="app", restart_count=9)
    finding = evaluate_container_restarts(pod, hot, "ctx", threshold=5, now=NOW)
    assert finding is not None
    assert finding.container_name == "app"


def test_all_containers_below_threshold_even_if_combined_total_exceeds_it() -> None:
    """The pod must never be flagged just because several small counts add up."""
    pod = PodInfo(name="multi", namespace="default", containers=[container()])
    statuses = [
        container_status(container_name="app", restart_count=3),
        container_status(container_name="sidecar", restart_count=3),
    ]
    findings = [
        f
        for s in statuses
        if (f := evaluate_container_restarts(pod, s, "ctx", threshold=5, now=NOW)) is not None
    ]
    assert findings == []


def test_waiting_reason_is_included_in_evidence() -> None:
    pod = PodInfo(name="crashy", namespace="default", containers=[container()])
    status = container_status(
        container_name="app", restart_count=6, waiting_reason="CrashLoopBackOff"
    )
    finding = evaluate_container_restarts(pod, status, "ctx", threshold=5, now=NOW)
    assert finding is not None
    assert "CrashLoopBackOff" in finding.evidence


def test_last_termination_reason_is_included_in_evidence() -> None:
    pod = PodInfo(name="oom-pod", namespace="default", containers=[container()])
    status = container_status(
        container_name="app", restart_count=6, last_termination_reason="OOMKilled"
    )
    finding = evaluate_container_restarts(pod, status, "ctx", threshold=5, now=NOW)
    assert finding is not None
    assert "OOMKilled" in finding.evidence


def test_restart_evidence_notes_count_is_cumulative() -> None:
    pod = PodInfo(name="crashy", namespace="default", containers=[container()])
    status = container_status(container_name="app", restart_count=6)
    finding = evaluate_container_restarts(pod, status, "ctx", threshold=5, now=NOW)
    assert finding is not None
    assert "cumulative" in finding.evidence.lower()


def test_no_sensitive_status_fields_exist_on_container_runtime_status() -> None:
    status = container_status()
    assert set(status.model_dump().keys()) == {
        "container_name",
        "restart_count",
        "ready",
        "waiting_reason",
        "last_termination_reason",
    }


# --- Evaluator orchestration --------------------------------------------------


def test_evaluate_empty_snapshot_yields_no_findings() -> None:
    snapshot = ClusterSnapshot(
        context="ctx", collected_at=NOW, namespaces=[], pods=[], deployments=[]
    )
    report = evaluate(snapshot)
    assert report.findings == []
    assert report.summary.total == 0


def test_evaluate_checks_deployment_containers_once_not_per_replica_pod() -> None:
    """12. A verified Deployment-owned pod does not produce duplicate template findings.

    The pods' owning_deployment="web" is verified against the DeploymentInfo
    actually present in snapshot.deployments (same namespace+name) before
    their own container checks are skipped.
    """
    bad_container = container(resources=ResourceRequirements())
    deployment = DeploymentInfo(
        name="web", namespace="default", replicas=2, containers=[bad_container]
    )
    pods = [
        PodInfo(
            name=f"web-abc-{i}",
            namespace="default",
            containers=[bad_container],
            owning_deployment="web",
        )
        for i in range(2)
    ]
    snapshot = ClusterSnapshot(
        context="ctx", collected_at=NOW, namespaces=[], pods=pods, deployments=[deployment]
    )

    report = evaluate(snapshot)

    deployment_findings = [f for f in report.findings if f.resource_kind == ResourceKind.DEPLOYMENT]
    pod_findings = [f for f in report.findings if f.resource_kind == ResourceKind.POD]
    assert len(deployment_findings) == 4  # 4 resource checks (image tag is pinned), once
    assert pod_findings == []  # owned pods are not separately re-checked


def test_evaluate_gives_container_checks_to_pod_with_unverified_owning_deployment() -> None:
    """11. A pod claiming an owning_deployment that matches no collected Deployment

    (stale attribution, deleted Deployment, or a bug upstream) must still
    receive its own container-level checks rather than being silently
    skipped -- this is the evaluator's defense-in-depth verification.
    """
    bad_container = container(resources=ResourceRequirements())
    pod = PodInfo(
        name="orphaned",
        namespace="default",
        containers=[bad_container],
        owning_deployment="web",  # claims ownership, but no such Deployment is collected
    )
    snapshot = ClusterSnapshot(
        context="ctx", collected_at=NOW, namespaces=[], pods=[pod], deployments=[]
    )

    report = evaluate(snapshot)

    assert len(report.findings) == 4  # 4 resource checks (image tag is pinned)
    assert all(f.resource_kind == ResourceKind.POD for f in report.findings)


def test_evaluate_verifies_owning_deployment_within_the_pod_s_own_namespace() -> None:
    """A pod's owning_deployment name must be verified in its own namespace,

    not just matched against any Deployment with that name anywhere.
    """
    bad_container = container(resources=ResourceRequirements())
    deployment = DeploymentInfo(
        name="web", namespace="other-namespace", replicas=2, containers=[bad_container]
    )
    pod = PodInfo(
        name="cross-namespace-pod",
        namespace="default",
        containers=[bad_container],
        owning_deployment="web",
    )
    snapshot = ClusterSnapshot(
        context="ctx", collected_at=NOW, namespaces=[], pods=[pod], deployments=[deployment]
    )

    report = evaluate(snapshot)

    pod_findings = [f for f in report.findings if f.resource_kind == ResourceKind.POD]
    assert len(pod_findings) == 4


def test_evaluate_checks_bare_pod_without_owning_deployment() -> None:
    bad_container = container(resources=ResourceRequirements())
    pod = PodInfo(name="standalone", namespace="default", containers=[bad_container])
    snapshot = ClusterSnapshot(
        context="ctx", collected_at=NOW, namespaces=[], pods=[pod], deployments=[]
    )

    report = evaluate(snapshot)

    assert len(report.findings) == 4  # 4 resource checks (image tag is pinned)
    assert all(f.resource_kind == ResourceKind.POD for f in report.findings)


def test_evaluate_includes_restart_finding_alongside_container_findings() -> None:
    pod = PodInfo(
        name="crashy",
        namespace="default",
        containers=[container()],
        container_statuses=[container_status(container_name="app", restart_count=10)],
    )
    snapshot = ClusterSnapshot(
        context="ctx", collected_at=NOW, namespaces=[], pods=[pod], deployments=[]
    )

    report = evaluate(snapshot, restart_threshold=5)

    assert len(report.findings) == 1
    assert report.findings[0].check_id == CHECK_EXCESSIVE_RESTARTS


def test_evaluate_summary_counts_match_findings_by_severity() -> None:
    bad_container = container(resources=ResourceRequirements())
    pod = PodInfo(name="standalone", namespace="default", containers=[bad_container])
    snapshot = ClusterSnapshot(
        context="ctx", collected_at=NOW, namespaces=[], pods=[pod], deployments=[]
    )

    report = evaluate(snapshot)

    high_count = sum(1 for f in report.findings if f.severity == Severity.HIGH)
    medium_count = sum(1 for f in report.findings if f.severity == Severity.MEDIUM)
    assert report.summary.high == high_count
    assert report.summary.medium == medium_count
    assert report.summary.total == len(report.findings)
