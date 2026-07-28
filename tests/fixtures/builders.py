"""Factory helpers for building representative Kubernetes API objects in tests.

These build real `kubernetes.client` model instances (not hand-rolled dicts)
so collector tests exercise the same shapes the official client returns.
"""

from __future__ import annotations

from kubernetes.client import (
    V1Container,
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
    V1ResourceRequirements,
)


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


def make_pod(
    name: str,
    namespace: str = "default",
    containers: list[V1Container] | None = None,
    restart_counts: list[int] | None = None,
    owner_replicaset_name: str | None = None,
) -> V1Pod:
    containers = containers or [make_container()]
    owner_references = None
    if owner_replicaset_name:
        owner_references = [
            V1OwnerReference(
                api_version="apps/v1",
                kind="ReplicaSet",
                name=owner_replicaset_name,
                uid="00000000-0000-0000-0000-000000000000",
                controller=True,
            )
        ]
    container_statuses = None
    if restart_counts is not None:
        container_statuses = [
            V1ContainerStatus(
                name=c.name,
                restart_count=count,
                image=c.image,
                image_id="",
                ready=True,
                started=True,
            )
            for c, count in zip(containers, restart_counts, strict=True)
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


class ListResult:
    """Minimal stand-in for the *List objects the Kubernetes client returns."""

    def __init__(self, items: list) -> None:
        self.items = items
