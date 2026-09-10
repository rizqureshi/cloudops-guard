// Phase 4G-A: placeholder parameters only (task 9's explicit
// requirement). Every value below must be replaced with a real one, out
// of band, before this template is ever deployed -- and only after
// `foundation.bicep` has already been deployed and its outputs are
// available to populate the `*Id`/`*Uri` values below.
//
// **Correction-pass item 2: this file is never read for a real
// deployment.** `.github/workflows/deploy-ingestion-azure.yml`'s
// `deploy` job applies `app.bicep` using an ephemeral parameter file
// generated at dispatch time (`scripts/generate_app_deployment_params.py`)
// from `foundation`'s real, live deployment outputs -- never from this
// checked-in file, every value of which is a placeholder by design. This
// file exists only so `bicep build-params` can validate that its
// parameter *names* stay in sync with `app.bicep`'s own declared
// parameters (`verify`'s own "Bicep parameter file compilation check"
// step), and as human-readable documentation of every parameter an
// operator manually applying this template outside the workflow would
// need to supply.

using 'app.bicep'

param namePrefix = 'REPLACE-WITH-NAME-PREFIX' // must match foundation.bicepparam's namePrefix
param environmentId = 'REPLACE-WITH-FOUNDATION-OUTPUT-environmentId'
param registryLoginServer = 'REPLACE-WITH-FOUNDATION-OUTPUT-registryLoginServer'
param appIdentityId = 'REPLACE-WITH-FOUNDATION-OUTPUT-appIdentityId'
param appIdentityClientId = 'REPLACE-WITH-FOUNDATION-OUTPUT-appIdentityClientId'
param operatorIdentityId = 'REPLACE-WITH-FOUNDATION-OUTPUT-operatorIdentityId'
param operatorIdentityClientId = 'REPLACE-WITH-FOUNDATION-OUTPUT-operatorIdentityClientId'
param migrationIdentityId = 'REPLACE-WITH-FOUNDATION-OUTPUT-migrationIdentityId'
param migrationIdentityClientId = 'REPLACE-WITH-FOUNDATION-OUTPUT-migrationIdentityClientId'
param keyVaultUri = 'REPLACE-WITH-FOUNDATION-OUTPUT-keyVaultUri'
param postgresServerId = 'REPLACE-WITH-FOUNDATION-OUTPUT-postgresServerId'
param blobAccountUrl = 'REPLACE-WITH-FOUNDATION-OUTPUT-blobEndpoint'
param blobContainerName = 'REPLACE-WITH-FOUNDATION-OUTPUT-reportContainerName'

// Must be a full, digest-pinned image reference -- e.g.
// "REPLACE.azurecr.io/cloudops-guard-ingestion-api@sha256:<64 hex
// characters>" -- produced only after a real image has been built from
// a reviewed commit and pushed by the deployment workflow. Never a
// mutable tag (e.g. "latest" or a branch name).
param imageDigest = 'REPLACE-WITH-DIGEST-PINNED-IMAGE-REFERENCE'

param alertEmailAddress = 'REPLACE-WITH-ONCALL-EMAIL-ADDRESS'
