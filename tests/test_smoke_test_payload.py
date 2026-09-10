"""Correction pass, item 1: proves `scripts/smoke_test_payload.py`'s
payload is genuinely valid by submitting it through the **real**
ingestion ASGI application -- the exact `create_app`/`IngestionApiConfig`
production code path, a real Argon2id-backed token (via the real
`provision_token()`, never a fake verifier), and a real loopback HTTP
server (`tests/ingestion_api_support.run_loopback_server`) -- and proving
POST -> GET -> DELETE all succeed with the real, expected status codes.

**Reproduction, before the fix**: the workflow's own previous smoke
payload (`{"platform":"kubernetes","report_schema_version":1,"report":
{"findings":[],"summary":{"total":0}}}`) was submitted directly to
`cloudops_guard.ingestion_api.report_validation.validate_report` and
confirmed to raise `ApiError(INVALID_REPORT)` -- the real `AuditReport`
model requires `cluster_context`/`namespace_filter`/`generated_at`, none
of which that payload supplied, and `summary.total` is not a real field
(`AuditSummary.total` is a computed `@property`). `TestOldPayloadWasGenuinelyBroken`
below re-confirms this exact reproduction as a permanent regression
guard, independent of whatever `smoke_test_payload.py` does today.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import httpx
import pytest

from cloudops_guard.ingestion.argon2_backend import Argon2SecretVerifier
from cloudops_guard.ingestion.fingerprint import compute_report_fingerprint
from cloudops_guard.ingestion.models import TokenScope
from cloudops_guard.ingestion.reference import (
    InMemoryAttemptLimiter,
    InMemoryMetadataStore,
    InMemoryReportBlobStore,
    InMemoryRequestRateLimiter,
    InMemoryTokenStore,
)
from cloudops_guard.ingestion.token_issuance import provision_token
from cloudops_guard.ingestion_api.app import create_app
from cloudops_guard.ingestion_api.config import IngestionApiConfig
from cloudops_guard.ingestion_api.errors import ApiError
from cloudops_guard.ingestion_api.report_validation import validate_report
from tests.ingestion_api_support import run_async, run_loopback_server

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from smoke_test_payload import build_smoke_test_report  # noqa: E402


class TestOldPayloadWasGenuinelyBroken:
    def test_the_original_smoke_payload_fails_real_server_side_validation(self) -> None:
        with pytest.raises(ApiError) as excinfo:
            validate_report("kubernetes", 1, {"findings": [], "summary": {"total": 0}})
        assert excinfo.value.code == "invalid_report"


class TestSmokeTestPayloadIsGenuinelyValid:
    def test_build_smoke_test_report_passes_real_server_side_validation(self) -> None:
        payload = build_smoke_test_report()
        validate_report(payload["platform"], payload["report_schema_version"], payload["report"])

    def test_generated_at_is_unique_across_successive_calls(self) -> None:
        """Task requirement: "Generate a unique `generated_at` value for
        each execution so the smoke test does not accidentally reuse a
        prior fingerprint." A fixed `generated_at` would make every
        smoke-test run's own `report_fingerprint` collide with the first
        one's forever.
        """
        first = build_smoke_test_report()
        second = build_smoke_test_report()
        assert first["report"]["generated_at"] != second["report"]["generated_at"]

    def test_explicit_generated_at_override_is_honored(self) -> None:
        """Tests need deterministic fingerprints -- confirms the override
        parameter exists and is actually used, not silently ignored.
        """
        fixed = "2026-01-01T00:00:00+00:00"
        payload = build_smoke_test_report(generated_at=fixed)
        assert payload["report"]["generated_at"] == fixed

    def test_frozen_generated_at_still_yields_distinct_fingerprints(self) -> None:
        """Correction pass, item 3: the actual uniqueness guarantee must
        not depend on `generated_at` at all -- freezes it to the exact
        same value for two separate calls (simulating the documented
        risk: two calls observing the same clock tick, or two
        independent workflow dispatches with no shared monotonic state)
        and proves both payloads still (1) pass real validation and (2)
        produce different `report_fingerprint` values, via the real
        production fingerprint function.
        """
        fixed = "2026-01-01T00:00:00+00:00"
        first = build_smoke_test_report(generated_at=fixed)
        second = build_smoke_test_report(generated_at=fixed)

        assert first["report"]["generated_at"] == second["report"]["generated_at"] == fixed
        assert first["report"]["cluster_context"] != second["report"]["cluster_context"], (
            "the uniqueness suffix itself must differ even under a frozen generated_at"
        )

        for payload in (first, second):
            validate_report(
                payload["platform"], payload["report_schema_version"], payload["report"]
            )

        first_fingerprint = compute_report_fingerprint(
            first["platform"], first["report_schema_version"], first["report"]
        )
        second_fingerprint = compute_report_fingerprint(
            second["platform"], second["report_schema_version"], second["report"]
        )
        assert first_fingerprint != second_fingerprint

    def test_cluster_context_suffix_is_a_real_documented_report_field_not_a_new_one(
        self,
    ) -> None:
        """Task requirement: "Do not add an undocumented report field or
        weaken report validation." `cluster_context` is already a
        required field of the released `AuditReport` contract -- this
        test fails if the uniqueness mechanism is ever reimplemented by
        adding some new, non-contract field instead.
        """
        payload = build_smoke_test_report()
        report_keys = set(payload["report"].keys())
        assert report_keys == {
            "cluster_context",
            "namespace_filter",
            "generated_at",
            "findings",
            "summary",
        }


def _build_real_app_and_token() -> tuple[object, str]:
    """Builds a real `IngestionApiConfig`/ASGI app backed by in-memory
    reference stores, and a real, genuinely Argon2id-hashed bearer token
    (via `provision_token`, the same function a real deployment's
    manual-token-provisioning procedure uses) with all three report
    scopes -- never a fake secret verifier, unlike most of this
    project's other HTTP-layer tests, since this specific test exists to
    prove the smoke payload survives the *entire* real path end to end.
    """
    metadata_store = InMemoryMetadataStore()
    blob_store = InMemoryReportBlobStore()
    token_store = InMemoryTokenStore(secret_verifier=Argon2SecretVerifier())
    config = IngestionApiConfig(
        metadata_store=metadata_store,
        blob_store=blob_store,
        token_store=token_store,
        lookup_limiter=InMemoryAttemptLimiter(threshold=1000),
        source_limiter=InMemoryAttemptLimiter(threshold=1000),
        token_rate_limiter=InMemoryRequestRateLimiter(threshold=1000),
        capabilities_rate_limiter=InMemoryRequestRateLimiter(threshold=1000),
        retention_period=dt.timedelta(days=90),
    )
    app = create_app(config)

    issued = provision_token(
        "smoke-test-tenant",
        [TokenScope.REPORTS_WRITE, TokenScope.REPORTS_READ, TokenScope.REPORTS_DELETE],
    )
    token_store.register_for_testing(issued.token_record)
    return app, issued.token


class TestRealEndToEndPostGetDelete:
    """Task requirement: "Add a behavioral test that submits the exact
    workflow smoke payload through the real ingestion ASGI application
    and proves POST -> GET -> DELETE succeeds." Uses a real loopback
    HTTP server (a real socket, real `uvicorn`), never merely an
    in-process ASGI transport -- the closest a test in this repository
    can get to what the real workflow's own `curl` invocations do
    against a real, deployed Container App.
    """

    def test_post_get_delete_all_succeed_with_the_real_smoke_payload(self) -> None:
        app, token = _build_real_app_and_token()
        payload = build_smoke_test_report()

        async def scenario() -> None:
            with run_loopback_server(app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    headers = {"Authorization": f"Bearer {token}"}

                    post_response = await client.post(
                        "/api/v1/reports", json=payload, headers=headers
                    )
                    assert post_response.status_code == 201, post_response.text
                    ingestion_id = post_response.json()["ingestion_id"]

                    get_response = await client.get(
                        f"/api/v1/reports/{ingestion_id}", headers=headers
                    )
                    assert get_response.status_code == 200, get_response.text

                    delete_response = await client.delete(
                        f"/api/v1/reports/{ingestion_id}", headers=headers
                    )
                    assert delete_response.status_code == 200, delete_response.text

        run_async(scenario())

    def test_a_second_smoke_run_with_a_fresh_generated_at_does_not_collide(self) -> None:
        """Proves the "unique `generated_at` per execution" property
        matters in practice: two successive, otherwise-identical smoke
        payloads (as two separate real workflow dispatches would send)
        must each independently succeed with their own new
        `ingestion_id` -- never silently short-circuited into replaying
        the first run's own already-`received` record.
        """
        app, token = _build_real_app_and_token()

        async def scenario() -> None:
            with run_loopback_server(app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    headers = {"Authorization": f"Bearer {token}"}

                    first_payload = build_smoke_test_report()
                    first_response = await client.post(
                        "/api/v1/reports", json=first_payload, headers=headers
                    )
                    assert first_response.status_code == 201, first_response.text
                    first_id = first_response.json()["ingestion_id"]

                    second_payload = build_smoke_test_report()
                    second_response = await client.post(
                        "/api/v1/reports", json=second_payload, headers=headers
                    )
                    assert second_response.status_code == 201, second_response.text
                    second_id = second_response.json()["ingestion_id"]

                    assert first_id != second_id, (
                        "two successive smoke runs must never collapse into "
                        "the same ingestion_id via fingerprint dedup"
                    )

                    for ingestion_id in (first_id, second_id):
                        delete_response = await client.delete(
                            f"/api/v1/reports/{ingestion_id}", headers=headers
                        )
                        assert delete_response.status_code == 200

        run_async(scenario())

    def test_two_submissions_with_an_identical_frozen_generated_at_still_get_distinct_ids(
        self,
    ) -> None:
        """Correction pass, item 3's own explicit requirement: "Two real
        API submissions produce different ingestion IDs" -- exercised
        with `generated_at` deliberately *frozen to the same value* for
        both submissions (the exact risk item 3 describes: two calls
        observing the same clock value, or two independent dispatches
        with no shared monotonic state), against the real server's own
        atomic `(tenant_id, report_fingerprint)` dedup -- proving the
        `cluster_context` uniqueness suffix, not `generated_at`, is what
        actually keeps these two submissions from colliding.
        """
        app, token = _build_real_app_and_token()
        frozen_generated_at = "2026-01-01T00:00:00+00:00"

        async def scenario() -> None:
            with run_loopback_server(app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    headers = {"Authorization": f"Bearer {token}"}

                    first_payload = build_smoke_test_report(generated_at=frozen_generated_at)
                    second_payload = build_smoke_test_report(generated_at=frozen_generated_at)
                    assert (
                        first_payload["report"]["generated_at"]
                        == second_payload["report"]["generated_at"]
                        == frozen_generated_at
                    )

                    first_response = await client.post(
                        "/api/v1/reports", json=first_payload, headers=headers
                    )
                    assert first_response.status_code == 201, first_response.text
                    first_id = first_response.json()["ingestion_id"]

                    second_response = await client.post(
                        "/api/v1/reports", json=second_payload, headers=headers
                    )
                    assert second_response.status_code == 201, second_response.text
                    second_id = second_response.json()["ingestion_id"]

                    assert first_id != second_id, (
                        "an identical frozen generated_at must not collapse two "
                        "submissions into the same ingestion_id"
                    )

                    for ingestion_id in (first_id, second_id):
                        delete_response = await client.delete(
                            f"/api/v1/reports/{ingestion_id}", headers=headers
                        )
                        assert delete_response.status_code == 200

        run_async(scenario())
