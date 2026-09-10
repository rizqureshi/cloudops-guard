// Phase 4G-A: Azure Container Registry, Basic SKU, admin account
// disabled (task 4/9's explicit requirement -- runtime and CI both
// authenticate via managed identity/OIDC federation, never a shared
// admin username/password).

@description('Azure region. Must be canadacentral.')
param location string = 'canadacentral'

@description('Globally-unique registry name (alphanumeric only, 5-50 characters).')
param registryName string

@description('Tags applied to every resource this module creates.')
param tags object

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled' // Basic SKU has no private-endpoint support; ACR pulls are authenticated via managed identity regardless of network path.
  }
}

output registryId string = registry.id
output registryName string = registry.name
output loginServer string = registry.properties.loginServer
