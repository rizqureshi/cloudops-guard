// Phase 4G-A (corrected): the three *scheduled* operator Container Apps
// Jobs -- `retention-sweep`, `purge-sweep`, `limiter-cleanup`. Split out
// of `jobs.bicep` (correction pass, item 1): `app.bicep` deploys this
// module only as part of its `deployTraffic`-gated group, alongside the
// traffic-facing Container App and monitoring alerts -- i.e., only once
// the pre-traffic migration-compatibility gate (which updates and
// checks `jobs.bicep`'s own `migrate` job first) has already passed.
// Unlike `migrate`, these three jobs run **automatically** on a cron
// schedule, so it is specifically these jobs -- not the traffic-facing
// Container App -- whose candidate-image update this split most needs
// to defer: an untested candidate image reachable only by manual HTTP
// traffic is comparatively low-risk before the gate passes, but the
// same image silently running an hourly retention/purge sweep against
// production data before compatibility is confirmed is not.
//
// `retention-sweep` and `purge-sweep` run under the **operator**
// identity (never the app's own runtime identity), with the full
// 15-variable configuration `retention_sweep_command`/`purge_sweep_
// command` genuinely need (both build a complete `IngestionApiConfig`).
// `limiter-cleanup` runs under the operator identity too, but with only
// the 3 variables `ops_cli.py limiter-cleanup`'s own narrow loader
// (`load_limiter_cleanup_settings_from_environment`) requires -- never
// the full 15. `purge-sweep` runs the real, bounded, concurrency-safe
// purge sweep (`ops_cli.py purge-sweep`) -- never the `migration-status`
// placeholder an earlier version of this infrastructure ran.

@description('Azure region. Must be canadacentral.')
param location string = 'canadacentral'

@description('Resource name prefix.')
param namePrefix string

@description('Tags applied to every resource this module creates.')
param tags object

@description('Container Apps environment resource ID.')
param environmentId string

@description('Container Registry login server.')
param registryLoginServer string

@description('Operator identity resource ID -- retention-sweep/purge-sweep/limiter-cleanup.')
param operatorIdentityId string

@description('Operator identity client ID.')
param operatorIdentityClientId string

@description('Full image reference, pinned by digest.')
param imageDigest string

@description('Key Vault URI, for secret references.')
param keyVaultUri string

@description('Azure Blob Storage endpoint URL.')
param blobAccountUrl string

@description('Report blob container name.')
param blobContainerName string

@description('Retention period in seconds.')
param retentionPeriodSeconds int

@description('Layer 1 (per-lookup_id) attempt-limiter threshold.')
param lookupLimiterThreshold int

@description('Layer 1 (per-lookup_id) attempt-limiter window, in seconds.')
param lookupLimiterWindowSeconds int

@description('Layer 2 (per-source) attempt-limiter threshold.')
param sourceLimiterThreshold int

@description('Layer 2 (per-source) attempt-limiter window, in seconds.')
param sourceLimiterWindowSeconds int

@description('Layer 3 (per-authenticated-token) request-rate threshold.')
param tokenRateLimiterThreshold int

@description('Layer 3 (per-authenticated-token) request-rate window, in seconds.')
param tokenRateLimiterWindowSeconds int

@description('Capabilities-endpoint (per-source) request-rate threshold.')
param capabilitiesRateLimiterThreshold int

@description('Capabilities-endpoint (per-source) request-rate window, in seconds.')
param capabilitiesRateLimiterWindowSeconds int

var operatorFullConfigEnv = [
  { name: 'COG_INGESTION_REGION', value: 'canadacentral' }
  { name: 'COG_METADATA_DB_CONNINFO', secretRef: 'operator-metadata-db-conninfo' }
  { name: 'COG_TOKEN_DB_CONNINFO', secretRef: 'operator-token-db-conninfo' }
  { name: 'COG_LIMITER_DB_CONNINFO', secretRef: 'operator-limiter-db-conninfo' }
  { name: 'COG_LIMITER_HMAC_KEY', secretRef: 'limiter-hmac-key' }
  { name: 'COG_BLOB_ACCOUNT_URL', value: blobAccountUrl }
  { name: 'COG_BLOB_CONTAINER_NAME', value: blobContainerName }
  { name: 'COG_RETENTION_PERIOD_SECONDS', value: string(retentionPeriodSeconds) }
  { name: 'COG_LOOKUP_LIMITER_THRESHOLD', value: string(lookupLimiterThreshold) }
  { name: 'COG_LOOKUP_LIMITER_WINDOW_SECONDS', value: string(lookupLimiterWindowSeconds) }
  { name: 'COG_SOURCE_LIMITER_THRESHOLD', value: string(sourceLimiterThreshold) }
  { name: 'COG_SOURCE_LIMITER_WINDOW_SECONDS', value: string(sourceLimiterWindowSeconds) }
  { name: 'COG_TOKEN_RATE_LIMITER_THRESHOLD', value: string(tokenRateLimiterThreshold) }
  { name: 'COG_TOKEN_RATE_LIMITER_WINDOW_SECONDS', value: string(tokenRateLimiterWindowSeconds) }
  {
    name: 'COG_CAPABILITIES_RATE_LIMITER_THRESHOLD'
    value: string(capabilitiesRateLimiterThreshold)
  }
  {
    name: 'COG_CAPABILITIES_RATE_LIMITER_WINDOW_SECONDS'
    value: string(capabilitiesRateLimiterWindowSeconds)
  }
  { name: 'AZURE_CLIENT_ID', value: operatorIdentityClientId }
]

var operatorFullConfigSecrets = [
  {
    name: 'operator-metadata-db-conninfo'
    keyVaultUrl: '${keyVaultUri}secrets/operator-metadata-db-conninfo'
    identity: operatorIdentityId
  }
  {
    name: 'operator-token-db-conninfo'
    keyVaultUrl: '${keyVaultUri}secrets/operator-token-db-conninfo'
    identity: operatorIdentityId
  }
  {
    name: 'operator-limiter-db-conninfo'
    keyVaultUrl: '${keyVaultUri}secrets/operator-limiter-db-conninfo'
    identity: operatorIdentityId
  }
  {
    name: 'limiter-hmac-key'
    keyVaultUrl: '${keyVaultUri}secrets/limiter-hmac-key'
    identity: operatorIdentityId
  }
]

resource retentionSweepJob 'Microsoft.App/jobs@2024-10-02-preview' = {
  name: '${namePrefix}-retention-sweep'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${operatorIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '0 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 900
      replicaRetryLimit: 1
      registries: [
        {
          server: registryLoginServer
          identity: operatorIdentityId
        }
      ]
      secrets: operatorFullConfigSecrets
    }
    template: {
      containers: [
        {
          name: 'retention-sweep'
          image: imageDigest
          command: ['python', '-m', 'cloudops_guard.ingestion_azure.ops_cli', 'retention-sweep']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: operatorFullConfigEnv
        }
      ]
    }
  }
}

resource purgeSweepJob 'Microsoft.App/jobs@2024-10-02-preview' = {
  name: '${namePrefix}-purge-sweep'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${operatorIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '30 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 900
      replicaRetryLimit: 1
      registries: [
        {
          server: registryLoginServer
          identity: operatorIdentityId
        }
      ]
      secrets: operatorFullConfigSecrets
    }
    template: {
      containers: [
        {
          name: 'purge-sweep'
          image: imageDigest
          command: ['python', '-m', 'cloudops_guard.ingestion_azure.ops_cli', 'purge-sweep']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: operatorFullConfigEnv
        }
      ]
    }
  }
}

resource limiterCleanupJob 'Microsoft.App/jobs@2024-10-02-preview' = {
  name: '${namePrefix}-limiter-cleanup'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${operatorIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '*/15 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 900
      replicaRetryLimit: 1
      registries: [
        {
          server: registryLoginServer
          identity: operatorIdentityId
        }
      ]
      secrets: [
        {
          name: 'operator-limiter-db-conninfo'
          keyVaultUrl: '${keyVaultUri}secrets/operator-limiter-db-conninfo'
          identity: operatorIdentityId
        }
        {
          name: 'limiter-hmac-key'
          keyVaultUrl: '${keyVaultUri}secrets/limiter-hmac-key'
          identity: operatorIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'limiter-cleanup'
          image: imageDigest
          command: ['python', '-m', 'cloudops_guard.ingestion_azure.ops_cli', 'limiter-cleanup']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'COG_INGESTION_REGION', value: 'canadacentral' }
            { name: 'COG_LIMITER_DB_CONNINFO', secretRef: 'operator-limiter-db-conninfo' }
            { name: 'COG_LIMITER_HMAC_KEY', secretRef: 'limiter-hmac-key' }
            { name: 'AZURE_CLIENT_ID', value: operatorIdentityClientId }
          ]
        }
      ]
    }
  }
}

output retentionSweepJobId string = retentionSweepJob.id
output purgeSweepJobId string = purgeSweepJob.id
output limiterCleanupJobId string = limiterCleanupJob.id
