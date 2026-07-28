"""Tests for KubernetesCollector using a fake API client (no live cluster)."""

from __future__ import annotations

import json
import ssl
from unittest.mock import MagicMock

import pytest
import urllib3.exceptions
from kubernetes.client.exceptions import ApiException

from cloudops_guard.collectors.kubernetes import (
    CollectorError,
    KubernetesCollector,
    create_api_clients,
)
from cloudops_guard.models import NamespaceInfo
from tests.fixtures.builders import (
    ListResult,
    make_container,
    make_container_status,
    make_deployment,
    make_namespace,
    make_pod,
    make_replicaset,
)


def make_collector(
    core_v1: MagicMock, apps_v1: MagicMock, context: str = "test-context"
) -> KubernetesCollector:
    return KubernetesCollector(core_v1, apps_v1, context)


def empty_apps_v1() -> MagicMock:
    apps_v1 = MagicMock()
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult([])
    apps_v1.list_namespaced_deployment.return_value = ListResult([])
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult([])
    apps_v1.list_namespaced_replica_set.return_value = ListResult([])
    return apps_v1


# --- All-namespace mode -----------------------------------------------------


def test_all_namespace_collection_calls_cluster_wide_apis() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult(
        [make_namespace("default"), make_namespace("kube-system")]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("web-abc123", namespace="default")]
    )
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="default")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert {ns.name for ns in snapshot.namespaces} == {"default", "kube-system"}
    assert len(snapshot.pods) == 1
    assert len(snapshot.deployments) == 1
    assert snapshot.context == "test-context"
    core_v1.list_namespace.assert_called_once()
    core_v1.list_pod_for_all_namespaces.assert_called_once()
    apps_v1.list_deployment_for_all_namespaces.assert_called_once()
    apps_v1.list_replica_set_for_all_namespaces.assert_called_once()


def test_empty_cluster_produces_empty_snapshot() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult([])

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.namespaces == []
    assert snapshot.pods == []
    assert snapshot.deployments == []


# --- Namespace-scoped least-privilege mode ----------------------------------


def test_namespace_scoped_never_calls_list_namespace() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    # Configured to fail loudly if ever called, proving cluster-wide namespace
    # permission is not required for a namespace-scoped audit.
    core_v1.list_namespace.side_effect = ApiException(status=403, reason="Forbidden")
    core_v1.list_namespaced_pod.return_value = ListResult([make_pod("web-1", namespace="default")])

    snapshot = make_collector(core_v1, apps_v1).collect(namespace="default")

    core_v1.list_namespace.assert_not_called()
    assert [ns.name for ns in snapshot.namespaces] == ["default"]


def test_namespace_scoped_never_calls_all_namespace_workload_apis() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespaced_pod.return_value = ListResult([])

    make_collector(core_v1, apps_v1).collect(namespace="default")

    core_v1.list_pod_for_all_namespaces.assert_not_called()
    apps_v1.list_deployment_for_all_namespaces.assert_not_called()
    apps_v1.list_replica_set_for_all_namespaces.assert_not_called()


def test_namespace_scoped_uses_only_namespaced_apis() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespaced_pod.return_value = ListResult([make_pod("web-1", namespace="default")])
    apps_v1.list_namespaced_deployment.return_value = ListResult(
        [make_deployment("web", namespace="default")]
    )
    apps_v1.list_namespaced_replica_set.return_value = ListResult([])

    snapshot = make_collector(core_v1, apps_v1).collect(namespace="default")

    core_v1.list_namespaced_pod.assert_called_once_with(namespace="default")
    apps_v1.list_namespaced_deployment.assert_called_once_with(namespace="default")
    apps_v1.list_namespaced_replica_set.assert_called_once_with(namespace="default")
    assert len(snapshot.pods) == 1
    assert len(snapshot.deployments) == 1


def test_namespace_scoped_collector_succeeds_without_cluster_wide_permission() -> None:
    """A service account restricted to one namespace has no `list namespaces` RBAC.

    list_namespace is configured to raise 403 to prove collection never
    depends on it succeeding in namespace-scoped mode.
    """
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.side_effect = ApiException(status=403, reason="Forbidden")
    core_v1.list_namespaced_pod.return_value = ListResult([make_pod("web-1", namespace="payments")])
    apps_v1.list_namespaced_deployment.return_value = ListResult(
        [make_deployment("web", namespace="payments")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect(namespace="payments")

    assert snapshot.namespaces == [NamespaceInfo(name="payments")]
    assert len(snapshot.pods) == 1


# --- Per-container runtime status --------------------------------------------


def test_container_statuses_are_collected_per_container() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    containers = [make_container(name="app"), make_container(name="sidecar")]
    statuses = [
        make_container_status(name="app", restart_count=3, ready=True),
        make_container_status(
            name="sidecar", restart_count=4, ready=False, waiting_reason="CrashLoopBackOff"
        ),
    ]
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("multi", containers=containers, container_statuses=statuses)]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    by_name = {s.container_name: s for s in snapshot.pods[0].container_statuses}
    assert by_name["app"].restart_count == 3
    assert by_name["app"].ready is True
    assert by_name["sidecar"].restart_count == 4
    assert by_name["sidecar"].ready is False
    assert by_name["sidecar"].waiting_reason == "CrashLoopBackOff"


def test_container_statuses_empty_without_pod_status() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult([make_pod("no-status")])

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].container_statuses == []


def test_last_termination_reason_is_collected() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    statuses = [
        make_container_status(name="app", restart_count=2, last_termination_reason="OOMKilled")
    ]
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("oom-pod", container_statuses=statuses)]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].container_statuses[0].last_termination_reason == "OOMKilled"


# --- Ownership resolution via real owner references -------------------------
#
# These tests exercise the full, verified Pod -> ReplicaSet -> Deployment
# chain: a ReplicaSet's claimed Deployment owner is only honored when a
# Deployment with a matching (namespace, UID) -- or (namespace, name) when
# UID is unavailable -- is actually present among the Deployments collected
# in the same snapshot. An unverifiable claim (deleted, absent, or
# UID-mismatched Deployment) is indistinguishable from a standalone
# ReplicaSet: both yield owning_deployment=None so the pod still gets its
# own container-level checks.


def test_pod_owned_by_replicaset_is_attributed_to_its_deployment() -> None:
    """1. Valid Pod -> ReplicaSet -> Deployment with matching UIDs throughout."""
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="default", uid="deploy-web")]
    )
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "web-6c9c8f9d7",
                namespace="default",
                uid="rs-web",
                deployment_owner_name="web",
                deployment_owner_uid="deploy-web",
            )
        ]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [
            make_pod(
                "web-6c9c8f9d7-abcde",
                namespace="default",
                owner_name="web-6c9c8f9d7",
                owner_uid="rs-web",
            )
        ]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment == "web"


def test_pod_owner_reference_with_controller_false_is_ignored() -> None:
    """2. A Pod's ReplicaSet owner reference with controller=False must be ignored."""
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="default", uid="deploy-web")]
    )
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "web-111",
                namespace="default",
                uid="rs-web",
                deployment_owner_name="web",
                deployment_owner_uid="deploy-web",
            )
        ]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [
            make_pod(
                "web-111-xyz",
                namespace="default",
                owner_name="web-111",
                owner_uid="rs-web",
                owner_controller=False,
            )
        ]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


def test_replicaset_owner_reference_with_controller_false_is_ignored() -> None:
    """3. A ReplicaSet's Deployment owner reference with controller=False must be ignored."""
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="default", uid="deploy-web")]
    )
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "web-111",
                namespace="default",
                uid="rs-web",
                deployment_owner_name="web",
                deployment_owner_uid="deploy-web",
                deployment_owner_controller=False,
            )
        ]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("web-111-xyz", namespace="default", owner_name="web-111", owner_uid="rs-web")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


def test_replicaset_references_deployment_absent_from_snapshot() -> None:
    """4. A ReplicaSet claims a Deployment owner that was deleted / not collected."""
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult([])  # deleted/absent
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "web-111",
                namespace="default",
                uid="rs-web",
                deployment_owner_name="web",
                deployment_owner_uid="deploy-web",
            )
        ]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("web-111-xyz", namespace="default", owner_name="web-111", owner_uid="rs-web")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


def test_deployment_recreated_with_same_name_different_uid_is_not_attributed() -> None:
    """5. A Deployment recreated with the same name but a different UID must not

    validate an old ReplicaSet's ownership claim.
    """
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    # The currently-live Deployment "web" has a fresh UID...
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="default", uid="deploy-web-v2")]
    )
    # ...but this ReplicaSet was created under the old, now-deleted "web" (uid v1).
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "web-111",
                namespace="default",
                uid="rs-web",
                deployment_owner_name="web",
                deployment_owner_uid="deploy-web-v1",
            )
        ]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("web-111-xyz", namespace="default", owner_name="web-111", owner_uid="rs-web")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


def test_overlapping_deployment_names_are_disambiguated_by_owner_reference() -> None:
    """8. Deployment "web" and "web-api" must not be confused by name-prefix matching."""
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [
            make_deployment("web", namespace="default", uid="deploy-web"),
            make_deployment("web-api", namespace="default", uid="deploy-web-api"),
        ]
    )
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "web-111",
                namespace="default",
                uid="rs-web",
                deployment_owner_name="web",
                deployment_owner_uid="deploy-web",
            ),
            make_replicaset(
                "web-api-222",
                namespace="default",
                uid="rs-web-api",
                deployment_owner_name="web-api",
                deployment_owner_uid="deploy-web-api",
            ),
        ]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [
            make_pod(
                "web-api-222-xyz",
                namespace="default",
                owner_name="web-api-222",
                owner_uid="rs-web-api",
            ),
        ]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment == "web-api"


def test_standalone_replicaset_is_not_attributed_to_a_deployment() -> None:
    """9. A ReplicaSet with no Deployment owner reference at all (standalone)."""
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "standalone-rs",
                namespace="default",
                uid="rs-standalone",
                deployment_owner_name=None,
            )
        ]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [
            make_pod(
                "standalone-rs-abc",
                namespace="default",
                owner_name="standalone-rs",
                owner_uid="rs-standalone",
            )
        ]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


def test_pod_with_no_owner_reference_is_a_bare_pod() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult([make_pod("standalone-pod")])

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


def test_pod_owned_by_non_replicaset_controller_is_not_attributed() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [
            make_pod(
                "job-abc",
                namespace="default",
                owner_kind="Job",
                owner_name="cleanup-job",
                owner_uid="job-uid",
            )
        ]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


def test_ownership_matching_is_namespace_safe() -> None:
    """10. The same ReplicaSet/Deployment names in two namespaces must not cross-attribute."""
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="team-a", uid="deploy-a")]
    )
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "web-111",
                namespace="team-a",
                uid="rs-a",
                deployment_owner_name="web",
                deployment_owner_uid="deploy-a",
            ),
            make_replicaset("web-111", namespace="team-b", uid="rs-b", deployment_owner_name=None),
        ]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("web-111-xyz", namespace="team-b", owner_name="web-111", owner_uid="rs-b")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


def test_ownership_matching_uses_uid_and_does_not_fall_back_to_name_on_mismatch() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="default", uid="deploy-web")]
    )
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "web-111",
                namespace="default",
                uid="rs-current",
                deployment_owner_name="web",
                deployment_owner_uid="deploy-web",
            )
        ]
    )
    # Owner reference has the right name but a stale/mismatched UID (e.g. the
    # ReplicaSet was recreated). Real ownership resolution must not fall back
    # to name matching when a UID was provided.
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("web-111-xyz", namespace="default", owner_name="web-111", owner_uid="rs-stale")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


def test_ownership_matching_falls_back_to_name_when_uid_unavailable() -> None:
    """6. Owner UID is unavailable; exact namespace-and-name fallback succeeds."""
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="default", uid="deploy-web")]
    )
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "web-111",
                namespace="default",
                uid="rs-current",
                deployment_owner_name="web",
                deployment_owner_uid="deploy-web",
            )
        ]
    )
    # The real Kubernetes API always sets ownerReferences[].uid (it's a required
    # field), so "unavailable" is simulated with an empty string rather than
    # None: the client model's own validation rejects uid=None outright.
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [make_pod("web-111-xyz", namespace="default", owner_name="web-111", owner_uid="")]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment == "web"


def test_pod_owner_uid_unavailable_and_name_does_not_match() -> None:
    """7. Owner UID is unavailable, but the name doesn't match any collected ReplicaSet."""
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.return_value = ListResult(
        [make_deployment("web", namespace="default", uid="deploy-web")]
    )
    apps_v1.list_replica_set_for_all_namespaces.return_value = ListResult(
        [
            make_replicaset(
                "web-111",
                namespace="default",
                uid="rs-current",
                deployment_owner_name="web",
                deployment_owner_uid="deploy-web",
            )
        ]
    )
    core_v1.list_pod_for_all_namespaces.return_value = ListResult(
        [
            make_pod(
                "web-111-xyz", namespace="default", owner_name="totally-different-rs", owner_uid=""
            )
        ]
    )

    snapshot = make_collector(core_v1, apps_v1).collect()

    assert snapshot.pods[0].owning_deployment is None


# --- Transport / error handling ----------------------------------------------


@pytest.mark.parametrize(
    "failing_method",
    ["list_namespace", "list_pod_for_all_namespaces"],
)
def test_api_error_raises_collector_error_with_status(failing_method: str) -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult([])
    getattr(core_v1, failing_method).side_effect = ApiException(status=403, reason="Forbidden")

    with pytest.raises(CollectorError, match="403"):
        make_collector(core_v1, apps_v1).collect()


def test_deployment_api_error_raises_collector_error() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    apps_v1.list_deployment_for_all_namespaces.side_effect = ApiException(
        status=500, reason="Internal Error"
    )

    with pytest.raises(CollectorError, match="500"):
        make_collector(core_v1, apps_v1).collect()


def test_connection_failure_raises_collector_error() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.side_effect = urllib3.exceptions.HTTPError("connection refused")

    with pytest.raises(CollectorError, match="could not reach the Kubernetes API server"):
        make_collector(core_v1, apps_v1).collect()


def test_timeout_raises_collector_error() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.side_effect = urllib3.exceptions.ConnectTimeoutError("timed out")

    with pytest.raises(CollectorError, match="could not reach the Kubernetes API server"):
        make_collector(core_v1, apps_v1).collect()


def test_tls_failure_raises_collector_error() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.side_effect = ssl.SSLError("certificate verify failed")

    with pytest.raises(CollectorError, match="could not reach the Kubernetes API server"):
        make_collector(core_v1, apps_v1).collect()


def test_unexpected_programming_error_is_not_masked_as_a_network_problem() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.side_effect = AttributeError(
        "'NoneType' object has no attribute 'items'"
    )

    with pytest.raises(AttributeError):
        make_collector(core_v1, apps_v1).collect()


def test_error_messages_never_contain_simulated_credentials() -> None:
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.side_effect = urllib3.exceptions.HTTPError(
        "auth failed with token=super-secret-token-value"
    )

    with pytest.raises(CollectorError) as excinfo:
        make_collector(core_v1, apps_v1).collect()

    assert "super-secret-token-value" not in str(excinfo.value)


def test_create_api_clients_config_error_does_not_leak_exception_text(monkeypatch) -> None:
    from kubernetes import config as kube_config

    def fake_load(*args, **kwargs):
        raise kube_config.config_exception.ConfigException(
            "kubeconfig at /home/user/.kube/config contains token=FAKE_TOKEN_abc123"
        )

    monkeypatch.setattr(kube_config, "load_kube_config", fake_load)

    with pytest.raises(CollectorError) as excinfo:
        create_api_clients("missing-context")

    assert "FAKE_TOKEN_abc123" not in str(excinfo.value)
    assert "token=" not in str(excinfo.value)


def test_missing_kubeconfig_file_raises_collector_error(monkeypatch) -> None:
    from kubernetes import config as kube_config

    def fake_load(*args, **kwargs):
        raise FileNotFoundError("no such file: /home/user/.kube/config")

    monkeypatch.setattr(kube_config, "load_kube_config", fake_load)

    with pytest.raises(CollectorError, match="could not read the kubeconfig"):
        create_api_clients("any-context")


def test_kubeconfig_permission_error_raises_collector_error(monkeypatch) -> None:
    """An appropriate OSError subtype beyond FileNotFoundError (e.g. unreadable file)."""
    from kubernetes import config as kube_config

    def fake_load(*args, **kwargs):
        raise PermissionError("permission denied: /home/user/.kube/config")

    monkeypatch.setattr(kube_config, "load_kube_config", fake_load)

    with pytest.raises(CollectorError, match="could not read the kubeconfig"):
        create_api_clients("any-context")


def test_auth_plugin_subprocess_failure_raises_collector_error(monkeypatch) -> None:
    """An exec-based credential plugin failing to run (subprocess.SubprocessError)."""
    import subprocess

    from kubernetes import config as kube_config

    def fake_load(*args, **kwargs):
        raise subprocess.SubprocessError(
            "aws eks get-token failed: AccessDenied for arn:aws:iam::123456789012:user/x"
        )

    monkeypatch.setattr(kube_config, "load_kube_config", fake_load)

    with pytest.raises(CollectorError) as excinfo:
        create_api_clients("eks-context")

    assert "authentication plugin" in str(excinfo.value)
    assert "AccessDenied" not in str(excinfo.value)
    assert "arn:aws:iam" not in str(excinfo.value)


def test_tls_initialization_failure_raises_collector_error(monkeypatch) -> None:
    from kubernetes import config as kube_config

    def fake_load(*args, **kwargs):
        raise ssl.SSLError("certificate verify failed: self-signed certificate")

    monkeypatch.setattr(kube_config, "load_kube_config", fake_load)

    with pytest.raises(CollectorError, match="TLS/certificate initialization"):
        create_api_clients("any-context")


def test_unexpected_error_during_config_load_is_not_masked(monkeypatch) -> None:
    """Programming errors (e.g. AttributeError) must keep propagating, not be

    hidden behind a generic CollectorError.
    """
    from kubernetes import config as kube_config

    def fake_load(*args, **kwargs):
        raise AttributeError("'NoneType' object has no attribute 'get'")

    monkeypatch.setattr(kube_config, "load_kube_config", fake_load)

    with pytest.raises(AttributeError):
        create_api_clients("any-context")


# --- Secret / sensitive data exclusion ---------------------------------------


def test_collected_pod_never_exposes_secret_env_var_values() -> None:
    """The raw pod carries an env var value; the collector's output must not."""
    core_v1 = MagicMock()
    apps_v1 = empty_apps_v1()
    core_v1.list_namespace.return_value = ListResult([])
    core_v1.list_pod_for_all_namespaces.return_value = ListResult([make_pod("web-1")])

    snapshot = make_collector(core_v1, apps_v1).collect()

    dumped = json.dumps(snapshot.model_dump(mode="json"))
    assert "super-secret-value" not in dumped
    assert "SECRET_KEY" not in dumped
    # ContainerInfo only exposes name, image and resource quantities.
    assert set(snapshot.pods[0].containers[0].model_dump().keys()) == {"name", "image", "resources"}
