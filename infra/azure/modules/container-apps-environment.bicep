// Phase 4G-A (corrected): the Azure Container Apps environment itself --
// VNet-integrated (into the delegated subnet from network.bicep), wired
// to the Log Analytics workspace for diagnostics.
//
// **Correction-pass item 4**: a delegated subnet is the *workload-
// profile* environment requirement, not the legacy consumption-only
// one (Microsoft's own documentation: consumption-only environments
// require an undelegated /23 subnet; workload-profile environments
// require delegation and permit smaller subnets). The original version
// of this module omitted `workloadProfiles` entirely, which defaults an
// environment to the legacy consumption-only model -- contradicting the
// delegated subnet `network.bicep` already provisions. Fixed by
// declaring this an explicit workload-profile (`v2`) environment with
// its own built-in `Consumption` workload profile, matching the
// delegated subnet it actually uses.

@description('Azure region. Must be canadacentral.')
param location string = 'canadacentral'

@description('Resource name prefix.')
param namePrefix string

@description('Tags applied to every resource this module creates.')
param tags object

@description('Subnet resource ID delegated to Microsoft.App/environments.')
param delegatedSubnetId string

@description('Log Analytics workspace customer ID.')
param logAnalyticsCustomerId string

@description('Log Analytics workspace name (plain resource name, not a secret) -- used only to resolve an `existing` reference in this module so its shared key can be read via `.listKeys()` directly against the real resource, never passed as a module output/parameter (task 9: "Outputs must never expose credentials or secret values").')
param logAnalyticsWorkspaceName string

resource existingLogAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource environment 'Microsoft.App/managedEnvironments@2024-10-02-preview' = {
  name: '${namePrefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: existingLogAnalytics.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: delegatedSubnetId
    }
    zoneRedundant: false // Single-zone, pilot-scale (Recorded human decisions) -- no cross-region/cross-zone failover.
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

output environmentId string = environment.id
output defaultDomain string = environment.properties.defaultDomain
