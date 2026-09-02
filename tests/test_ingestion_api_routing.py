"""Routing, method-matching, and fixed-error-envelope discipline tests
for the ingestion API (§5 -- no framework-default HTML/redirect/422
behavior anywhere).
"""

from __future__ import annotations

import json

import httpx

from cloudops_guard.ingestion_api.app import create_app
from tests.ingestion_api_support import (
    IngestionApiTestHarness,
    valid_kubernetes_report,
    with_client,
)


def _get(harness: IngestionApiTestHarness, path: str, **kwargs: object) -> httpx.Response:
    async def _do(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get(path, **kwargs)  # type: ignore[arg-type]

    return with_client(harness, _do)


class TestUnsupportedApiVersion:
    def test_unrecognized_version_segment_is_404_unsupported_api_version(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/api/v2/capabilities")
        assert resp.status_code == 404
        assert resp.json() == {
            "ok": False,
            "error": "unsupported_api_version",
            "request_id": "req_test_1",
        }

    def test_non_api_path_is_404_not_found(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/healthz")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"


class TestUnknownRoute:
    def test_unknown_route_under_v1_is_404_not_found(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/api/v1/does-not-exist")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_extra_path_segment_after_ingestion_id_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/api/v1/reports/abc/extra")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"


class TestMethodNotAllowed:
    def test_post_capabilities_is_405_with_allow_get(self) -> None:
        harness = IngestionApiTestHarness()

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post("/api/v1/capabilities")

        resp = with_client(harness, _do)
        assert resp.status_code == 405
        assert resp.headers["allow"] == "GET"
        assert resp.json()["error"] == "method_not_allowed"

    def test_head_capabilities_is_405_not_an_automatic_success(self) -> None:
        harness = IngestionApiTestHarness()

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.request("HEAD", "/api/v1/capabilities")

        resp = with_client(harness, _do)
        assert resp.status_code == 405

    def test_options_capabilities_is_405_not_an_automatic_response(self) -> None:
        harness = IngestionApiTestHarness()

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.request("OPTIONS", "/api/v1/capabilities")

        resp = with_client(harness, _do)
        assert resp.status_code == 405

    def test_get_reports_collection_is_405_with_allow_post(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/api/v1/reports")
        assert resp.status_code == 405
        assert resp.headers["allow"] == "POST"

    def test_post_report_item_is_405_with_allow_get_delete(self) -> None:
        harness = IngestionApiTestHarness()

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post("/api/v1/reports/some-id")

        resp = with_client(harness, _do)
        assert resp.status_code == 405
        assert resp.headers["allow"] == "GET, DELETE"

    def test_put_report_item_is_405(self) -> None:
        harness = IngestionApiTestHarness()

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.put("/api/v1/reports/some-id")

        resp = with_client(harness, _do)
        assert resp.status_code == 405
        assert resp.headers["allow"] == "GET, DELETE"


class TestNoTrailingSlashRedirect:
    """Correction-pass item 4: a trailing (or internal double) slash is
    never collapsed into the real route -- it is a *different* path
    shape, matching none of the four exact declared routes, and is
    therefore `404 not_found`, never a redirect and never silently
    aliased to the real endpoint (a deliberate reversal of this project's
    own earlier, superseded design, which had treated a trailing slash as
    identical to its non-trailing-slash route).
    """

    def test_capabilities_with_trailing_slash_is_not_a_redirect(self) -> None:
        harness = IngestionApiTestHarness()

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get("/api/v1/capabilities/", follow_redirects=False)

        resp = with_client(harness, _do)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"
        assert resp.status_code not in (301, 302, 307, 308)

    def test_reports_collection_with_trailing_slash_is_404_not_aliased(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/api/v1/reports/")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"


class TestErrorEnvelopeShape:
    def test_error_response_has_exactly_three_keys(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/api/v1/does-not-exist")
        assert set(resp.json().keys()) == {"ok", "error", "request_id"}

    def test_error_response_is_never_html_or_a_stack_trace(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/api/v1/does-not-exist")
        assert resp.headers["content-type"].startswith("application/json")
        assert b"<html" not in resp.content.lower()
        assert b"traceback" not in resp.content.lower()

    def test_request_id_is_fresh_on_every_request(self) -> None:
        harness = IngestionApiTestHarness()
        first = _get(harness, "/api/v1/capabilities").json()["request_id"]
        second = _get(harness, "/api/v1/capabilities").json()["request_id"]
        assert first != second


class TestInternalErrorIsSanitized:
    def test_unexpected_exception_becomes_sanitized_500(self) -> None:
        import dataclasses

        harness = IngestionApiTestHarness()

        class _ExplodingMetadataStore:
            def get(self, tenant_id: str, ingestion_id: str) -> None:
                raise RuntimeError("a secret internal detail that must never leak")

        # `IngestionApiConfig` is frozen by design (config.py) -- build a
        # new one with only `metadata_store` swapped, and a fresh app
        # closed over it, rather than mutating the harness's own config.
        exploding_config = dataclasses.replace(
            harness.config, metadata_store=_ExplodingMetadataStore()
        )
        harness.app = create_app(exploding_config)

        token = harness.issue_token("tenant-a")

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get(
                "/api/v1/reports/some-id", headers={"Authorization": f"Bearer {token}"}
            )

        resp = with_client(harness, _do)
        assert resp.status_code == 500
        assert resp.json()["error"] == "internal_error"
        assert b"secret internal detail" not in resp.content
        assert b"RuntimeError" not in resp.content


class TestGetDoesNotRequireJsonHeaders:
    def test_get_report_item_succeeds_with_no_content_type_header(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")

        async def _post_then_get(client: httpx.AsyncClient) -> httpx.Response:
            post_resp = await client.post(
                "/api/v1/reports",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(
                    {
                        "platform": "kubernetes",
                        "report_schema_version": 1,
                        "report": valid_kubernetes_report(),
                    }
                ),
            )
            ingestion_id = post_resp.json()["ingestion_id"]
            return await client.get(
                f"/api/v1/reports/{ingestion_id}", headers={"Authorization": f"Bearer {token}"}
            )

        resp = with_client(harness, _post_then_get)
        assert resp.status_code == 200
