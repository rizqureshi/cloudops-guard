// Phase 4G-A (corrected): Key Vault -- RBAC authorization model, soft-
// delete and purge protection both enabled and never configurable off,
// **and a private endpoint** (`publicNetworkAccess: 'Disabled'` +
// `bypass: 'None'` with no private endpoint would make the vault
// unreachable by anything, which Azure's own documentation states is the
// effect of this exact combination -- private endpoints are the only
// path in).
//
// **This module creates no Key Vault *secret* at all** (correction pass,
// item 4 -- a full removal, not merely a conditional one). An earlier
// version of this module declared eight placeholder secret *objects*,
// gated behind a `createPlaceholderSecrets` parameter that defaulted to
// `false` after the very first bootstrap deployment. That conditional
// gate closed the specific "a later routine deployment silently reverts
// a real secret back to its placeholder value" bug (ARM Key Vault
// secrets are versioned: submitting a *different* value than a secret's
// current version always creates a new, current version), but it still
// left a real, deployable Bicep resource capable of writing the literal
// string `REPLACE-AT-DEPLOYMENT-TIME-NEVER-COMMITTED` into a production
// secret under the right (mistaken) parameter value -- a structural
// possibility this pass's own required test
// (`TestNetworkConstraint::test_no_deployable_resource_ever_creates_a_key_vault_secret`)
// exists to make permanently impossible, not merely improbable.
//
// **The fix -- three explicit bootstrap stages, never a fourth "trust
// the flag" stage**:
//   1. This deployment (`foundation.bicep`, this module) creates the
//      vault and every identity, with no secret object and no per-secret
//      role assignment requiring one to exist.
//   2. An authorized operator creates all eight real secret values out
//      of band (`az keyvault secret set`, documented in
//      `docs/deployment/azure-ingestion-production.md` §9) -- Bicep
//      never sees, writes, or is capable of writing any of these values.
//   3. `foundation.bicep` is deployed again with
//      `assignKeyVaultSecretRbac: true`, which brings in
//      `keyvault-secret-rbac.bicep` (an `existing`-reference-only
//      module: it can grant RBAC against a secret that already exists,
//      but has no resource type in it capable of creating or writing
//      one) to grant each identity read access to exactly its own
//      secrets. Re-running this stage is safe indefinitely: role
//      assignments are not versioned the way secret values are, so
//      re-applying it is a no-op once granted.
//
// Three distinct database-role secret families (`runtime-*`/
// `operator-*`/`migration-*`) exist so the running application, the
// operator CLI, and the schema-migration job each authenticate as a
// distinct PostgreSQL role with distinct privileges -- the migration
// role's own credential is the one that can never be readable by the app
// or operator identities, which is exactly what per-secret scoping
// (rather than vault-wide access) exists to prevent.

@description('Azure region. Must be canadacentral.')
param location string = 'canadacentral'

@description('Globally-unique Key Vault name (3-24 characters).')
param keyVaultName string

@description('Tags applied to every resource this module creates.')
param tags object

@description('Azure AD tenant ID.')
param tenantId string

@description('VNet resource ID, for the private-DNS-zone virtual network link.')
param vnetId string

@description('Subnet resource ID hosting the private endpoint.')
param privateEndpointSubnetId string

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenantId
    enableRbacAuthorization: true // RBAC, never the legacy access-policy model.
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true // Never configurable to false.
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
    }
  }
}

// -- Private networking: the only reachable path once publicNetworkAccess is Disabled --

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
  tags: tags
}

resource privateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: privateDnsZone
  name: '${keyVaultName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

resource keyVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${keyVaultName}-kv-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${keyVaultName}-kv-connection'
        properties: {
          privateLinkServiceId: keyVault.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource keyVaultPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: keyVaultPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output privateEndpointId string = keyVaultPrivateEndpoint.id
