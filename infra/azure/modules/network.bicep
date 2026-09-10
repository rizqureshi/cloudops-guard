// Phase 4G-A: virtual network with two delegated subnets -- one for the
// Container Apps environment's own VNet integration, one for PostgreSQL
// Flexible Server's private-access delegation. No public IP is
// provisioned by this module; the Container Apps environment's own
// public ingress IP (if any) is managed by the platform, not this
// network definition.

@description('Azure region. Must be canadacentral -- enforced by the caller (main.bicep), restated here as a parameter default only.')
param location string = 'canadacentral'

@description('Resource name prefix, e.g. cog-ingestion-pilot.')
param namePrefix string

@description('Tags applied to every resource this module creates.')
param tags object

@description('VNet address space.')
param vnetAddressPrefix string = '10.20.0.0/16'

@description('Subnet delegated to the Container Apps environment.')
param containerAppsSubnetPrefix string = '10.20.0.0/23'

@description('Subnet delegated to PostgreSQL Flexible Server private access.')
param postgresSubnetPrefix string = '10.20.2.0/24'

@description('Subnet hosting private endpoints (Blob Storage, Key Vault) -- never delegated to any service, since a delegated subnet cannot also host a private endpoint.')
param privateEndpointsSubnetPrefix string = '10.20.3.0/24'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${namePrefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: 'container-apps'
        properties: {
          addressPrefix: containerAppsSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'postgres'
        properties: {
          addressPrefix: postgresSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.DBforPostgreSQL.flexibleServers'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: privateEndpointsSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output containerAppsSubnetId string = vnet.properties.subnets[0].id
output postgresSubnetId string = vnet.properties.subnets[1].id
output privateEndpointsSubnetId string = vnet.properties.subnets[2].id
