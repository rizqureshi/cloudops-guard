"""Deterministic Kubernetes checks.

Each check operates on the normalized internal models (models.py), never on
the Kubernetes API client directly, so checks can be unit tested without a
live cluster and without duplicating collector logic.
"""

from __future__ import annotations

import datetime as dt

from cloudops_guard.models import (
    ContainerInfo,
    ContainerRuntimeStatus,
    Finding,
    PodInfo,
    ResourceKind,
    Severity,
)

CHECK_NO_CPU_REQUEST = "K8S-RES-001"
CHECK_NO_MEMORY_REQUEST = "K8S-RES-002"
CHECK_NO_CPU_LIMIT = "K8S-RES-003"
CHECK_NO_MEMORY_LIMIT = "K8S-RES-004"
CHECK_MUTABLE_IMAGE_TAG = "K8S-IMG-001"
CHECK_EXCESSIVE_RESTARTS = "K8S-REL-001"

_MUTABLE_TAGS = {"latest"}


def _check_cpu_request(
    kind: ResourceKind,
    resource_name: str,
    namespace: str,
    container: ContainerInfo,
    context: str,
    now: dt.datetime,
) -> Finding | None:
    if container.resources.cpu_request:
        return None
    return Finding(
        check_id=CHECK_NO_CPU_REQUEST,
        title="Container has no CPU request",
        severity=Severity.MEDIUM,
        cluster_context=context,
        namespace=namespace,
        resource_kind=kind,
        resource_name=resource_name,
        container_name=container.name,
        evidence=f"Container '{container.name}' (image: {container.image}) does not set "
        "resources.requests.cpu",
        impact="Without a CPU request the scheduler cannot reserve capacity for this "
        "container, increasing the risk of CPU contention with other workloads on the "
        "same node.",
        recommendation="Set resources.requests.cpu based on observed usage.",
        auto_remediable=False,
        audited_at=now,
    )


def _check_memory_request(
    kind: ResourceKind,
    resource_name: str,
    namespace: str,
    container: ContainerInfo,
    context: str,
    now: dt.datetime,
) -> Finding | None:
    if container.resources.memory_request:
        return None
    return Finding(
        check_id=CHECK_NO_MEMORY_REQUEST,
        title="Container has no memory request",
        severity=Severity.MEDIUM,
        cluster_context=context,
        namespace=namespace,
        resource_kind=kind,
        resource_name=resource_name,
        container_name=container.name,
        evidence=f"Container '{container.name}' (image: {container.image}) does not set "
        "resources.requests.memory",
        impact="Without a memory request the scheduler cannot reserve capacity for this "
        "container, increasing the risk of scheduling it onto an already memory-pressured "
        "node.",
        recommendation="Set resources.requests.memory based on observed usage.",
        auto_remediable=False,
        audited_at=now,
    )


def _check_cpu_limit(
    kind: ResourceKind,
    resource_name: str,
    namespace: str,
    container: ContainerInfo,
    context: str,
    now: dt.datetime,
) -> Finding | None:
    if container.resources.cpu_limit:
        return None
    return Finding(
        check_id=CHECK_NO_CPU_LIMIT,
        title="Container has no CPU limit",
        severity=Severity.MEDIUM,
        cluster_context=context,
        namespace=namespace,
        resource_kind=kind,
        resource_name=resource_name,
        container_name=container.name,
        evidence=f"Container '{container.name}' (image: {container.image}) does not set "
        "resources.limits.cpu",
        impact="Without a CPU limit this container can consume unbounded CPU on its node, "
        "starving other workloads during load spikes.",
        recommendation="Set resources.limits.cpu to cap worst-case CPU usage.",
        auto_remediable=False,
        audited_at=now,
    )


def _check_memory_limit(
    kind: ResourceKind,
    resource_name: str,
    namespace: str,
    container: ContainerInfo,
    context: str,
    now: dt.datetime,
) -> Finding | None:
    if container.resources.memory_limit:
        return None
    return Finding(
        check_id=CHECK_NO_MEMORY_LIMIT,
        title="Container has no memory limit",
        severity=Severity.HIGH,
        cluster_context=context,
        namespace=namespace,
        resource_kind=kind,
        resource_name=resource_name,
        container_name=container.name,
        evidence=f"Container '{container.name}' (image: {container.image}) does not set "
        "resources.limits.memory",
        impact="Without a memory limit this container can consume unbounded memory, risking "
        "node-level out-of-memory conditions that can evict or kill unrelated workloads.",
        recommendation="Set resources.limits.memory to cap worst-case memory usage.",
        auto_remediable=False,
        audited_at=now,
    )


def _image_tag(image: str) -> str | None:
    """Return the tag portion of an image reference.

    Handles registry hosts with ports (e.g. registry:5000/app:tag) by only
    splitting on the final path segment. Returns None for images pinned by
    digest (no mutable tag involved) as well as for untagged images.
    """
    last_segment = image.rsplit("/", 1)[-1]
    if "@" in last_segment:
        return None  # pinned by digest, not a tag
    if ":" not in last_segment:
        return None  # no tag at all -> implicitly "latest"
    return last_segment.rsplit(":", 1)[-1]


def _is_pinned_by_digest(image: str) -> bool:
    return "@" in image.rsplit("/", 1)[-1]


def _check_mutable_image_tag(
    kind: ResourceKind,
    resource_name: str,
    namespace: str,
    container: ContainerInfo,
    context: str,
    now: dt.datetime,
) -> Finding | None:
    if _is_pinned_by_digest(container.image):
        return None
    tag = _image_tag(container.image)
    if tag is not None and tag not in _MUTABLE_TAGS:
        return None
    observed = tag or "(no tag)"
    return Finding(
        check_id=CHECK_MUTABLE_IMAGE_TAG,
        title="Container image uses a mutable tag",
        severity=Severity.HIGH,
        cluster_context=context,
        namespace=namespace,
        resource_kind=kind,
        resource_name=resource_name,
        container_name=container.name,
        evidence=f"Container '{container.name}' uses image '{container.image}' (tag: {observed})",
        impact="A mutable tag such as 'latest', or no tag at all, means the exact image "
        "content running today may differ from what runs after the next pod restart, "
        "undermining reproducibility, rollback safety and supply-chain traceability. A "
        "specific version tag (e.g. app:1.4.2) is a meaningful improvement over 'latest', "
        "but a tag can still be overwritten and re-pushed in the registry — it is not "
        "itself a guarantee of immutability.",
        recommendation="Pin the image to a specific version tag at minimum. For a "
        "content-addressed, truly immutable reference, pin to a digest instead or in "
        "addition (e.g. app@sha256:...).",
        auto_remediable=False,
        audited_at=now,
    )


_CONTAINER_CHECKS = (
    _check_cpu_request,
    _check_memory_request,
    _check_cpu_limit,
    _check_memory_limit,
    _check_mutable_image_tag,
)


def evaluate_container(
    kind: ResourceKind,
    resource_name: str,
    namespace: str,
    container: ContainerInfo,
    context: str,
    now: dt.datetime,
) -> list[Finding]:
    """Run all container-level checks against a single container."""
    findings = []
    for check in _CONTAINER_CHECKS:
        finding = check(kind, resource_name, namespace, container, context, now)
        if finding is not None:
            findings.append(finding)
    return findings


def evaluate_container_restarts(
    pod: PodInfo,
    status: ContainerRuntimeStatus,
    context: str,
    threshold: int,
    now: dt.datetime,
) -> Finding | None:
    """Flag a single container whose restart count meets or exceeds the threshold.

    Each container is evaluated independently: a pod is never flagged merely
    because several containers' individually-small counts add up to the
    threshold. The count is cumulative for the pod's current lifetime — this
    does not compute a time-windowed restart rate.
    """
    if status.restart_count < threshold:
        return None
    evidence = (
        f"Container '{status.container_name}' in pod '{pod.name}' has restarted "
        f"{status.restart_count} time(s) (cumulative for the pod's current lifetime), "
        f"meeting or exceeding the configured threshold of {threshold}"
    )
    if status.waiting_reason:
        evidence += f"; current waiting reason: {status.waiting_reason}"
    if status.last_termination_reason:
        evidence += f"; last termination reason: {status.last_termination_reason}"
    return Finding(
        check_id=CHECK_EXCESSIVE_RESTARTS,
        title="Container has an excessive restart count",
        severity=Severity.HIGH,
        cluster_context=context,
        namespace=pod.namespace,
        resource_kind=ResourceKind.POD,
        resource_name=pod.name,
        container_name=status.container_name,
        evidence=evidence,
        impact="Frequent restarts typically indicate crash looping, failing health checks, "
        "or resource exhaustion, and reduce the reliability of the workload.",
        recommendation="Inspect this container's events and state for the root cause "
        "(crash, OOMKill, failed probe) before increasing replica count or ignoring the "
        "symptom.",
        auto_remediable=False,
        audited_at=now,
    )
