"""Structural tests for the Phase 4G-A Bicep infrastructure (task 9):
region, network, identity, SKU, replica, and cost constraints. Compiles
the real `.bicep` files with the real, standalone Bicep CLI (never a
hand-parsed approximation of Bicep syntax) and asserts properties of the
resulting ARM JSON template.

**No Azure deployment or `what-if` is ever run** -- `bicep build` is a
purely local, offline compiler; nothing in this file contacts Azure.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INFRA_DIR = REPO_ROOT / "infra" / "azure"


def _find_bicep_cli() -> str | None:
    found = shutil.which("bicep")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "bicep"
    if local.is_file():
        return str(local)
    return None


_BICEP_CLI = _find_bicep_cli()
pytestmark = pytest.mark.skipif(_BICEP_CLI is None, reason="bicep CLI not found on PATH")


def _compile(bicep_file: str, tmp_path: Path) -> dict[str, Any]:
    out_path = tmp_path / f"{bicep_file}.json"
    result = subprocess.run(
        [_BICEP_CLI, "build", str(INFRA_DIR / bicep_file), "--outfile", str(out_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"bicep build failed: {result.stderr}"
    return json.loads(out_path.read_text())


def _iter_all_resources(template: dict[str, Any]):
    """Yields every resource in `template`, recursing into nested
    (`Microsoft.Resources/deployments`) module templates -- `bicep
    build`'s own module-inlining strategy nests each module's compiled
    template under `properties.template` of a wrapping deployment
    resource, so a naive top-level-only scan would miss almost
    everything this infrastructure actually defines.
    """
    for resource in template.get("resources", []):
        yield resource
        nested = resource.get("properties", {}).get("template")
        if isinstance(nested, dict):
            yield from _iter_all_resources(nested)


def _resources_of_type(template: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
    return [r for r in _iter_all_resources(template) if r.get("type") == resource_type]


def _default_param(template: dict[str, Any], name: str) -> Any:
    return template.get("parameters", {}).get(name, {}).get("defaultValue")


@pytest.fixture(scope="module")
def foundation_template(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _compile("foundation.bicep", tmp_path_factory.mktemp("foundation"))


@pytest.fixture(scope="module")
def app_template(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _compile("app.bicep", tmp_path_factory.mktemp("app"))


class TestRegionConstraint:
    """Every data-bearing resource must default to canadacentral -- no
    provider-default location anywhere (task 9).
    """

    @pytest.mark.parametrize(
        "resource_type",
        [
            "Microsoft.Network/virtualNetworks",
            "Microsoft.ContainerRegistry/registries",
            "Microsoft.Storage/storageAccounts",
            "Microsoft.DBforPostgreSQL/flexibleServers",
            "Microsoft.KeyVault/vaults",
            "Microsoft.App/managedEnvironments",
        ],
    )
    def test_resource_location_defaults_to_canadacentral(
        self, foundation_template: dict[str, Any], resource_type: str
    ) -> None:
        for resource in _resources_of_type(foundation_template, resource_type):
            location = resource.get("location")
            # A module's own nested template parameter is what actually
            # carries the default -- resolve it there.
            assert location in (
                "canadacentral",
                "[parameters('location')]",
            ), f"{resource_type} has unexpected location expression: {location}"

    def test_every_module_location_parameter_defaults_to_canadacentral(
        self, foundation_template: dict[str, Any]
    ) -> None:
        for resource in _iter_all_resources(foundation_template):
            nested = resource.get("properties", {}).get("template")
            if isinstance(nested, dict) and "location" in nested.get("parameters", {}):
                default = _default_param(nested, "location")
                assert default == "canadacentral", (
                    f"module {resource.get('name')} location parameter defaults to "
                    f"{default!r}, not 'canadacentral'"
                )


class TestNetworkConstraint:
    def test_vnet_has_delegated_container_apps_and_postgres_subnets(
        self, foundation_template: dict[str, Any]
    ) -> None:
        vnets = _resources_of_type(foundation_template, "Microsoft.Network/virtualNetworks")
        assert len(vnets) == 1
        subnets = vnets[0]["properties"]["subnets"]
        delegated_services = {
            d["properties"]["serviceName"]
            for s in subnets
            for d in s["properties"].get("delegations", [])
        }
        assert "Microsoft.App/environments" in delegated_services
        assert "Microsoft.DBforPostgreSQL/flexibleServers" in delegated_services

    def test_postgres_has_no_public_network_access(
        self, foundation_template: dict[str, Any]
    ) -> None:
        servers = _resources_of_type(
            foundation_template, "Microsoft.DBforPostgreSQL/flexibleServers"
        )
        assert len(servers) == 1
        # Private access is expressed via a delegated subnet + private
        # DNS zone, not a publicNetworkAccess toggle, for this resource
        # type -- assert the delegated-subnet wiring exists instead.
        network = servers[0]["properties"]["network"]
        assert "delegatedSubnetResourceId" in network
        assert "privateDnsZoneArmResourceId" in network

    def test_storage_account_denies_public_network_access(
        self, foundation_template: dict[str, Any]
    ) -> None:
        accounts = _resources_of_type(foundation_template, "Microsoft.Storage/storageAccounts")
        assert len(accounts) == 1
        props = accounts[0]["properties"]
        assert props["publicNetworkAccess"] == "Disabled"
        assert props["networkAcls"]["defaultAction"] == "Deny"
        assert props["allowBlobPublicAccess"] is False
        assert props["allowSharedKeyAccess"] is False

    def test_blob_container_has_no_anonymous_access(
        self, foundation_template: dict[str, Any]
    ) -> None:
        containers = _resources_of_type(
            foundation_template, "Microsoft.Storage/storageAccounts/blobServices/containers"
        )
        assert len(containers) == 1
        assert containers[0]["properties"]["publicAccess"] == "None"

    def test_key_vault_denies_public_network_access(
        self, foundation_template: dict[str, Any]
    ) -> None:
        vaults = _resources_of_type(foundation_template, "Microsoft.KeyVault/vaults")
        assert len(vaults) == 1
        props = vaults[0]["properties"]
        assert props["publicNetworkAccess"] == "Disabled"
        assert props["networkAcls"]["defaultAction"] == "Deny"

    def test_no_deployable_resource_ever_creates_a_key_vault_secret(
        self, foundation_template: dict[str, Any], app_template: dict[str, Any]
    ) -> None:
        """Correction pass, item 4 (a full removal, not merely a
        conditional one): an earlier version of this infrastructure
        declared eight placeholder `Microsoft.KeyVault/vaults/secrets`
        resources, gated behind a `createPlaceholderSecrets` parameter,
        so that resource type could still write the literal string
        `REPLACE-AT-DEPLOYMENT-TIME-NEVER-COMMITTED` into a production
        secret under the wrong parameter value. This structurally
        stronger test asserts the resource type itself is entirely
        absent from both compiled templates -- there is no parameter
        value that can make this infrastructure create or write a
        secret; every per-secret role assignment
        (`keyvault-secret-rbac.bicep`) uses only `existing` references.
        """
        assert _resources_of_type(foundation_template, "Microsoft.KeyVault/vaults/secrets") == []
        assert _resources_of_type(app_template, "Microsoft.KeyVault/vaults/secrets") == []

    def test_assign_key_vault_secret_rbac_defaults_to_false(
        self, foundation_template: dict[str, Any]
    ) -> None:
        """The per-secret RBAC module must not be deployed on the first,
        secret-free foundation deployment -- its `existing` secret
        references would fail to resolve since nothing has created those
        secrets yet (deliberately: Bicep never does).
        """
        assert _default_param(foundation_template, "assignKeyVaultSecretRbac") is False

    def test_key_vault_secret_rbac_module_is_conditional(
        self, foundation_template: dict[str, Any]
    ) -> None:
        by_name = {r["name"]: r for r in foundation_template["resources"]}
        assert (
            by_name["keyvault-secret-rbac"].get("condition")
            == "[parameters('assignKeyVaultSecretRbac')]"
        )

    def test_key_vault_secret_rbac_module_grants_no_role_outside_key_vault_scope(
        self, foundation_template: dict[str, Any]
    ) -> None:
        """Cross-check that the per-secret RBAC module's own role
        assignments are all scoped to a Key Vault secret specifically --
        never the vault itself, the resource group, or the subscription
        -- mirroring the same scoping discipline
        `test_every_registry_pull_identity_has_a_registry_scoped_acr_pull_assignment`
        enforces for AcrPull.
        """
        by_name = {r["name"]: r for r in foundation_template["resources"]}
        rbac_module = by_name["keyvault-secret-rbac"]
        nested = rbac_module["properties"]["template"]
        role_assignments = _resources_of_type(nested, "Microsoft.Authorization/roleAssignments")
        assert role_assignments, "expected at least one per-secret Key Vault role assignment"
        for assignment in role_assignments:
            scope = str(assignment.get("scope", ""))
            assert "Microsoft.KeyVault/vaults/secrets" in scope or "secrets" in scope.lower(), (
                f"role assignment {assignment.get('name')} is not scoped to an individual "
                f"secret: {scope}"
            )


class TestIdentityConstraint:
    def test_acr_admin_account_is_disabled(self, foundation_template: dict[str, Any]) -> None:
        registries = _resources_of_type(
            foundation_template, "Microsoft.ContainerRegistry/registries"
        )
        assert len(registries) == 1
        assert registries[0]["properties"]["adminUserEnabled"] is False

    def test_exactly_three_user_assigned_identities(
        self, foundation_template: dict[str, Any]
    ) -> None:
        identities = _resources_of_type(
            foundation_template, "Microsoft.ManagedIdentity/userAssignedIdentities"
        )
        names = {r["name"] for r in identities}
        assert len(names) == 3
        assert any("app-identity" in n for n in names)
        assert any("migration-identity" in n for n in names)
        assert any("operator-identity" in n for n in names)

    def test_key_vault_uses_rbac_authorization_never_access_policies(
        self, foundation_template: dict[str, Any]
    ) -> None:
        vaults = _resources_of_type(foundation_template, "Microsoft.KeyVault/vaults")
        assert vaults[0]["properties"]["enableRbacAuthorization"] is True

    def test_key_vault_soft_delete_and_purge_protection_enabled(
        self, foundation_template: dict[str, Any]
    ) -> None:
        vaults = _resources_of_type(foundation_template, "Microsoft.KeyVault/vaults")
        props = vaults[0]["properties"]
        assert props["enableSoftDelete"] is True
        assert props["enablePurgeProtection"] is True

    def test_container_app_uses_only_user_assigned_identity(
        self, app_template: dict[str, Any]
    ) -> None:
        apps = _resources_of_type(app_template, "Microsoft.App/containerApps")
        assert len(apps) == 1
        assert apps[0]["identity"]["type"] == "UserAssigned"

    def test_every_registry_pull_identity_has_a_registry_scoped_acr_pull_assignment(
        self, app_template: dict[str, Any], foundation_template: dict[str, Any]
    ) -> None:
        """Correction pass, item 2: every identity `container-app.bicep`/
        `jobs.bicep` actually configures under a Container App/Job's own
        `registries[].identity` must have a corresponding, registry-
        scoped `AcrPull` role assignment in `managed-identities.bicep` --
        otherwise that Container App/Job cannot pull its own image at
        runtime. Reproduced directly against the compiled ARM template
        before this fix: only `appIdentityId` had one; `operatorIdentityId`/
        `migrationIdentityId` (used by 4 of the 5 workloads this template
        defines) had none at all.

        Cross-checks the two separately-deployed templates
        (`app.bicep`'s own params can only be *parameter references*, not
        the underlying identity resources `foundation.bicep` owns) via
        this codebase's own fixed, documented parameter-name ->
        identity-resource-name-suffix convention (`appIdentityId` ->
        `*-app-identity`, etc.) -- the same convention every other
        `foundation.bicep` output/`app.bicep` param pairing in this
        infrastructure already relies on.
        """
        param_to_identity_suffix = {
            "appIdentityId": "app-identity",
            "operatorIdentityId": "operator-identity",
            "migrationIdentityId": "migration-identity",
        }

        # Every distinct identity *parameter* actually used under a
        # `registries[].identity` anywhere in the compiled app template.
        used_identity_params: set[str] = set()
        for resource in _iter_all_resources(app_template):
            registries = resource.get("properties", {}).get("configuration", {}).get("registries")
            if not registries:
                continue
            for registry in registries:
                identity_expr = registry.get("identity", "")
                for param_name in param_to_identity_suffix:
                    if f"parameters('{param_name}')" in identity_expr:
                        used_identity_params.add(param_name)
        assert used_identity_params == set(param_to_identity_suffix), (
            "expected every one of appIdentityId/operatorIdentityId/migrationIdentityId to be "
            f"used by at least one registries[] block; got: {used_identity_params}"
        )

        # Every identity-resource-name-suffix that has a registry-scoped
        # AcrPull assignment in the compiled foundation template.
        acr_pull_identity_suffixes: set[str] = set()
        for resource in _iter_all_resources(foundation_template):
            if resource.get("type") != "Microsoft.Authorization/roleAssignments":
                continue
            role_definition_id = resource.get("properties", {}).get("roleDefinitionId", "")
            scope = str(resource.get("scope", ""))
            if "acrPullRoleId" not in role_definition_id:
                continue
            assert "Microsoft.ContainerRegistry/registries" in scope, (
                f"an AcrPull assignment was not scoped to the registry: {resource.get('name')}"
            )
            principal_id_expr = str(resource.get("properties", {}).get("principalId", ""))
            for suffix in param_to_identity_suffix.values():
                if suffix in principal_id_expr:
                    acr_pull_identity_suffixes.add(suffix)

        required_suffixes = set(param_to_identity_suffix.values())
        missing = required_suffixes - acr_pull_identity_suffixes
        assert not missing, (
            f"identity suffix(es) used for image pull have no registry-scoped AcrPull "
            f"assignment: {missing}"
        )


class TestSkuConstraint:
    def test_acr_sku_is_basic(self, foundation_template: dict[str, Any]) -> None:
        registries = _resources_of_type(
            foundation_template, "Microsoft.ContainerRegistry/registries"
        )
        assert registries[0]["sku"]["name"] == "Basic"

    def test_storage_sku_is_standard_lrs_never_geo_redundant(
        self, foundation_template: dict[str, Any]
    ) -> None:
        accounts = _resources_of_type(foundation_template, "Microsoft.Storage/storageAccounts")
        sku_name = accounts[0]["sku"]["name"]
        assert sku_name == "Standard_LRS"
        assert "GRS" not in sku_name
        assert "RAGRS" not in sku_name

    def test_postgres_is_burstable_single_zone_no_geo_backup(
        self, foundation_template: dict[str, Any]
    ) -> None:
        servers = _resources_of_type(
            foundation_template, "Microsoft.DBforPostgreSQL/flexibleServers"
        )
        server = servers[0]
        assert server["sku"]["tier"] == "Burstable"
        assert server["properties"]["highAvailability"]["mode"] == "Disabled"
        assert server["properties"]["backup"]["geoRedundantBackup"] == "Disabled"

    def test_postgres_initial_storage_size_defaults_to_32gb(
        self, foundation_template: dict[str, Any]
    ) -> None:
        # storageSizeGB is a bicep parameter with a default -- resolve
        # the default from the enclosing module's own nested template,
        # the same pattern used for the location parameter above.
        for resource in _iter_all_resources(foundation_template):
            nested = resource.get("properties", {}).get("template")
            if isinstance(nested, dict) and "storageSizeGB" in nested.get("parameters", {}):
                assert _default_param(nested, "storageSizeGB") == 32
                return
        pytest.fail("no module parameterizes storageSizeGB")

    def test_postgres_storage_auto_grow_is_disabled(
        self, foundation_template: dict[str, Any]
    ) -> None:
        """Task 9: "Do not enable storage auto-growth without an explicit
        tested upper bound... leave auto-growth disabled and add storage
        alerts" -- this is the disabled half of that requirement;
        `TestCostConstraint.test_postgres_storage_alert_exists` below is
        the alert half.
        """
        servers = _resources_of_type(
            foundation_template, "Microsoft.DBforPostgreSQL/flexibleServers"
        )
        assert servers[0]["properties"]["storage"]["autoGrow"] == "Disabled"


class TestReplicaConstraint:
    def test_container_app_max_replicas_is_at_most_two(self, app_template: dict[str, Any]) -> None:
        apps = _resources_of_type(app_template, "Microsoft.App/containerApps")
        scale = apps[0]["properties"]["template"]["scale"]
        assert scale["minReplicas"] == 0
        assert scale["maxReplicas"] <= 2

    def test_container_app_forbidden_resource_types_are_absent(
        self, foundation_template: dict[str, Any], app_template: dict[str, Any]
    ) -> None:
        """Task 9: no AKS, Redis, Front Door, WAF, NAT Gateway, or
        premium ACR anywhere in this infrastructure.
        """
        forbidden_type_prefixes = (
            "Microsoft.ContainerService",  # AKS
            "Microsoft.Cache",  # Azure Cache for Redis
            "Microsoft.Network/frontDoors",
            "Microsoft.Cdn/profiles",  # Azure Front Door (CDN profile kind)
            "Microsoft.Network/applicationGateWays",
            "Microsoft.Network/natGateways",
        )
        all_types = {
            r.get("type", "")
            for r in list(_iter_all_resources(foundation_template))
            + list(_iter_all_resources(app_template))
        }
        for forbidden in forbidden_type_prefixes:
            assert not any(t.startswith(forbidden) for t in all_types), (
                f"forbidden resource type family present: {forbidden}"
            )

    def test_acr_is_never_premium(self, foundation_template: dict[str, Any]) -> None:
        registries = _resources_of_type(
            foundation_template, "Microsoft.ContainerRegistry/registries"
        )
        assert registries[0]["sku"]["name"] != "Premium"


class TestCostConstraint:
    def test_budget_amount_matches_approved_ceiling(
        self, foundation_template: dict[str, Any]
    ) -> None:
        budgets = _resources_of_type(foundation_template, "Microsoft.Consumption/budgets")
        assert len(budgets) == 1
        assert budgets[0]["properties"]["category"] == "Cost"
        # monthlyAmount is a bicep parameter with a default of 100 (the
        # approved CAD $100/month ceiling) -- resolve the default from
        # the enclosing module's own nested template.
        for resource in _iter_all_resources(foundation_template):
            nested = resource.get("properties", {}).get("template")
            if isinstance(nested, dict) and "monthlyAmount" in nested.get("parameters", {}):
                assert _default_param(nested, "monthlyAmount") == 100
                return
        pytest.fail("no module parameterizes monthlyAmount")

    def test_budget_start_date_has_no_fixed_default(
        self, foundation_template: dict[str, Any]
    ) -> None:
        """Correction-pass item 10: a fixed, checked-in `startDate`
        (the original `'2026-01-01'` default) eventually falls outside
        Azure's required "start date within the current Monthly time-
        grain period" window and fails deployment -- `budgetStartDate`
        must be a required parameter with **no** default anywhere in the
        compiled template, forcing the deploying workflow/operator to
        supply a freshly computed first-of-current-month value every
        time.
        """
        assert "budgetStartDate" in foundation_template.get("parameters", {})
        assert "defaultValue" not in foundation_template["parameters"]["budgetStartDate"]
        for resource in _iter_all_resources(foundation_template):
            nested = resource.get("properties", {}).get("template")
            if isinstance(nested, dict) and "startDate" in nested.get("parameters", {}):
                assert "defaultValue" not in nested["parameters"]["startDate"], (
                    "budget.bicep's startDate must never have a fixed, checked-in default"
                )

    def test_budget_has_forecasted_and_actual_notifications(
        self, foundation_template: dict[str, Any]
    ) -> None:
        budgets = _resources_of_type(foundation_template, "Microsoft.Consumption/budgets")
        notifications = budgets[0]["properties"]["notifications"]
        assert any(n["thresholdType"] == "Forecasted" for n in notifications.values())
        assert any(n["thresholdType"] == "Actual" for n in notifications.values())

    def test_postgres_storage_alert_exists(self, app_template: dict[str, Any]) -> None:
        alerts = _resources_of_type(app_template, "Microsoft.Insights/metricAlerts")
        storage_alerts = [
            a for a in alerts if a["properties"]["scopes"] == ["[parameters('postgresServerId')]"]
        ]
        assert len(storage_alerts) == 1
        criterion = storage_alerts[0]["properties"]["criteria"]["allOf"][0]
        assert criterion["metricName"] == "storage_percent"

    def test_postgres_backup_retention_is_seven_days(
        self, foundation_template: dict[str, Any]
    ) -> None:
        servers = _resources_of_type(
            foundation_template, "Microsoft.DBforPostgreSQL/flexibleServers"
        )
        assert servers[0]["properties"]["backup"]["backupRetentionDays"] == 7


class TestBicepParamFilesStayInSyncWithTheirTemplates:
    """Correction-pass item 11: "plan/deploy template or parameter
    drift" -- `bicep build-params` fails (BCP258/BCP259) the moment a
    `.bicepparam` file's parameter names fall out of sync with its
    `.bicep` template's own declared parameters, exactly the real,
    independently-reproduced defect this correction pass found in
    `app.bicepparam` (still using the pre-rewrite `jobIdentityId`/
    `jobIdentityClientId` names, and missing `blobAccountUrl`/
    `blobContainerName`/the split `operatorIdentity*`/`migrationIdentity*`
    parameters `app.bicep` was rewritten to require). `app.bicepparam` is
    never read by a real deployment (see its own top-of-file comment --
    `deploy-ingestion-azure.yml` generates an ephemeral parameter file
    instead), but it must still stay compilable: it is the only
    automatically-checked documentation of `app.bicep`'s real parameter
    surface, and `verify`'s own "Bicep parameter file compilation check"
    workflow step relies on exactly this property.
    """

    @pytest.mark.parametrize("bicepparam_file", ["foundation.bicepparam", "app.bicepparam"])
    def test_bicepparam_file_compiles_against_its_template(
        self, tmp_path: Path, bicepparam_file: str
    ) -> None:
        out_path = tmp_path / f"{bicepparam_file}.json"
        result = subprocess.run(
            [
                _BICEP_CLI,
                "build-params",
                str(INFRA_DIR / bicepparam_file),
                "--outfile",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, f"bicep build-params failed: {result.stderr}"


class TestPreTrafficMigrationGateSplit:
    """Correction-pass item 1: migration/compatibility must be
    established *before* the candidate image reaches anything
    traffic-facing OR schedule-driven -- the original workflow applied
    the entire `app.bicep` deployment (Container App included) in one
    `az deployment group create` call and only checked compatibility
    afterward, which is too late. `app.bicep` now takes a `deployTraffic`
    parameter (default `true`) so the workflow can deploy `jobs` (the
    `migrate` job only, whose own image reference updates) on its own
    first, run the compatibility gate against that now-updated job, and
    only then redeploy with `deployTraffic: true` to actually update the
    Container App -- never in the other order.

    **This pass's own narrowing**: a prior version of this split left
    the three *scheduled* operator jobs (`retention-sweep`/`purge-sweep`/
    `limiter-cleanup`) inside the always-on `jobs` module, so the very
    "jobs only" Stage 1 deployment this split exists to make safe would
    have silently repointed those cron-scheduled jobs at the untested
    candidate image too -- and, unlike the traffic-facing Container App,
    they can execute automatically before a human ever notices anything
    is wrong. `scheduledJobs` (`modules/scheduled-jobs.bicep`) is now a
    *separate* module, conditional on the same `deployTraffic` flag as
    `container-app`/`monitoring` -- `jobs` (`migrate` only) remains the
    only unconditional job deployment, since `migrate` is
    manual-trigger-only and can never run unattended regardless of
    deployment ordering.
    """

    def test_deploy_traffic_parameter_defaults_to_true(self, app_template: dict[str, Any]) -> None:
        assert _default_param(app_template, "deployTraffic") is True

    def test_container_app_and_monitoring_are_conditional_on_deploy_traffic(
        self, app_template: dict[str, Any]
    ) -> None:
        by_name = {r["name"]: r for r in app_template["resources"]}
        assert by_name["container-app"].get("condition") == "[parameters('deployTraffic')]"
        assert by_name["monitoring"].get("condition") == "[parameters('deployTraffic')]"

    def test_scheduled_jobs_module_is_conditional_on_deploy_traffic(
        self, app_template: dict[str, Any]
    ) -> None:
        by_name = {r["name"]: r for r in app_template["resources"]}
        assert by_name["scheduled-jobs"].get("condition") == "[parameters('deployTraffic')]"

    def test_jobs_module_is_never_conditional(self, app_template: dict[str, Any]) -> None:
        """The `jobs` deployment (`migrate` only) must always be applied
        regardless of `deployTraffic` -- it is what the pre-traffic gate
        itself depends on being updated first, and it is safe to update
        early because it is manual-trigger-only.
        """
        by_name = {r["name"]: r for r in app_template["resources"]}
        assert "condition" not in by_name["jobs"]

    def test_scheduled_operator_jobs_are_nested_only_under_the_conditional_module(
        self, app_template: dict[str, Any]
    ) -> None:
        """Structural cross-check, not just the module-level `condition`
        flag: the actual `Microsoft.App/jobs` resources for
        retention-sweep/purge-sweep/limiter-cleanup must be nested inside
        the `scheduled-jobs` module's own template, never inside the
        always-on `jobs` module -- and `migrate` must be the other way
        around.
        """
        by_name = {r["name"]: r for r in app_template["resources"]}
        jobs_nested_names = {
            r.get("name")
            for r in _iter_all_resources(by_name["jobs"]["properties"]["template"])
            if r.get("type") == "Microsoft.App/jobs"
        }
        scheduled_nested_names = {
            r.get("name")
            for r in _iter_all_resources(by_name["scheduled-jobs"]["properties"]["template"])
            if r.get("type") == "Microsoft.App/jobs"
        }
        assert any("migrate" in str(n) for n in jobs_nested_names)
        assert not any("retention-sweep" in str(n) for n in jobs_nested_names)
        assert not any("purge-sweep" in str(n) for n in jobs_nested_names)
        assert not any("limiter-cleanup" in str(n) for n in jobs_nested_names)
        assert any("retention-sweep" in str(n) for n in scheduled_nested_names)
        assert any("purge-sweep" in str(n) for n in scheduled_nested_names)
        assert any("limiter-cleanup" in str(n) for n in scheduled_nested_names)
        assert not any("migrate" in str(n) for n in scheduled_nested_names)


class TestNoUnexpectedGitHubWorkflowTrigger:
    """Not a Bicep test, but co-located here since it validates the same
    "no accidental production mutation" property from the infrastructure
    side: this repository's Bicep files are inert (compiled locally only)
    unless a human explicitly runs a deployment command against them --
    confirmed by grepping for the literal absence of any deployment
    command anywhere in the infra/azure/ tree itself.
    """

    def test_no_az_deployment_command_committed_anywhere_in_infra_tree(self) -> None:
        for path in INFRA_DIR.rglob("*"):
            if path.is_file() and path.suffix in (".bicep", ".bicepparam", ".json"):
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert "az deployment" not in text
                assert "az group deployment" not in text
