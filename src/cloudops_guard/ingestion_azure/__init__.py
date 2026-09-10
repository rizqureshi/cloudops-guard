"""Azure production adapters for the v0.4.0 ingestion API (Phase 4G-A).

**Phase boundary, restated here because it governs every module in this
package**: Phase 4G-A implements deployable code, container/infrastructure
definitions, and deployment tooling -- it creates no Azure resource, reads
no real credential, and contacts no live Azure subscription. Phase 4G-B
(separately authorized, not started) is the only phase that may run any of
this against a real subscription. Every adapter here is unit- and
integration-tested against a local, offline stand-in (a real, local
PostgreSQL container for the database adapters; Azurite, the offline Azure
Storage emulator, for the blob adapter) -- never against a real Azure
account. See `docs/deployment/azure-ingestion-production.md` for the full
architecture this package implements.

**Recorded human decisions this package implements (not decisions it
makes)**: Microsoft Azure, Canada Central (`canadacentral`), the
managed-container "Option A" architecture from `docs/deployment/
ingestion-production.md`, Azure Container Apps (Consumption plan), Azure
Container Registry (Basic), Azure Database for PostgreSQL Flexible Server,
Azure Blob Storage, and a PostgreSQL-backed distributed limiter (no Azure
Managed Redis/Azure Cache for Redis in this pilot baseline). See
`docs/pilots/phase-4g-authorization-checklist.md` for where these
decisions are recorded as satisfied preconditions.

**Optional, isolated dependency boundary**: importing this package (or
installing the base CLI, or the local/staging `api` extra) never requires
installing the `azure-production` extra -- a database driver and a cloud
SDK are only ever needed by code that actually talks to a real (or
emulated) Postgres/Blob Storage endpoint. `tests/
test_uploader_dependency_boundary.py`-style import-boundary coverage for
this package lives in `tests/ingestion_azure/
test_azure_extra_import_boundary.py`.

**Dependency justification** (`pyproject.toml`'s `azure-production`
extra, CLAUDE.md: "avoid unnecessary dependencies... justify any
addition"):

- `psycopg[binary,pool]` -- a maintained PostgreSQL driver with a built-in
  synchronous connection pool (`psycopg_pool`). No ORM is added: every
  interface this package implements (`cloudops_guard.ingestion.
  interfaces`) is already a thin, explicit method contract with no need
  for a query builder or model-mapping layer on top of it, and hand-
  written SQL keeps the exact atomicity/locking behavior these interfaces
  require (`create_or_get_received`'s single-transaction dedup, the
  exclusive purge-claim protocol) directly auditable. The synchronous API
  is used deliberately: every method these adapters implement is already
  a plain synchronous call, invoked from `ingestion_api.app`'s existing
  `anyio.to_thread.run_sync` worker-thread offload -- there is no event
  loop inside an adapter to justify an async driver.
- `azure-storage-blob` -- the official Azure Blob Storage SDK.
  `ReportBlobStore.put_if_absent`'s conditional-create requirement maps
  directly onto Azure Blob's own `if_none_match="*"` upload precondition;
  hand-rolling raw REST calls against Azure Storage's control plane would
  be substantially riskier than using Microsoft's own maintained client.
- `azure-identity` -- `DefaultAzureCredential`, for managed-identity
  authentication to Blob Storage. A storage-account key is never used,
  embedded in code, or baked into a container image.
"""
