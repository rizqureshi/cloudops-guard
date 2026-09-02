"""`GET /api/v1/capabilities` tests (§E.1) -- including task 3.1's
resolution: the response now carries a fresh `request_id`, which the
milestone document's original example omitted (a discrepancy from the
document's own stronger global invariant, resolved in favor of that
invariant -- see the Phase 4D report).
"""

from __future__ import annotations

import httpx

from cloudops_guard.ingestion.abuse_protection import source_scope_key
from tests.ingestion_api_support import IngestionApiTestHarness, with_client


def _capabilities(harness: IngestionApiTestHarness, **kwargs: object) -> httpx.Response:
    async def _do(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get("/api/v1/capabilities", **kwargs)  # type: ignore[arg-type]

    return with_client(harness, _do)


class TestCapabilitiesSuccessShape:
    def test_response_shape_and_values(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _capabilities(harness)
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "ok": True,
            "api_version": "v1",
            "request_id": "req_test_1",
            "supported_report_schema_versions": {"kubernetes": [1], "gitlab": [1]},
            "max_report_bytes": 10485760,
            "max_request_body_bytes": 10489856,
            "max_findings_per_report": 10000,
        }

    def test_request_id_present_and_fresh_every_call(self) -> None:
        # Task 3.1: the milestone's stronger global "fresh request_id on
        # every response" invariant wins over the document's own example,
        # which omitted it -- this is the resolved, adopted behavior.
        harness = IngestionApiTestHarness()
        first = _capabilities(harness).json()["request_id"]
        second = _capabilities(harness).json()["request_id"]
        assert first.startswith("req_")
        assert second.startswith("req_")
        assert first != second

    def test_no_auth_tenant_or_infrastructure_detail_present(self) -> None:
        harness = IngestionApiTestHarness()
        body = _capabilities(harness).json()
        forbidden_keys = {
            "tenant_id",
            "tenant",
            "rate_limit",
            "rate_limit_budget",
            "provider",
            "region",
            "database",
            "storage",
        }
        assert forbidden_keys.isdisjoint(body.keys())

    def test_unauthenticated_no_authorization_header_needed(self) -> None:
        harness = IngestionApiTestHarness()

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get("/api/v1/capabilities")

        resp = with_client(harness, _do)
        assert resp.status_code == 200


def _capabilities_from(harness: IngestionApiTestHarness, host: str, port: int) -> httpx.Response:
    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=harness.app, client=(host, port))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/api/v1/capabilities")

    import asyncio

    return asyncio.run(_run())


class TestCapabilitiesRateLimiting:
    def test_source_scoped_layer2_block_returns_429(self) -> None:
        # Scoped by host only, never the client's ephemeral TCP port --
        # see _peer_source_identifier's docstring for why (a real client
        # opens a fresh source port per connection; including it would
        # let an attacker escape this scope just by opening another
        # connection).
        harness = IngestionApiTestHarness(source_threshold=1)
        harness.source_limiter.record_failure(source_scope_key("203.0.113.5"))
        resp = _capabilities(harness)
        assert resp.status_code == 429
        assert resp.json()["error"] == "rate_limited"

    def test_capabilities_own_request_rate_ceiling_returns_429(self) -> None:
        harness = IngestionApiTestHarness(capabilities_rate_threshold=2)
        assert _capabilities(harness).status_code == 200
        assert _capabilities(harness).status_code == 200
        resp = _capabilities(harness)
        assert resp.status_code == 429
        assert resp.json()["error"] == "rate_limited"

    def test_capabilities_request_rate_ceiling_is_independent_per_source(self) -> None:
        harness = IngestionApiTestHarness(capabilities_rate_threshold=1)
        assert _capabilities_from(harness, "203.0.113.5", 12345).status_code == 200
        # A distinct source's own budget is untouched by the first
        # source's consumption.
        assert _capabilities_from(harness, "198.51.100.9", 9999).status_code == 200
        # The first source is now over its own budget.
        assert _capabilities_from(harness, "203.0.113.5", 12345).status_code == 429

    def test_x_forwarded_for_header_does_not_change_the_scope_key(self) -> None:
        # Task 10: the abuse-protection source identifier is derived only
        # from the actual peer connection -- a spoofed forwarding header
        # must never let a caller escape (or forge) its own rate-limit
        # scope.
        harness = IngestionApiTestHarness(capabilities_rate_threshold=1)
        assert _capabilities(harness).status_code == 200

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get("/api/v1/capabilities", headers={"X-Forwarded-For": "9.9.9.9"})

        resp = with_client(harness, _do)
        # Same real peer host (default 203.0.113.5) as the first call --
        # still blocked despite the spoofed header, proving the header
        # was never consulted.
        assert resp.status_code == 429
