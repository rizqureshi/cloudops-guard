// Phase 4G-A: placeholder parameters only -- no subscription ID, tenant
// ID, client ID, email address, domain, or secret value appears in this
// file (task 9's explicit requirement). Every value below must be
// replaced with a real one, out of band (never committed to this
// repository), before this template is ever deployed -- Phase 4G-B,
// separately authorized, work.

using 'foundation.bicep'

param namePrefix = 'REPLACE-WITH-NAME-PREFIX' // e.g. 'cog-ingestion-pilot'
param globalSuffix = 'REPLACE-WITH-UNIQUE-SUFFIX' // e.g. a short random alphanumeric string
param tenantId = 'REPLACE-WITH-AZURE-AD-TENANT-ID'

// Never a literal password -- generate at deployment time (e.g.
// `az postgres flexible-server` password-generation guidance, or a
// secrets-manager-generated value) and pass via a secure parameter
// mechanism (`--parameters postgresAdministratorPassword=@securefile`
// or a deployment pipeline secret), never typed into this file.
param postgresAdministratorPassword = 'REPLACE-AT-DEPLOYMENT-TIME-NEVER-COMMITTED'

// Correction-pass item 10: must be the first day of the *current*
// calendar month at deployment time (e.g. `date -u +%Y-%m-01`) -- never
// a fixed, checked-in date, which would eventually fall outside Azure's
// required current-time-grain-period window and fail deployment.
param budgetStartDate = 'REPLACE-AT-DEPLOYMENT-TIME-WITH-FIRST-OF-CURRENT-MONTH'
