// Phase 4G-A (corrected): the Container App itself -- external HTTPS
// ingress, TCP startup/liveness/readiness probes, minimum 0 / maximum 2
// replicas, digest-pinned image reference.
//
// **Correction-pass item 3**: `production_config.py` requires 15
// environment variables; the original version of this module supplied
// only 5, so the container would fail closed at startup every time.
// Every required variable is now either a Bicep parameter (the 10
// non-secret, pilot-scale-default limiter/retention values, plus the
// two blob-storage values) or a `secretRef` into a Key Vault secret (the
// three `runtime-*` database connection strings and the shared HMAC
// key) -- never a placeholder relying on a later manual
// `az containerapp update`, which a subsequent Bicep deployment could
// silently erase.
//
// **Correction-pass item 4**: `workloadProfileName` is now required --
// the environment this app deploys into is an explicit workload-profile
// (`v2`) environment (`container-apps-environment.bicep`), not the
// legacy consumption-only model.

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

@description('User-assigned managed identity resource ID the app authenticates as.')
param appIdentityId string

@description('User-assigned managed identity client ID (for ACR pull authentication and DefaultAzureCredential disambiguation).')
param appIdentityClientId string

@description('Full image reference, pinned by digest -- e.g. "myregistry.azurecr.io/cloudops-guard-ingestion-api@sha256:<64 hex characters>". Never a mutable tag.')
param imageDigest string

@description('Key Vault URI, for secret references.')
param keyVaultUri string

@description('Application port.')
param containerPort int = 8000

@description('Azure Blob Storage endpoint URL (COG_BLOB_ACCOUNT_URL) -- not a secret, foundation.bicep output.')
param blobAccountUrl string

@description('Report blob container name (COG_BLOB_CONTAINER_NAME) -- not a secret.')
param blobContainerName string

@description('Retention period in seconds (COG_RETENTION_PERIOD_SECONDS) -- Recorded human decision: 90 days.')
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

resource containerApp 'Microsoft.App/containerApps@2024-10-02-preview' = {
  name: '${namePrefix}-api'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${appIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: registryLoginServer
          identity: appIdentityId
        }
      ]
      ingress: {
        external: true
        targetPort: containerPort
        transport: 'auto' // HTTP/1.1 and HTTP/2, TLS termination at the platform ingress -- never plaintext to the customer.
        allowInsecure: false // HTTPS only -- plain HTTP is rejected/redirected, never served.
      }
      secrets: [
        {
          name: 'runtime-metadata-db-conninfo'
          keyVaultUrl: '${keyVaultUri}secrets/runtime-metadata-db-conninfo'
          identity: appIdentityId
        }
        {
          name: 'runtime-token-db-conninfo'
          keyVaultUrl: '${keyVaultUri}secrets/runtime-token-db-conninfo'
          identity: appIdentityId
        }
        {
          name: 'runtime-limiter-db-conninfo'
          keyVaultUrl: '${keyVaultUri}secrets/runtime-limiter-db-conninfo'
          identity: appIdentityId
        }
        {
          name: 'limiter-hmac-key'
          keyVaultUrl: '${keyVaultUri}secrets/limiter-hmac-key'
          identity: appIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'ingestion-api'
          image: imageDigest
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'COG_INGESTION_REGION', value: 'canadacentral' }
            { name: 'COG_METADATA_DB_CONNINFO', secretRef: 'runtime-metadata-db-conninfo' }
            { name: 'COG_TOKEN_DB_CONNINFO', secretRef: 'runtime-token-db-conninfo' }
            { name: 'COG_LIMITER_DB_CONNINFO', secretRef: 'runtime-limiter-db-conninfo' }
            { name: 'COG_LIMITER_HMAC_KEY', secretRef: 'limiter-hmac-key' }
            { name: 'COG_BLOB_ACCOUNT_URL', value: blobAccountUrl }
            { name: 'COG_BLOB_CONTAINER_NAME', value: blobContainerName }
            { name: 'COG_RETENTION_PERIOD_SECONDS', value: string(retentionPeriodSeconds) }
            { name: 'COG_LOOKUP_LIMITER_THRESHOLD', value: string(lookupLimiterThreshold) }
            { name: 'COG_LOOKUP_LIMITER_WINDOW_SECONDS', value: string(lookupLimiterWindowSeconds) }
            { name: 'COG_SOURCE_LIMITER_THRESHOLD', value: string(sourceLimiterThreshold) }
            { name: 'COG_SOURCE_LIMITER_WINDOW_SECONDS', value: string(sourceLimiterWindowSeconds) }
            { name: 'COG_TOKEN_RATE_LIMITER_THRESHOLD', value: string(tokenRateLimiterThreshold) }
            {
              name: 'COG_TOKEN_RATE_LIMITER_WINDOW_SECONDS'
              value: string(tokenRateLimiterWindowSeconds)
            }
            {
              name: 'COG_CAPABILITIES_RATE_LIMITER_THRESHOLD'
              value: string(capabilitiesRateLimiterThreshold)
            }
            {
              name: 'COG_CAPABILITIES_RATE_LIMITER_WINDOW_SECONDS'
              value: string(capabilitiesRateLimiterWindowSeconds)
            }
            { name: 'AZURE_CLIENT_ID', value: appIdentityClientId } // Disambiguates DefaultAzureCredential when multiple user-assigned identities exist.
          ]
          probes: [
            {
              type: 'Startup'
              tcpSocket: { port: containerPort }
              initialDelaySeconds: 5
              periodSeconds: 5
              failureThreshold: 12
            }
            {
              type: 'Liveness'
              tcpSocket: { port: containerPort }
              periodSeconds: 15
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              tcpSocket: { port: containerPort }
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2 // Recorded human decision: no more than two replicas in this pilot baseline.
        rules: [
          {
            name: 'http-scale-rule'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

output containerAppId string = containerApp.id
output fqdn string = containerApp.properties.configuration.ingress.fqdn
