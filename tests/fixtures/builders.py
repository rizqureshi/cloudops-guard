"""Factory helpers for building representative Kubernetes API objects in tests.

These build real `kubernetes.client` model instances (not hand-rolled dicts)
so collector tests exercise the same shapes the official client returns.
"""

from __future__ import annotations

from kubernetes.client import (
    V1Container,
    V1ContainerState,
    V1ContainerStateRunning,
    V1ContainerStateTerminated,
    V1ContainerStateWaiting,
    V1ContainerStatus,
    V1Deployment,
    V1DeploymentSpec,
    V1EnvVar,
    V1LabelSelector,
    V1Namespace,
    V1ObjectMeta,
    V1OwnerReference,
    V1Pod,
    V1PodSpec,
    V1PodStatus,
    V1PodTemplateSpec,
    V1ReplicaSet,
    V1ReplicaSetSpec,
    V1ResourceRequirements,
)

DEFAULT_REPLICASET_UID = "00000000-0000-0000-0000-0000000000rs"
DEFAULT_DEPLOYMENT_UID = "00000000-0000-0000-0000-0000000000dp"


def make_namespace(name: str) -> V1Namespace:
    return V1Namespace(metadata=V1ObjectMeta(name=name))


def make_container(
    name: str = "app",
    image: str = "example.com/app:1.0.0",
    cpu_request: str | None = "100m",
    memory_request: str | None = "128Mi",
    cpu_limit: str | None = "500m",
    memory_limit: str | None = "256Mi",
) -> V1Container:
    requests = {}
    if cpu_request:
        requests["cpu"] = cpu_request
    if memory_request:
        requests["memory"] = memory_request
    limits = {}
    if cpu_limit:
        limits["cpu"] = cpu_limit
    if memory_limit:
        limits["memory"] = memory_limit
    return V1Container(
        name=name,
        image=image,
        resources=V1ResourceRequirements(requests=requests or None, limits=limits or None),
        env=[V1EnvVar(name="SECRET_KEY", value="super-secret-value")],
    )


def make_container_status(
    name: str = "app",
    restart_count: int = 0,
    ready: bool = True,
    waiting_reason: str | None = None,
    last_termination_reason: str | None = None,
) -> V1ContainerStatus:
    state = V1ContainerState(
        waiting=V1ContainerStateWaiting(reason=waiting_reason) if waiting_reason else None,
        running=None if waiting_reason else V1ContainerStateRunning(),
    )
    last_state = V1ContainerState(
        terminated=(
            V1ContainerStateTerminated(reason=last_termination_reason, exit_code=1)
            if last_termination_reason
            else None
        )
    )
    return V1ContainerStatus(
        name=name,
        restart_count=restart_count,
        ready=ready,
        image="example.com/app:1.0.0",
        image_id="",
        state=state,
        last_state=last_state,
        started=True,
    )


def make_pod(
    name: str,
    namespace: str = "default",
    containers: list[V1Container] | None = None,
    container_statuses: list[V1ContainerStatus] | None = None,
    owner_kind: str = "ReplicaSet",
    owner_name: str | None = None,
    owner_uid: str = DEFAULT_REPLICASET_UID,
) -> V1Pod:
    """Build a pod. Pass owner_name to attach a single owner reference.

    owner_uid="" simulates an owner reference without a UID (rare in
    practice since the real API always sets it, but the collector must fall
    back to name-based matching in that case). The client model rejects
    uid=None outright since UID is a required OwnerReference field, so an
    empty string is the correct way to simulate "unavailable" here. Leave
    container_statuses=None to simulate a pod with no reported status yet.
    """
    containers = containers or [make_container()]
    owner_references = None
    if owner_name:
        owner_references = [
            V1OwnerReference(
                api_version="apps/v1" if owner_kind in {"ReplicaSet", "Deployment"} else "batch/v1",
                kind=owner_kind,
                name=owner_name,
                uid=owner_uid,
                controller=True,
            )
        ]
    return V1Pod(
        metadata=V1ObjectMeta(name=name, namespace=namespace, owner_references=owner_references),
        spec=V1PodSpec(containers=containers),
        status=V1PodStatus(container_statuses=container_statuses),
    )


def make_deployment(
    name: str,
    namespace: str = "default",
    replicas: int = 3,
    containers: list[V1Container] | None = None,
) -> V1Deployment:
    containers = containers or [make_container()]
    return V1Deployment(
        metadata=V1ObjectMeta(name=name, namespace=namespace),
        spec=V1DeploymentSpec(
            replicas=replicas,
            selector=V1LabelSelector(match_labels={"app": name}),
            template=V1PodTemplateSpec(spec=V1PodSpec(containers=containers)),
        ),
    )


def make_replicaset(
    name: str,
    namespace: str = "default",
    uid: str | None = DEFAULT_REPLICASET_UID,
    deployment_owner_name: str | None = None,
    deployment_owner_uid: str = DEFAULT_DEPLOYMENT_UID,
) -> V1ReplicaSet:
    """Build a ReplicaSet. Pass deployment_owner_name to make it Deployment-owned;

    leave it None to represent a standalone ReplicaSet.
    """
    owner_references = None
    if deployment_owner_name:
        owner_references = [
            V1OwnerReference(
                api_version="apps/v1",
                kind="Deployment",
                name=deployment_owner_name,
                uid=deployment_owner_uid,
                controller=True,
            )
        ]
    return V1ReplicaSet(
        metadata=V1ObjectMeta(
            name=name, namespace=namespace, uid=uid, owner_references=owner_references
        ),
        spec=V1ReplicaSetSpec(
            selector=V1LabelSelector(match_labels={"app": name}),
            template=V1PodTemplateSpec(spec=V1PodSpec(containers=[make_container()])),
        ),
    )


class ListResult:
    """Minimal stand-in for the *List objects the Kubernetes client returns."""

    def __init__(self, items: list) -> None:
        self.items = items
