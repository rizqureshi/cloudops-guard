// Phase 4G-A: monitoring alerts (task 9). Two specifically required by
// this pass's own storage-auto-grow decision (PostgreSQL storage percent
// used, since auto-grow is deliberately disabled) plus basic application
// health signals. Notifies a single action group; the actual recipient
// (an email address or Azure Monitor action) is a placeholder-only
// parameter, filled in with a real, non-repository-committed value at
// Phase 4G-B deployment time.

@description('Resource name prefix.')
param namePrefix string

@description('Tags applied to every resource this module creates.')
param tags object

@description('PostgreSQL Flexible Server resource ID.')
param postgresServerId string

@description('Container App resource ID.')
param containerAppId string

@description('Placeholder-only: the operational alert recipient email address, set at Phase 4G-B deployment time.')
param alertEmailAddress string = 'placeholder-oncall@example.invalid'

resource actionGroup 'Microsoft.Insights/actionGroups@2024-10-01-preview' = {
  name: '${namePrefix}-oncall'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'cogIngest'
    enabled: true
    emailReceivers: [
      {
        name: 'oncall'
        emailAddress: alertEmailAddress
        useCommonAlertSchema: true
      }
    ]
  }
}

resource postgresStorageAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-postgres-storage-percent'
  location: 'global'
  tags: tags
  properties: {
    severity: 2
    enabled: true
    scopes: [postgresServerId]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT30M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'StoragePercentHigh'
          metricName: 'storage_percent'
          operator: 'GreaterThan'
          threshold: 80
          timeAggregation: 'Average'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource containerAppRestartAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-container-app-restarts'
  location: 'global'
  tags: tags
  properties: {
    severity: 2
    enabled: true
    scopes: [containerAppId]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'RestartCountHigh'
          metricName: 'RestartCount'
          operator: 'GreaterThan'
          threshold: 3
          timeAggregation: 'Total'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

output actionGroupId string = actionGroup.id
