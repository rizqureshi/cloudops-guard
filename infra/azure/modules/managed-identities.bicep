// Phase 4G-A (corrected): user-assigned managed identities and their
// AcrPull/Blob role assignments -- every role assignment scopes to the
// *specific target resource* (existing-resource symbolic references),
// never `scope: resourceGroup()` -- AcrPull to the registry itself, blob
// access to the report container specifically.
//
// **Per-secret Key Vault RBAC lives in `keyvault-secret-rbac.bicep`, a
// separate module** (correction pass, item 4): granting a role against a
// specific secret requires that secret to already exist, and this
// project's Key Vault secrets are deliberately never created by any
// Bicep resource at all (an operator creates each real value out of
// band) -- keeping that RBAC in a module of its own, deployed only once
// an operator confirms the real secrets exist
// (`foundation.bicep`'s `assignKeyVaultSecretRbac` parameter), means
// this module (identities + AcrPull + Blob access) can be deployed on
// day one, before any secret exists, with nothing here ever depending on
// Key Vault content.
//
// Three separate identities:
// - `appIdentity`: the running Container App. Reads only the
//   `runtime-*` database secrets and the shared `limiter-hmac-key`.
// - `migrationIdentity`: the schema-migration Container Apps Job. Reads
//   only `migration-metadata-db-conninfo` -- never the runtime or
//   operator secrets, and never the HMAC key (the migration job never
//   touches a limiter).
// - `operatorIdentity`: the retention-sweep/purge-sweep/limiter-cleanup/
//   tenant-inventory/offboarding Container Apps Jobs. Reads only the
//   `operator-*` database secrets and the shared `limiter-hmac-key`.

@description('Azure region. Must be canadacentral.')
param location string = 'canadacentral'

@description('Resource name prefix.')
param namePrefix string

@description('Tags applied to every resource this module creates.')
param tags object

@description('Container Registry name, for the AcrPull role assignment scoped to this exact registry.')
param containerRegistryName string

@description('Storage account name, for the Storage Blob Data Contributor role assignment scoped to the report container.')
param storageAccountName string

@description('Report blob container name.')
param reportContainerName string

var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var storageBlobDataContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: '${namePrefix}-app-identity'
  location: location
  tags: tags
}

resource migrationIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: '${namePrefix}-migration-identity'
  location: location
  tags: tags
}

resource operatorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: '${namePrefix}-operator-identity'
  location: location
  tags: tags
}

// -- Existing-resource references, for correctly-scoped role assignments --

resource acr 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = {
  name: containerRegistryName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2024-01-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' existing = {
  parent: storageAccount
  name: 'default'
}

resource reportContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' existing = {
  parent: blobService
  name: reportContainerName
}

// -- AcrPull, scoped to the registry itself (never the resource group) --
//
// **Correction pass, item 2**: every identity that `container-app.bicep`/
// `jobs.bicep` actually configures under a Container App/Job's own
// `registries[].identity` needs AcrPull on this registry to pull its
// image at all -- that is `appIdentity` (the Container App),
// `operatorIdentity` (retention-sweep/purge-sweep/limiter-cleanup), and
// `migrationIdentity` (the migrate job). The original version of this
// file granted it only to `appIdentity`; reproduced directly against the
// compiled ARM template before this fix (exactly one `AcrPull`
// assignment existed, for `app-identity` only) -- every operator/
// migration Container Apps Job would have failed to pull its image at
// runtime.

resource appAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, appIdentity.id, 'AcrPull')
  scope: acr
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource operatorAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, operatorIdentity.id, 'AcrPull')
  scope: acr
  properties: {
    principalId: operatorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource migrationAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, migrationIdentity.id, 'AcrPull')
  scope: acr
  properties: {
    principalId: migrationIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

// -- Blob access, scoped to the report container specifically --

resource appBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(reportContainer.id, appIdentity.id, 'StorageBlobDataContributor')
  scope: reportContainer
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataContributorRoleId
  }
}

// The purge-sweep operator identity independently needs the same
// container-scoped blob-delete-capable role -- Storage Blob Data
// Contributor includes delete; no narrower built-in role exists for
// "delete but not overwrite" (Azure RBAC has no such distinction for
// blobs), so this is the least-privilege built-in role available,
// scoped as narrowly as Azure RBAC permits (the container, never the
// account or resource group).
resource operatorBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(reportContainer.id, operatorIdentity.id, 'StorageBlobDataContributor')
  scope: reportContainer
  properties: {
    principalId: operatorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataContributorRoleId
  }
}

output appIdentityId string = appIdentity.id
output appIdentityPrincipalId string = appIdentity.properties.principalId
output appIdentityClientId string = appIdentity.properties.clientId
output migrationIdentityId string = migrationIdentity.id
output migrationIdentityPrincipalId string = migrationIdentity.properties.principalId
output migrationIdentityClientId string = migrationIdentity.properties.clientId
output operatorIdentityId string = operatorIdentity.id
output operatorIdentityPrincipalId string = operatorIdentity.properties.principalId
output operatorIdentityClientId string = operatorIdentity.properties.clientId
