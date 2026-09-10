#!/usr/bin/env python3
"""Builds the exact synthetic Kubernetes report envelope the production
deployment workflow's smoke test posts to `POST /api/v1/reports`
(correction pass, item 1).

**The defect this replaces**: the workflow previously posted
`{"platform":"kubernetes","report_schema_version":1,"report":
{"findings":[],"summary":{"total":0}}}` -- a report object missing the
released `AuditReport` contract's required `cluster_context`,
`namespace_filter`, and `generated_at` fields, and a `summary` object
using a nonexistent `total` key instead of the real `critical`/`high`/
`medium`/`low` fields (`total` is a computed `@property` on
`AuditSummary`, never a real field). Reproduced directly: constructing
`AuditReport(**{"findings": [], "summary": {"total": 0}})` raises a
`pydantic.ValidationError` for the three missing required fields, and
the real server-side `report_validation.validate_report` (which
constructs exactly this model) would therefore reject this payload with
`400 invalid_report`, not the `201 Created` the smoke test's own
assertion expects -- meaning the smoke test's success path had never
actually been exercised by a genuinely valid payload.

**The fix**: this module is the *single source of truth* for the smoke
payload -- both the workflow (`python3 scripts/smoke_test_payload.py`,
printing the JSON document to stdout) and this project's own behavioral
test (`tests/test_smoke_test_payload.py`, importing
`build_smoke_test_report` directly) call the exact same function, so the
two can never silently drift apart.

**Correction pass -- `generated_at` alone was not an actual uniqueness
guarantee**: an earlier version of this module relied solely on
`datetime.now(UTC)` at microsecond precision to keep successive smoke
runs from colliding on `report_fingerprint` (the ingestion API's own
deduplication key, computed from the complete, canonicalized
`{platform, report_schema_version, report}` document). Two calls
*normally* observe different wall-clock values, but nothing guarantees
that: the system clock's actual resolution is not contractually
microsecond-fine on every platform, two calls can genuinely race onto
the same tick, and two independent workflow dispatches (e.g. two
`deploy` runs kicked off by different operators within the same clock
tick, or a runner whose clock is coarser than expected) share no
process-level monotonic state that would prevent it. Fixed by adding a
fresh, cryptographically strong (`uuid.uuid4`, backed by the OS CSPRNG)
random suffix to the synthetic `cluster_context` field -- a genuine,
already-`AuditReport`-typed `str` field, never an undocumented addition
to the report schema, and never a weakening of any existing validation.
`generated_at` is kept as a real, valid UTC timestamp (still useful as a
human-readable audit trail of when a given smoke run actually
happened), but is no longer what uniqueness depends on. A UUIDv4 suffix
gives cryptographically strong *practical* uniqueness (collision
resistance from a 128-bit CSPRNG value) -- this is not, and is never
claimed to be, an absolute mathematical guarantee that two calls can
never coincide.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from typing import Any


def build_smoke_test_report(generated_at: str | None = None) -> dict[str, Any]:
    """Returns a complete, valid ingestion-API envelope wrapping a
    genuinely `AuditReport`-shaped Kubernetes report -- zero findings, an
    all-zero four-counter summary (`critical`/`high`/`medium`/`low`,
    matching the real, empty `findings` list, per the server's own
    `_assert_summary_matches` cross-check), a fresh `generated_at`
    (current UTC time) unless the caller supplies one explicitly (tests
    do, for determinism), and a fresh, cryptographically random suffix
    on `cluster_context` -- the mechanism this module actually relies on
    for practical uniqueness (see this module's own docstring): even when
    `generated_at` is fixed (an explicit override, or two calls that
    happen to observe the same clock value), the `cluster_context` suffix
    alone gives the complete report -- and therefore its
    `report_fingerprint` -- cryptographically strong collision resistance
    on every call. This is practical, not absolute, uniqueness: a 128-bit
    random value has a non-zero, but for any realistic number of smoke
    runs utterly negligible, chance of colliding with a prior one -- never
    claimed here as a mathematical impossibility.
    """
    if generated_at is None:
        generated_at = dt.datetime.now(dt.UTC).isoformat()
    unique_suffix = uuid.uuid4().hex

    return {
        "platform": "kubernetes",
        "report_schema_version": 1,
        "report": {
            "cluster_context": f"cog-smoke-test-cluster-{unique_suffix}",
            "namespace_filter": None,
            "generated_at": generated_at,
            "findings": [],
            "summary": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(json.dumps(build_smoke_test_report()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
