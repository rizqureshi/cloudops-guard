// Phase 4G-A: Log Analytics workspace backing the Container Apps
// environment's own diagnostic/log pipeline. No alert rules live here --
// see monitoring.bicep.

@description('Azure region. Must be canadacentral.')
param location string = 'canadacentral'

@description('Resource name prefix.')
param namePrefix string

@description('Tags applied to every resource this module creates.')
param tags object

@description('Log retention in days -- pilot-scale default.')
param retentionInDays int = 30

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
  }
}

output workspaceId string = logAnalytics.id
output workspaceName string = logAnalytics.name
output customerId string = logAnalytics.properties.customerId
