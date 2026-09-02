"""Correction-pass item 4: only the four exact, declared routes ever
match -- no empty path segment (leading, trailing, or internal double
slash) is ever collapsed, so none of these can alias a real endpoint.
Includes percent-encoded-slash/path-confusion cases; never a redirect.
"""

from __future__ import annotations

import httpx

from tests.ingestion_api_support import IngestionApiTestHarness, with_client


def _get(harness: IngestionApiTestHarness, path: str) -> httpx.Response:
    async def _do(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get(path, follow_redirects=False)

    return with_client(harness, _do)


def _get_raw_path(harness: IngestionApiTestHarness, raw_path: str) -> httpx.Response:
    """Constructs an ASGI scope with `path` set exactly to `raw_path` --
    bypassing httpx/URL normalization entirely, so a deliberately
    malformed or already-percent-decoded path (exactly as a real,
    ASGI-spec-conformant server would have already decoded it) reaches
    dispatch unmodified.
    """
    import asyncio

    scope = {
        "type": "http",
        "method": "GET",
        "path": raw_path,
        "headers": [],
        "query_string": b"",
        "client": ("203.0.113.5", 12345),
    }
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(harness.app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = next(m["body"] for m in sent if m["type"] == "http.response.body")
    return status, body


class TestExactCapabilitiesRoute:
    def test_bare_route_matches(self) -> None:
        harness = IngestionApiTestHarness()
        assert _get(harness, "/api/v1/capabilities").status_code == 200

    def test_trailing_slash_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/api/v1/capabilities/")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_double_slash_before_v1_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        status, body = _get_raw_path(harness, "/api//v1/capabilities")
        assert status == 404
        import json

        assert json.loads(body)["error"] == "not_found"

    def test_double_slash_before_capabilities_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        status, body = _get_raw_path(harness, "/api/v1//capabilities")
        assert status == 404
        import json

        assert json.loads(body)["error"] == "not_found"

    def test_leading_double_slash_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        status, _body = _get_raw_path(harness, "//api/v1/capabilities")
        assert status == 404

    def test_none_of_the_aliases_ever_redirect(self) -> None:
        harness = IngestionApiTestHarness()
        for path in (
            "/api/v1/capabilities/",
            "/api//v1/capabilities",
            "/api/v1//capabilities",
        ):
            resp = _get(harness, path)
            assert resp.status_code not in (301, 302, 303, 307, 308)


class TestExactReportsCollectionRoute:
    def test_trailing_slash_is_404_not_405(self) -> None:
        # A 405 would imply the route itself matched (just the wrong
        # method) -- the trailing-slash path must not match the route at
        # all, so this is 404, not 405.
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/api/v1/reports/")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_double_slash_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        status, _body = _get_raw_path(harness, "/api/v1//reports")
        assert status == 404


class TestExactReportItemRoute:
    def test_bare_route_matches(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get(
                "/api/v1/reports/ing_whatever", headers={"Authorization": f"Bearer {token}"}
            )

        resp = with_client(harness, _do)
        assert resp.status_code == 404  # unknown id -- but the ROUTE matched
        assert resp.json()["error"] == "not_found"

    def test_trailing_slash_after_id_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        status, _body = _get_raw_path(harness, "/api/v1/reports/ing_whatever/")
        assert status == 404

    def test_empty_id_segment_from_double_slash_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        status, _body = _get_raw_path(harness, "/api/v1/reports//")
        assert status == 404

    def test_double_slash_before_the_id_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        status, _body = _get_raw_path(harness, "/api/v1/reports//ing_whatever")
        assert status == 404


class TestPercentEncodedSlashPathConfusion:
    def test_already_decoded_slash_in_id_position_is_404_not_aliased(self) -> None:
        # What a real, ASGI-spec-conformant server presents after
        # decoding a client's literal `%2F`: this function never decodes
        # anything itself, so it must never re-interpret this as
        # anything other than one extra path segment -- correctly
        # rejected as 404, never matched to any route (in particular,
        # never treated as a single id "abc/def").
        harness = IngestionApiTestHarness()
        status, body = _get_raw_path(harness, "/api/v1/reports/abc/def")
        assert status == 404
        import json

        assert json.loads(body)["error"] == "not_found"

    def test_still_encoded_percent_2f_in_id_is_treated_as_an_ordinary_id_character_sequence(
        self,
    ) -> None:
        # If a raw, still-percent-encoded "%2F" somehow reaches this
        # layer (this function performs no decoding of its own, so it
        # never distinguishes this from any other id-shaped string): a
        # single 5-segment path, safely matched to the item route with an
        # unusual (but harmless) literal id value -- never split into
        # extra segments, never a route-confusion bypass.
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get(
                "/api/v1/reports/abc%252Fdef",  # httpx encodes %2F -> %252F verbatim
                headers={"Authorization": f"Bearer {token}"},
            )

        resp = with_client(harness, _do)
        # Either a clean 404 (unknown id) or 200 (never expected here) --
        # the important property is NOT 500, NOT routed to a different
        # handler, and never a path-confusion bypass to another tenant's
        # or another route's behavior.
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_null_byte_in_id_segment_is_handled_safely_never_500(self) -> None:
        # The route itself matches (5 segments, non-empty last segment,
        # including a literal NUL character in an unusual id) -- the
        # important property is that this reaches the handler safely
        # (never an uncaught exception/500), not any particular status.
        harness = IngestionApiTestHarness()
        status, _body = _get_raw_path(harness, "/api/v1/reports/abc\x00def")
        assert status in (401, 404)


class TestUnsupportedApiVersionVsNotFound:
    def test_clean_unsupported_version_is_unsupported_api_version(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "/api/v2/capabilities")
        assert resp.status_code == 404
        assert resp.json()["error"] == "unsupported_api_version"

    def test_malformed_version_segment_from_double_slash_is_generic_not_found(self) -> None:
        # An empty segment in the version position is a malformed path,
        # not a "named but unsupported" version -- must be the generic
        # not_found code, never unsupported_api_version.
        harness = IngestionApiTestHarness()
        status, body = _get_raw_path(harness, "/api//capabilities")
        assert status == 404
        import json

        assert json.loads(body)["error"] == "not_found"
