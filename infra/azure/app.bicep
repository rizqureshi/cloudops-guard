// Phase 4G-A (corrected): application deployment -- the Container App
// itself, scheduled Container Apps Jobs, and monitoring alerts. Deployed
// **second**, after `foundation.bicep`, and only once a real container
// image has been built from a reviewed commit and pushed to the
// registry `foundation.bicep` created -- `imageDigest` has no default
// and must be supplied explicitly.
//
// **This template creates no resource by itself in this pass.**

targetScope = 'resourceGroup'

@description('Azure region. Hard-coded to canadacentral.')
var location = 'canadacentral'

@description('Resource name prefix -- must match the value passed to foundation.bicep.')
param namePrefix string

@description('Container Apps environment resource ID (foundation.bicep output: environmentId).')
param environmentId string

@description('Container Registry login server (foundation.bicep output: registryLoginServer).')
param registryLoginServer string

@description('App identity resource ID (foundation.bicep output: appIdentityId).')
param appIdentityId string

@description('App identity client ID (foundation.bicep output: appIdentityClientId).')
param appIdentityClientId string

@description('Operator identity resource ID -- runs retention-sweep/purge-sweep/limiter-cleanup jobs (foundation.bicep output: operatorIdentityId).')
param operatorIdentityId string

@description('Operator identity client ID (foundation.bicep output: operatorIdentityClientId).')
param operatorIdentityClientId string

@description('Migration identity resource ID -- runs the schema-migration job only (foundation.bicep output: migrationIdentityId).')
param migrationIdentityId string

@description('Migration identity client ID (foundation.bicep output: migrationIdentityClientId).')
param migrationIdentityClientId string

@description('Key Vault URI (foundation.bicep output: keyVaultUri).')
param keyVaultUri string

@description('Full image reference, pinned by digest. No default: must be supplied explicitly, and must never be a mutable tag.')
param imageDigest string

@description('PostgreSQL Flexible Server resource ID, for the storage-percent monitoring alert (foundation.bicep output: postgresServerId).')
param postgresServerId string

@description('Azure Blob Storage endpoint URL (foundation.bicep output: blobEndpoint).')
param blobAccountUrl string

@description('Report blob container name (foundation.bicep output: reportContainerName).')
param blobContainerName string

@description('Placeholder-only: the operational alert recipient email address.')
param alertEmailAddress string = 'placeholder-oncall@example.invalid'

@description('Correction pass, item 1 (this pass -- narrowed from the prior "jobs only" design): whether to deploy the traffic-facing Container App, monitoring alert, and the three *scheduled* operator jobs (retention-sweep/purge-sweep/limiter-cleanup). False for the pre-migration-gate deployment the workflow applies first, which updates *only* the manual, read-only `jobs` module (the `migrate` job) so its own migration-status output can be validated before anything traffic-facing OR schedule-driven runs against the candidate image -- true (the default) for the full deployment, applied only once that gate has passed. `jobs` (the `migrate` job) is always deployed regardless of this flag -- it is manual-trigger-only, never scheduled, so updating its image early is exactly what makes the pre-traffic gate possible. `scheduledJobs` (retention-sweep/purge-sweep/limiter-cleanup) runs automatically on a cron schedule and therefore must **not** be updated to the candidate image until this flag is true -- an untested candidate silently running an hourly retention/purge sweep against production data before compatibility is confirmed is exactly the risk this split closes.')
param deployTraffic bool = true

var tags = {
  project: 'cloudops-guard-ingestion-api'
  environment: 'pilot'
  dataResidency: 'canada-central'
  dataClassification: 'customer-report-metadata'
  managedBy: 'bicep-infra-azure'
  phase: '4g-a-application'
}

// Pilot-scale defaults -- must match `production_config.py`'s own
// DEFAULT_* constants exactly (`tests/ingestion_azure/
// test_bicep_infrastructure.py::TestConfigCompleteness` cross-checks
// this). Retention: 90 days (Recorded human decision, §C's default).
var retentionPeriodSeconds = 7776000
var lookupLimiterThreshold = 10
var lookupLimiterWindowSeconds = 900
var sourceLimiterThreshold = 30
var sourceLimiterWindowSeconds = 300
var tokenRateLimiterThreshold = 60
var tokenRateLimiterWindowSeconds = 60
var capabilitiesRateLimiterThreshold = 60
var capabilitiesRateLimiterWindowSeconds = 60

module containerApp 'modules/container-app.bicep' = if (deployTraffic) {
  name: 'container-app'
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
    environmentId: environmentId
    registryLoginServer: registryLoginServer
    appIdentityId: appIdentityId
    appIdentityClientId: appIdentityClientId
    imageDigest: imageDigest
    keyVaultUri: keyVaultUri
    blobAccountUrl: blobAccountUrl
    blobContainerName: blobContainerName
    retentionPeriodSeconds: retentionPeriodSeconds
    lookupLimiterThreshold: lookupLimiterThreshold
    lookupLimiterWindowSeconds: lookupLimiterWindowSeconds
    sourceLimiterThreshold: sourceLimiterThreshold
    sourceLimiterWindowSeconds: sourceLimiterWindowSeconds
    tokenRateLimiterThreshold: tokenRateLimiterThreshold
    tokenRateLimiterWindowSeconds: tokenRateLimiterWindowSeconds
    capabilitiesRateLimiterThreshold: capabilitiesRateLimiterThreshold
    capabilitiesRateLimiterWindowSeconds: capabilitiesRateLimiterWindowSeconds
  }
}

module jobs 'modules/jobs.bicep' = {
  name: 'jobs'
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
    environmentId: environmentId
    registryLoginServer: registryLoginServer
    migrationIdentityId: migrationIdentityId
    migrationIdentityClientId: migrationIdentityClientId
    imageDigest: imageDigest
    keyVaultUri: keyVaultUri
  }
}

module scheduledJobs 'modules/scheduled-jobs.bicep' = if (deployTraffic) {
  name: 'scheduled-jobs'
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
    environmentId: environmentId
    registryLoginServer: registryLoginServer
    operatorIdentityId: operatorIdentityId
    operatorIdentityClientId: operatorIdentityClientId
    imageDigest: imageDigest
    keyVaultUri: keyVaultUri
    blobAccountUrl: blobAccountUrl
    blobContainerName: blobContainerName
    retentionPeriodSeconds: retentionPeriodSeconds
    lookupLimiterThreshold: lookupLimiterThreshold
    lookupLimiterWindowSeconds: lookupLimiterWindowSeconds
    sourceLimiterThreshold: sourceLimiterThreshold
    sourceLimiterWindowSeconds: sourceLimiterWindowSeconds
    tokenRateLimiterThreshold: tokenRateLimiterThreshold
    tokenRateLimiterWindowSeconds: tokenRateLimiterWindowSeconds
    capabilitiesRateLimiterThreshold: capabilitiesRateLimiterThreshold
    capabilitiesRateLimiterWindowSeconds: capabilitiesRateLimiterWindowSeconds
  }
}

module monitoring 'modules/monitoring.bicep' = if (deployTraffic) {
  name: 'monitoring'
  params: {
    namePrefix: namePrefix
    tags: tags
    postgresServerId: postgresServerId
    containerAppId: containerApp.?outputs.containerAppId ?? ''
    alertEmailAddress: alertEmailAddress
  }
}

output containerAppFqdn string = containerApp.?outputs.fqdn ?? ''
