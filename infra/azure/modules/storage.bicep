// Phase 4G-A: StorageV2, Standard LRS, private report container -- no
// geo-redundant storage (task 9's explicit "no geo-redundant storage"
// constraint), public access disabled, private-endpoint-only network
// path. No storage-account key is ever read by application code
// (`ingestion_azure.blob_store` authenticates via managed identity only)
// -- key-based access is also disabled at the resource level here,
// defense in depth.

@description('Azure region. Must be canadacentral.')
param location string = 'canadacentral'

@description('Globally-unique storage account name (lowercase alphanumeric, 3-24 characters).')
param storageAccountName string

@description('Tags applied to every resource this module creates.')
param tags object

@description('Blob container name for report bytes.')
param reportContainerName string = 'reports'

@description('VNet resource ID, for the private-DNS-zone virtual network link.')
param vnetId string

@description('Subnet resource ID hosting the private endpoint.')
param privateEndpointSubnetId string

resource storageAccount 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS' // Explicitly LRS, never geo-redundant (task 9).
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false // Managed-identity/Azure-AD authentication only -- no storage-account key is ever valid for this account.
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
    }
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource reportContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobService
  name: reportContainerName
  properties: {
    publicAccess: 'None' // Anonymous access prohibited (task 9's explicit requirement).
  }
}

resource blobPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource blobPrivateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: blobPrivateDnsZone
  name: '${storageAccountName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

resource blobPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${storageAccountName}-blob-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${storageAccountName}-blob-connection'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource blobPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: blobPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob'
        properties: {
          privateDnsZoneId: blobPrivateDnsZone.id
        }
      }
    ]
  }
}

output storageAccountId string = storageAccount.id
output storageAccountName string = storageAccount.name
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
output reportContainerName string = reportContainer.name
