"""Tests for KubernetesCollector using a fake API client (no live cluster)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from cloudops_guard.collectors.kubernetes import CollectorError, KubernetesCollector
from tests.fixtures.builders import (
    ListResult,
    make_container,
    make_deployment,
    make_namespace,
    make_pod,
)


def make_collector(
    core_v1: MagicMock, apps_v1: MagicMock, context: str = "test-context"
) -> KubernetesCollector:
    return KubernetesCollector(core_v1, apps_v1, context)


def test_collect_returns_namespaces_pods_and_deployments() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    core_v1.list_namespace.return_value = ListResult(
        [make_namespace("default"), make_namespace("kube-system")]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("web-abc123", namespace="default", owner_replicaset_name="web-abc123")]
    )
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="default")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert {ns.name for ns in snapshot.namespaces} == {"default", "kube-system"}
    assert len(snapshot.pods) == 1
    assert len(snapshot.deployments) == 1
    assert snapshot.context == "test-context"
    core_v1.list_pod_for_all_namespaces.assert_called_once()
    apps_v1.list_deployment_for_all_namespaces.assert_called_once()


def test_collect_restricts_to_namespace_when_given() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    core_v1.list_namespace.return_value = ListResult([make_namespace("default")])
    core_v1.list_namespaced_pod.return_value = ListResult([make_pod("web-1", namespace="default")])
    apps_v1.list_namespaced_deployment.return_value = ListResult(
        [make_deployment("web", namespace="default")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect(namespace="default")

    core_v1.list_namespaced_pod.assert_called_once_with(namespace="default")
    apps_v1.list_namespaced_deployment.assert_called_once_with(namespace="default")
    core_v1.list_pod_for_all_namespaces.assert_not_called()
    assert len(snapshot.pods) == 1


def test_pod_restart_count_sums_all_containers() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    containers = [make_container(name="app"), make_container(name="sidecar")]
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("multi", containers=containers, restart_counts=[3, 4])]
    )
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult([])

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].restart_count == 7


def test_pod_restart_count_defaults_to_zero_without_status() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult([make_pod("no-status")])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult([])

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].restart_count == 0


def test_pod_owned_by_deployment_is_attributed_by_replicaset_prefix() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [
            make_pod(
                "web-6c9c8f9d7-abcde", namespace="default", owner_replicaset_name="web-6c9c8f9d7"
            )
        ]
    )
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="default")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment == "web"


def test_bare_pod_has_no_owning_deployment() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult([make_pod("standalone-pod")])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult([])

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


def test_empty_cluster_produces_empty_snapshot() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult([])

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.namespaces == []
    assert snapshot.pods == []
    assert snapshot.deployments == []


@pytest.mark.parametrize(
    "failing_method",
    ["list_namespace", "list_pod_for_all_namespaces"],
)
def test_api_error_raises_collector_error(failing_method: str) -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult([])
    getattr(core_v1, failing_method).side_effect = ApiException(status=403, reason="Forbidden")

    with pytest.raises(CollectorError, match="403"):
        make_collector(core_v1, apps_v1).collect()


def test_deployment_api_error_raises_collector_error() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.side_effect = ApiException(
        status=500, reason="Internal Error"
    )

    with pytest.raises(CollectorError, match="500"):
        make_collector(core_v1, apps_v1).collect()


def test_collected_pod_never_exposes_secret_env_var_values() -> None:
    """The raw pod carries an env var value; the collector's output must not."""
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult([make_pod("web-1")])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult([])

    snapshot = make_collector(core_v1, apps_v1).collect()

    dumped = json.dumps(snapshot.model_dump(mode="json"))
    assert "super-secret-value" not in dumped
    assert "SECRET_KEY" not in dumped
    # ContainerInfo only exposes name, image and resource quantities.
    assert set(snapshot.pods[0].containers[0].model_dump().keys()) == {"name", "image", "resources"}
