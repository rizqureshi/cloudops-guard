"""Read-only collection of Kubernetes metadata into normalized internal models.

This module never reads Secret contents, ConfigMap contents, container
environment variable values, or pod logs, and it never calls any Kubernetes
API that mutates cluster state. The Kubernetes API client is injected so
tests can exercise this module without a live cluster.

When a namespace is given, collection only ever calls namespace-scoped APIs
(`list_namespaced_*`) so a service account restricted to that namespace can
run a complete audit without cluster-wide `list namespaces` permission.
"""

from __future__ import annotations

import datetime as dt
import logging
import ssl
from collections.abc import Callable
from dataclasses import dataclass

import urllib3.exceptions
from kubernetes import client
from kubernetes import config as kube_config
from kubernetes.client import (
    AppsV1Api,
    CoreV1Api,
    V1Container,
    V1Deployment,
    V1Pod,
    V1ReplicaSet,
)
from kubernetes.client.exceptions import ApiException

from cloudops_guard.models import (
    ClusterSnapshot,
    ContainerInfo,
    ContainerRuntimeStatus,
    DeploymentInfo,
    NamespaceInfo,
    PodInfo,
    ResourceRequirements,
)

logger = logging.getLogger(__name__)

# Connection-level failures (DNS, refused connections, timeouts, TLS/certificate
# problems). ssl.SSLError is already an OSError subclass in Python 3; it is
# listed explicitly for readability. Deliberately narrow: programming errors
# such as AttributeError or TypeError are NOT caught here and propagate as-is.
_TRANSPORT_EXCEPTIONS: tuple[type[Exception], ...] = (
    urllib3.exceptions.HTTPError,
    ssl.SSLError,
    OSError,
)


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
    except kube_config.config_exception.ConfigException:
        # The exception text can include kubeconfig file paths or other
        # locally-sensitive detail, so it is deliberately not interpolated.
        raise CollectorError(
            f"Unable to load Kubernetes context {context!r}. Check that it exists in your "
            f"kubeconfig (see `kubectl config get-contexts`)."
        ) from None
    except FileNotFoundError as exc:
        raise CollectorError(
            "Kubeconfig file not found. Set the KUBECONFIG environment variable or create "
            "~/.kube/config."
        ) from exc

    api_client = client.ApiClient()
    return client.CoreV1Api(api_client), client.AppsV1Api(api_client)


def _call_api[T](operation: str, func: Callable[[], T]) -> T:
    """Run a single Kubernetes API call, translating failures into CollectorError.

    Only expected API and transport failures are translated. Programming
    errors (AttributeError, TypeError, etc.) are intentionally left to
    propagate so they are not misreported as network problems.
    """
    try:
        return func()
    except ApiException as exc:
        raise CollectorError(f"{operation} failed (HTTP {exc.status}: {exc.reason})") from None
    except _TRANSPORT_EXCEPTIONS as exc:
        raise CollectorError(
            f"{operation} failed: could not reach the Kubernetes API server "
            f"({type(exc).__name__}). Check network connectivity, DNS and TLS configuration."
        ) from None


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


def _container_runtime_statuses(pod: V1Pod) -> list[ContainerRuntimeStatus]:
    statuses = pod.status.container_statuses if pod.status else None
    if not statuses:
        return []
    result = []
    for status in statuses:
        waiting_reason = (
            status.state.waiting.reason if status.state and status.state.waiting else None
        )
        last_termination_reason = (
            status.last_state.terminated.reason
            if status.last_state and status.last_state.terminated
            else None
        )
        result.append(
            ContainerRuntimeStatus(
                container_name=status.name,
                restart_count=status.restart_count,
                ready=bool(status.ready),
                waiting_reason=waiting_reason,
                last_termination_reason=last_termination_reason,
            )
        )
    return result


@dataclass(frozen=True)
class _ReplicaSetOwnership:
    """Just enough normalized ReplicaSet metadata to resolve pod ownership."""

    namespace: str
    name: str
    uid: str | None
    deployment_name: str | None  # None if this ReplicaSet is not Deployment-owned


def _replicaset_deployment_owner(replicaset: V1ReplicaSet) -> str | None:
    owners = replicaset.metadata.owner_references or []
    for owner in owners:
        if owner.kind == "Deployment":
            return owner.name
    return None


def _pod_replicaset_owner_ref(pod: V1Pod):
    owners = pod.metadata.owner_references or []
    for owner in owners:
        if owner.kind == "ReplicaSet":
            return owner
    return None


class KubernetesCollector:
    """Collects namespace, pod, deployment and ownership metadata for one context."""

    def __init__(self, core_v1: CoreV1Api, apps_v1: AppsV1Api, context: str) -> None:
        self._core_v1 = core_v1
        self._apps_v1 = apps_v1
        self._context = context

    def collect(self, namespace: str | None = None) -> ClusterSnapshot:
        """Collect a full snapshot, optionally restricted to a single namespace.

        When namespace is given, only namespace-scoped APIs are called: no
        `list_namespace`, `list_pod_for_all_namespaces`,
        `list_deployment_for_all_namespaces` or
        `list_replica_set_for_all_namespaces`. This lets a service account
        scoped to a single namespace run a complete audit.
        """
        namespaces = self._collect_namespaces(namespace)
        deployments = self._collect_deployments(namespace)
        replicasets = self._collect_replicasets(namespace)
        pods = self._collect_pods(namespace, replicasets)
        return ClusterSnapshot(
            context=self._context,
            collected_at=dt.datetime.now(dt.UTC),
            namespaces=namespaces,
            pods=pods,
            deployments=deployments,
        )

    def _collect_namespaces(self, namespace: str | None) -> list[NamespaceInfo]:
        if namespace:
            return [NamespaceInfo(name=namespace)]
        result = _call_api("List namespaces", self._core_v1.list_namespace)
        return [NamespaceInfo(name=ns.metadata.name) for ns in result.items]

    def _collect_deployments(self, namespace: str | None) -> list[DeploymentInfo]:
        if namespace:
            result = _call_api(
                "List deployments",
                lambda: self._apps_v1.list_namespaced_deployment(namespace=namespace),
            )
        else:
            result = _call_api("List deployments", self._apps_v1.list_deployment_for_all_namespaces)
        return [self._deployment_info(deployment) for deployment in result.items]

    def _deployment_info(self, deployment: V1Deployment) -> DeploymentInfo:
        containers = deployment.spec.template.spec.containers if deployment.spec else []
        return DeploymentInfo(
            name=deployment.metadata.name,
            namespace=deployment.metadata.namespace,
            replicas=deployment.spec.replicas or 0,
            containers=[_container_info(c) for c in containers],
        )

    def _collect_replicasets(self, namespace: str | None) -> list[_ReplicaSetOwnership]:
        if namespace:
            result = _call_api(
                "List replica sets",
                lambda: self._apps_v1.list_namespaced_replica_set(namespace=namespace),
            )
        else:
            result = _call_api(
                "List replica sets", self._apps_v1.list_replica_set_for_all_namespaces
            )
        return [
            _ReplicaSetOwnership(
                namespace=rs.metadata.namespace,
                name=rs.metadata.name,
                uid=rs.metadata.uid,
                deployment_name=_replicaset_deployment_owner(rs),
            )
            for rs in result.items
        ]

    def _collect_pods(
        self, namespace: str | None, replicasets: list[_ReplicaSetOwnership]
    ) -> list[PodInfo]:
        if namespace:
            result = _call_api(
                "List pods", lambda: self._core_v1.list_namespaced_pod(namespace=namespace)
            )
        else:
            result = _call_api("List pods", self._core_v1.list_pod_for_all_namespaces)

        by_uid = {(rs.namespace, rs.uid): rs.deployment_name for rs in replicasets if rs.uid}
        by_name = {(rs.namespace, rs.name): rs.deployment_name for rs in replicasets}
        return [self._pod_info(pod, by_uid, by_name) for pod in result.items]

    def _pod_info(
        self,
        pod: V1Pod,
        replicasets_by_uid: dict[tuple[str, str], str | None],
        replicasets_by_name: dict[tuple[str, str], str | None],
    ) -> PodInfo:
        pod_namespace = pod.metadata.namespace
        owning_deployment = self._resolve_owning_deployment(
            pod, pod_namespace, replicasets_by_uid, replicasets_by_name
        )
        containers = pod.spec.containers if pod.spec else []
        return PodInfo(
            name=pod.metadata.name,
            namespace=pod_namespace,
            containers=[_container_info(c) for c in containers],
            container_statuses=_container_runtime_statuses(pod),
            owning_deployment=owning_deployment,
        )

    @staticmethod
    def _resolve_owning_deployment(
        pod: V1Pod,
        namespace: str,
        replicasets_by_uid: dict[tuple[str, str], str | None],
        replicasets_by_name: dict[tuple[str, str], str | None],
    ) -> str | None:
        """Resolve Pod -> ReplicaSet -> Deployment via real owner references.

        Matches by (namespace, UID) whenever the owning ReplicaSet's UID is
        known, falling back to (namespace, name) only when it is not. A
        ReplicaSet that is not itself Deployment-owned (a standalone
        ReplicaSet), or one CloudOps Guard could not see/collect, correctly
        yields None: the pod is then treated as a bare pod and checked
        directly rather than silently skipped.
        """
        owner_ref = _pod_replicaset_owner_ref(pod)
        if owner_ref is None:
            return None
        if owner_ref.uid:
            return replicasets_by_uid.get((namespace, owner_ref.uid))
        return replicasets_by_name.get((namespace, owner_ref.name))
