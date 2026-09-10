// Phase 4G-A (corrected): the schema-migration Container Apps Job only.
//
// **Correction pass (this pass), item 1**: this module previously also
// declared the three *scheduled* operator jobs (`retention-sweep`,
// `purge-sweep`, `limiter-cleanup`) alongside `migrate`, and `app.bicep`
// deployed this entire module unconditionally -- including on the
// deliberately pre-traffic, `deployTraffic=false` "Stage 1" deployment
// the workflow uses to update `migrate`'s own image before the
// migration-compatibility gate runs. Since this module was unconditional,
// that same Stage 1 deployment *also* silently repointed every scheduled
// job at the untested candidate image -- and those jobs run
// automatically on a cron schedule (`retention-sweep` hourly,
// `purge-sweep` hourly, `limiter-cleanup` every 15 minutes), so a
// candidate image that later failed the compatibility gate could still
// have its retention/purge sweep execute against production data before
// the gate ever ran, let alone failed it. Reproduced directly against
// the compiled ARM template: `jobs`'s own resource had no `condition`,
// and all four job resources (including the three scheduled ones) were
// nested underneath it.
//
// **The fix**: this module now declares `migrate` only -- the one job
// the pre-traffic compatibility gate actually needs updated first, and
// which is manually-triggered-only (never scheduled, so it can never
// execute unattended regardless of deployment ordering). The three
// scheduled operator jobs moved to `scheduled-jobs.bicep`, which
// `app.bicep` deploys only as part of the same `deployTraffic`-gated
// group as the traffic-facing Container App and monitoring alerts --
// i.e., only *after* the compatibility gate has already passed.

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

@description('Migration identity resource ID -- the migrate job only.')
param migrationIdentityId string

@description('Migration identity client ID.')
param migrationIdentityClientId string

@description('Full image reference, pinned by digest.')
param imageDigest string

@description('Key Vault URI, for secret references.')
param keyVaultUri string

// Manual-trigger only -- a schema migration must never run unattended
// or on a schedule. Dispatched explicitly by `deploy-ingestion-azure.yml`
// (correction-pass item 1's migration-status/compatibility gate) via
// `az containerapp job start`, using the migration identity's own,
// narrowly-scoped `migration-metadata-db-conninfo` secret -- never the
// runtime or operator credentials, which lack DDL privilege by design
// (`docs/deployment/azure-ingestion-production.md` §4).
resource migrateJob 'Microsoft.App/jobs@2024-10-02-preview' = {
  name: '${namePrefix}-migrate'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${migrationIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0 // A migration must never be silently retried -- a failure needs human review.
      registries: [
        {
          server: registryLoginServer
          identity: migrationIdentityId
        }
      ]
      secrets: [
        {
          name: 'migration-metadata-db-conninfo'
          keyVaultUrl: '${keyVaultUri}secrets/migration-metadata-db-conninfo'
          identity: migrationIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: imageDigest
          // The actual command (`migrate` or `migration-status`) is
          // supplied at dispatch time via `az containerapp job start
          // --command`, overriding this default -- `migration-status`
          // is the safe, read-only default if this job is ever started
          // without an explicit override.
          command: ['python', '-m', 'cloudops_guard.ingestion_azure.ops_cli', 'migration-status']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'COG_INGESTION_REGION', value: 'canadacentral' }
            { name: 'COG_METADATA_DB_CONNINFO', secretRef: 'migration-metadata-db-conninfo' }
            { name: 'AZURE_CLIENT_ID', value: migrationIdentityClientId }
          ]
        }
      ]
    }
  }
}

output migrateJobId string = migrateJob.id
output migrateJobName string = migrateJob.name
