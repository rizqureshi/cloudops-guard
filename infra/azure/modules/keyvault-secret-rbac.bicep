// Phase 4G-A (corrected): per-secret Key Vault RBAC assignments only --
// correction pass, item 4, stage 3 of the explicit three-stage bootstrap
// `keyvault.bicep`'s own header comment describes.
//
// **This module contains no resource type capable of creating or
// writing a Key Vault secret value.** Every secret reference below is an
// `existing` resource -- Bicep's `existing` keyword never creates,
// updates, or deletes anything; it only lets a role assignment scope
// itself against a resource this deployment does not own. Deploying
// this module before an operator has created a given secret's real
// value out of band (`docs/deployment/azure-ingestion-production.md`
// §9) simply fails that one role assignment (the `existing` reference
// cannot resolve) -- it can never fall back to creating a placeholder,
// because there is no such resource declared here to fall back to.
//
// Idempotent and safe to redeploy indefinitely once granted: unlike a
// Key Vault secret *value* (versioned -- a differing resubmission always
// creates a new current version), a role assignment's own identity is
// its `name` (a deterministic `guid(...)`), so re-applying an
// already-granted assignment is a no-op, never a reversion risk.

@description('Key Vault name whose per-secret RBAC assignments this module manages.')
param keyVaultName string

@description('App identity principal ID (foundation.bicep output: appIdentityPrincipalId).')
param appIdentityPrincipalId string

@description('Operator identity principal ID (foundation.bicep output: operatorIdentityPrincipalId).')
param operatorIdentityPrincipalId string

@description('Migration identity principal ID (foundation.bicep output: migrationIdentityPrincipalId).')
param migrationIdentityPrincipalId string

var keyVaultSecretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: keyVaultName
}

var appSecretNames = [
  'runtime-metadata-db-conninfo'
  'runtime-token-db-conninfo'
  'runtime-limiter-db-conninfo'
  'limiter-hmac-key'
]

var operatorSecretNames = [
  'operator-metadata-db-conninfo'
  'operator-token-db-conninfo'
  'operator-limiter-db-conninfo'
  'limiter-hmac-key'
]

var migrationSecretNames = [
  'migration-metadata-db-conninfo'
]

resource appSecrets 'Microsoft.KeyVault/vaults/secrets@2024-11-01' existing = [
  for name in appSecretNames: {
    parent: keyVault
    name: name
  }
]

resource operatorSecrets 'Microsoft.KeyVault/vaults/secrets@2024-11-01' existing = [
  for name in operatorSecretNames: {
    parent: keyVault
    name: name
  }
]

resource migrationSecrets 'Microsoft.KeyVault/vaults/secrets@2024-11-01' existing = [
  for name in migrationSecretNames: {
    parent: keyVault
    name: name
  }
]

resource appSecretAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (name, i) in appSecretNames: {
    name: guid(keyVault.id, name, appIdentityPrincipalId, 'KeyVaultSecretsUser')
    scope: appSecrets[i]
    properties: {
      principalId: appIdentityPrincipalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: keyVaultSecretsUserRoleId
    }
  }
]

resource operatorSecretAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (name, i) in operatorSecretNames: {
    name: guid(keyVault.id, name, operatorIdentityPrincipalId, 'KeyVaultSecretsUser')
    scope: operatorSecrets[i]
    properties: {
      principalId: operatorIdentityPrincipalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: keyVaultSecretsUserRoleId
    }
  }
]

resource migrationSecretAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (name, i) in migrationSecretNames: {
    name: guid(keyVault.id, name, migrationIdentityPrincipalId, 'KeyVaultSecretsUser')
    scope: migrationSecrets[i]
    properties: {
      principalId: migrationIdentityPrincipalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: keyVaultSecretsUserRoleId
    }
  }
]
