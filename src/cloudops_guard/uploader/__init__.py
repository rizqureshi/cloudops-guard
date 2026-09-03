"""Phase 4E: the customer-controlled `cloudops-guard upload` CLI command.

**Not automatic, ever.** Existing audit commands (`cloudops-guard audit
kubernetes`/`gitlab`) never import this package and never perform
ingestion-network activity -- see `tests/test_uploader_regression_isolation.py`
for the enforced proof. Uploading a report is a separate, explicit,
customer-initiated action: `cloudops-guard upload --report-dir <dir>
--endpoint <url>`, requiring either an exact, case-sensitive `UPLOAD`
typed at an interactive confirmation prompt, or an explicit `--yes` flag.

**Absolute pre-confirmation privacy boundary**: before that confirmation
(or `--yes`) is satisfied, this package performs zero network activity of
any kind -- no DNS resolution, no connection attempt, no capabilities
request, no authentication probe. Local report loading, strict-JSON
decoding, report-contract validation, RFC 8785 fingerprinting, endpoint-
URL validation, and the local summary shown to the user are all pure,
local operations using only `cloudops_guard.ingestion.fingerprint`/
`cloudops_guard.ingestion.strict_json` (the same authoritative
implementations the ingestion API itself uses) and
`cloudops_guard.ingestion_api.report_validation`/`limits` (dependency-
free, reused rather than duplicated -- see `local_report.py`'s own
docstring for why importing from `ingestion_api` here is still safe: none
of the modules this package actually imports pull in that package's
`api`-extra-only dependencies). Only `--dry-run` and the local-validation/
summary phase of every other mode ever run before that boundary; the
actual HTTP POST -- via `transport.py`'s injectable, `urllib3`-backed
transport -- happens only after it, and only in service.py's
`run_upload`.

No module in this package opens a socket, reads an environment variable,
or performs any I/O at import time.
"""

from __future__ import annotations
