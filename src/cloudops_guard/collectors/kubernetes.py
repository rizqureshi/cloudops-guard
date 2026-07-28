"""Read-only collection of Kubernetes metadata into normalized internal models.

This module never reads Secret contents, ConfigMap contents, container
environment variable values, or pod logs, and it never calls any Kubernetes
API that mutates cluster state. The Kubernetes API client is injected so
tests can exercise this module without a live cluster.
"""

from __future__ import annotations

import datetime as dt
import logging

from kubernetes import client
from kubernetes import config as kube_config
from kubernetes.client import AppsV1Api, CoreV1Api, V1Container, V1Deployment, V1Pod
from kubernetes.client.exceptions import ApiException

from cloudops_guard.models import (
    ClusterSnapshot,
    ContainerInfo,
    DeploymentInfo,
    NamespaceInfo,
    PodInfo,
    ResourceRequirements,
)

logger = logging.getLogger(__name__)


class CollectorError(Exception):
    """Raised when Kubernetes context loading or metadata collection fails.

    Messages are kept free of credentials, tokens and certificate material.
    """


def create_api_clients(context: str) -> tuple[CoreV1Api, AppsV1Api]:
    """Load the given kubeconfig context and build API clients for it.

    Raises CollectorError with a clear, credential-free message if the
    kubeconfig file is missing, the context does not exist, or the config
    cannot otherwise be loaded.
    """
    try:
        kube_config.load_kube_config(context=context)
    except kube_config.config_exception.ConfigException as exc:
        raise CollectorError(
            f"Unable to load Kubernetes context {context!r}. Check that it exists in your "
            f"kubeconfig (see `kubectl config get-contexts`). Details: {exc}"
        ) from None
    except FileNotFoundError as exc:
        raise CollectorError(
            "Kubeconfig file not found. Set the KUBECONFIG environment variable or create "
            "~/.kube/config."
        ) from exc

    api_client = client.ApiClient()
    return client.CoreV1Api(api_client), client.AppsV1Api(api_client)


def _resource_requirements(container: V1Container) -> ResourceRequirements:
    resources = container.resources
    requests = (resources.requests or {}) if resources else {}
    limits = (resources.limits or {}) if resources else {}
    return ResourceRequirements(
        cpu_request=requests.get("cpu"),
        memory_request=requests.get("memory"),
        cpu_limit=limits.get("cpu"),
        memory_limit=limits.get("memory"),
    )


def _container_info(container: V1Container) -> ContainerInfo:
    return ContainerInfo(
        name=container.name,
        image=container.image or "",
        resources=_resource_requirements(container),
    )


def _restart_count(pod: V1Pod) -> int:
    statuses = pod.status.container_statuses if pod.status else None
    if not statuses:
        return 0
    return sum(status.restart_count for status in statuses)


def _replicaset_owner_name(pod: V1Pod) -> str | None:
    owners = pod.metadata.owner_references or []
    for owner in owners:
        if owner.kind == "ReplicaSet":
            return owner.name
    return None


def _matching_deployment_name(
    replicaset_name: str, namespace: str, deployments: list[DeploymentInfo]
) -> str | None:
    for deployment in deployments:
        if deployment.namespace == namespace and replicaset_name.startswith(f"{deployment.name}-"):
            return deployment.name
    return None


class KubernetesCollector:
    """Collects namespace, pod and deployment metadata from a single cluster context."""

    def __init__(self, core_v1: CoreV1Api, apps_v1: AppsV1Api, context: str) -> None:
        self._core_v1 = core_v1
        self._apps_v1 = apps_v1
        self._context = context

    def collect(self, namespace: str | None = None) -> ClusterSnapshot:
        """Collect a full snapshot, optionally restricted to a single namespace."""
        namespaces = self._collect_namespaces()
        deployments = self._collect_deployments(namespace)
        pods = self._collect_pods(namespace, deployments)
        return ClusterSnapshot(
            context=self._context,
            collected_at=dt.datetime.now(dt.UTC),
            namespaces=namespaces,
            pods=pods,
            deployments=deployments,
        )

    def _collect_namespaces(self) -> list[NamespaceInfo]:
        try:
            result = self._core_v1.list_namespace()
        except ApiException as exc:
            raise CollectorError(
                f"Failed to list namespaces (HTTP {exc.status}: {exc.reason})"
            ) from None
        return [NamespaceInfo(name=ns.metadata.name) for ns in result.items]

    def _collect_deployments(self, namespace: str | None) -> list[DeploymentInfo]:
        try:
            if namespace:
                result = self._apps_v1.list_namespaced_deployment(namespace=namespace)
            else:
                result = self._apps_v1.list_deployment_for_all_namespaces()
        except ApiException as exc:
            raise CollectorError(
                f"Failed to list deployments (HTTP {exc.status}: {exc.reason})"
            ) from None
        return [self._deployment_info(deployment) for deployment in result.items]

    def _deployment_info(self, deployment: V1Deployment) -> DeploymentInfo:
        containers = deployment.spec.template.spec.containers if deployment.spec else []
        return DeploymentInfo(
            name=deployment.metadata.name,
            namespace=deployment.metadata.namespace,
            replicas=deployment.spec.replicas or 0,
            containers=[_container_info(c) for c in containers],
        )

    def _collect_pods(
        self, namespace: str | None, deployments: list[DeploymentInfo]
    ) -> list[PodInfo]:
        try:
            if namespace:
                result = self._core_v1.list_namespaced_pod(namespace=namespace)
            else:
                result = self._core_v1.list_pod_for_all_namespaces()
        except ApiException as exc:
            raise CollectorError(f"Failed to list pods (HTTP {exc.status}: {exc.reason})") from None
        return [self._pod_info(pod, deployments) for pod in result.items]

    def _pod_info(self, pod: V1Pod, deployments: list[DeploymentInfo]) -> PodInfo:
        pod_namespace = pod.metadata.namespace
        owning_deployment = None
        replicaset_name = _replicaset_owner_name(pod)
        if replicaset_name:
            owning_deployment = _matching_deployment_name(
                replicaset_name, pod_namespace, deployments
            )
        containers = pod.spec.containers if pod.spec else []
        return PodInfo(
            name=pod.metadata.name,
            namespace=pod_namespace,
            containers=[_container_info(c) for c in containers],
            restart_count=_restart_count(pod),
            owning_deployment=owning_deployment,
        )
