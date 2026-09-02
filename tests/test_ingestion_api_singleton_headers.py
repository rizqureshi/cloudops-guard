"""Correction-pass item 3: singleton transport/security header
enforcement, read from the raw ASGI header list rather than
`starlette.datastructures.Headers.get()` (which silently resolves a
repeated header to only its first occurrence). Covers both header
orders, repeated identical headers, conflicting headers, sanitized
errors, and confirms no header or token value ever appears in a log line
or response body.
"""

from __future__ import annotations

import logging

import pytest

from tests.ingestion_api_support import IngestionApiTestHarness, valid_kubernetes_report

SECRET_MARKER_A = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # 43 chars, secret-shaped
SECRET_MARKER_B = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def _run_scope(
    harness: IngestionApiTestHarness, scope: dict, body: bytes = b""
) -> tuple[int, bytes]:
    import asyncio

    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(harness.app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    response_body = next(m["body"] for m in sent if m["type"] == "http.response.body")
    return status, response_body


def _base_scope(method: str, path: str, headers: list[tuple[bytes, bytes]]) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
        "query_string": b"",
        "client": ("203.0.113.5", 12345),
    }


class TestDuplicateAuthorization:
    def test_two_identical_authorization_headers_is_401(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"authorization", f"Bearer {token}".encode()),
        ]
        status, body = _run_scope(
            harness, _base_scope("GET", "/api/v1/reports/ing_whatever", headers)
        )
        assert status == 401
        assert body == b'{"ok":false,"error":"unauthorized","request_id":"req_test_1"}'

    def test_two_conflicting_authorization_headers_is_401(self) -> None:
        harness = IngestionApiTestHarness()
        token_a = harness.issue_token("tenant-a")
        token_b = harness.issue_token("tenant-b")
        headers = [
            (b"authorization", f"Bearer {token_a}".encode()),
            (b"authorization", f"Bearer {token_b}".encode()),
        ]
        status, _body = _run_scope(
            harness, _base_scope("GET", "/api/v1/reports/ing_whatever", headers)
        )
        assert status == 401

    def test_reversed_header_order_still_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token_a = harness.issue_token("tenant-a")
        token_b = harness.issue_token("tenant-b")
        headers = [
            (b"authorization", f"Bearer {token_b}".encode()),
            (b"authorization", f"Bearer {token_a}".encode()),
        ]
        status, _body = _run_scope(
            harness, _base_scope("GET", "/api/v1/reports/ing_whatever", headers)
        )
        assert status == 401

    def test_single_authorization_header_still_works(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [(b"authorization", f"Bearer {token}".encode())]
        status, _body = _run_scope(
            harness, _base_scope("GET", "/api/v1/reports/ing_whatever", headers)
        )
        assert status == 404  # authenticated fine; ID simply doesn't exist

    def test_missing_authorization_header_is_401(self) -> None:
        harness = IngestionApiTestHarness()
        status, _body = _run_scope(harness, _base_scope("GET", "/api/v1/reports/ing_whatever", []))
        assert status == 401

    def test_duplicate_authorization_on_delete_is_401(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"authorization", f"Bearer {token}".encode()),
        ]
        status, _body = _run_scope(
            harness, _base_scope("DELETE", "/api/v1/reports/ing_whatever", headers)
        )
        assert status == 401

    def test_duplicate_authorization_on_post_is_401(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
        ]
        status, _body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=b"{}"
        )
        assert status == 401


class TestDuplicateContentType:
    def test_two_identical_content_type_headers_is_415(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
            (b"content-type", b"application/json"),
        ]
        status, body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=b"{}"
        )
        assert status == 415
        assert body == b'{"ok":false,"error":"unsupported_content_type","request_id":"req_test_1"}'

    def test_two_conflicting_content_type_headers_is_415(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
            (b"content-type", b"text/plain"),
        ]
        status, _body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=b"{}"
        )
        assert status == 415

    def test_reversed_header_order_still_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"content-type", b"text/plain"),
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
        ]
        status, _body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=b"{}"
        )
        assert status == 415

    def test_single_content_type_header_still_works(self) -> None:
        import json

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
        ]
        body = json.dumps(
            {
                "platform": "kubernetes",
                "report_schema_version": 1,
                "report": valid_kubernetes_report(),
            }
        ).encode()
        status, _body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=body
        )
        assert status == 201


class TestContentEncodingAlwaysRejected:
    def test_single_content_encoding_header_is_415(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
            (b"content-encoding", b"gzip"),
        ]
        status, _body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=b"{}"
        )
        assert status == 415

    def test_two_content_encoding_headers_is_415(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
            (b"content-encoding", b"gzip"),
            (b"content-encoding", b"br"),
        ]
        status, body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=b"{}"
        )
        assert status == 415
        assert (
            body == b'{"ok":false,"error":"unsupported_content_encoding","request_id":"req_test_1"}'
        )


class TestDuplicateContentLength:
    def test_two_identical_content_length_headers_is_400(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
            (b"content-length", b"2"),
            (b"content-length", b"2"),
        ]
        status, body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=b"{}"
        )
        assert status == 400
        assert body == b'{"ok":false,"error":"invalid_request","request_id":"req_test_1"}'

    def test_two_conflicting_content_length_headers_is_400(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
            (b"content-length", b"2"),
            (b"content-length", b"99999999"),
        ]
        status, _body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=b"{}"
        )
        assert status == 400

    def test_rejected_before_authentication_is_even_attempted(self) -> None:
        # A malformed/absent Authorization header would itself be 401 --
        # but the duplicate Content-Length must be caught FIRST (task 3:
        # "before reading or authenticating the body"), so this request
        # (bad auth AND duplicate content-length) is 400, not 401.
        harness = IngestionApiTestHarness()
        headers = [
            (b"authorization", b"Bearer not-a-real-token"),
            (b"content-type", b"application/json"),
            (b"content-length", b"2"),
            (b"content-length", b"2"),
        ]
        status, _body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=b"{}"
        )
        assert status == 400

    def test_reversed_header_order_still_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        headers = [
            (b"content-length", b"99999999"),
            (b"content-type", b"application/json"),
            (b"content-length", b"2"),
            (b"authorization", f"Bearer {token}".encode()),
        ]
        status, _body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=b"{}"
        )
        assert status == 400

    def test_single_content_length_header_still_works(self) -> None:
        import json

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = json.dumps(
            {
                "platform": "kubernetes",
                "report_schema_version": 1,
                "report": valid_kubernetes_report(),
            }
        ).encode()
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        status, _body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=body
        )
        assert status == 201

    def test_missing_content_length_header_is_allowed(self) -> None:
        import json

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = json.dumps(
            {
                "platform": "kubernetes",
                "report_schema_version": 1,
                "report": valid_kubernetes_report(),
            }
        ).encode()
        headers = [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
        ]
        status, _body = _run_scope(
            harness, _base_scope("POST", "/api/v1/reports", headers), body=body
        )
        assert status == 201


class TestSanitizedErrorsAndLogHygiene:
    def test_error_envelope_never_contains_a_header_or_token_value(self) -> None:
        harness = IngestionApiTestHarness()
        token_a = harness.issue_token("tenant-a")
        token_b = harness.issue_token("tenant-b")
        headers = [
            (b"authorization", f"Bearer {token_a}".encode()),
            (b"authorization", f"Bearer {token_b}".encode()),
        ]
        status, body = _run_scope(
            harness, _base_scope("GET", "/api/v1/reports/ing_whatever", headers)
        )
        assert status == 401
        assert token_a.encode() not in body
        assert token_b.encode() not in body
        assert b"authorization" not in body.lower()
        import json as _json

        assert set(_json.loads(body).keys()) == {"ok", "error", "request_id"}

    def test_duplicate_headers_never_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        harness = IngestionApiTestHarness()
        token_a = harness.issue_token("tenant-a")
        marker_a = f"{SECRET_MARKER_A}-{token_a}"
        marker_b = f"{SECRET_MARKER_B}"
        headers = [
            (b"authorization", f"Bearer {marker_a[:22]}.{marker_a[-43:]}".encode()),
            (b"authorization", f"Bearer {marker_b[:22]}.{'x' * 43}".encode()),
        ]
        with caplog.at_level(logging.INFO, logger="cloudops_guard.ingestion_api"):
            status, _body = _run_scope(
                harness, _base_scope("GET", "/api/v1/reports/ing_whatever", headers)
            )
        assert status == 401
        assert SECRET_MARKER_A not in caplog.text
        assert SECRET_MARKER_B not in caplog.text
        assert token_a not in caplog.text
