"""`POST /api/v1/reports` tests (§E.2, §E.0, §D) -- authentication,
content-type/encoding, strict JSON decoding, closed-envelope validation,
report validation, and idempotency semantics.
"""

from __future__ import annotations

import json

import httpx

from cloudops_guard.ingestion.models import RetirementReason
from cloudops_guard.ingestion_api.fingerprint import compute_report_fingerprint
from tests.ingestion_api_support import (
    IngestionApiTestHarness,
    valid_gitlab_report,
    valid_kubernetes_report,
    with_client,
)


def _post(
    harness: IngestionApiTestHarness,
    *,
    token: str | None,
    body: bytes | None,
    content_type: str | None = "application/json",
    content_encoding: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if content_type is not None:
        headers["Content-Type"] = content_type
    if content_encoding is not None:
        headers["Content-Encoding"] = content_encoding
    if extra_headers:
        headers.update(extra_headers)

    async def _do(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post("/api/v1/reports", headers=headers, content=body)

    return with_client(harness, _do)


def _envelope(**overrides: object) -> dict:
    body = {
        "platform": "kubernetes",
        "report_schema_version": 1,
        "report": valid_kubernetes_report(),
    }
    body.update(overrides)
    return body


class TestSuccessfulIngestion:
    def test_new_ingestion_is_201_with_expected_fields(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        resp = _post(harness, token=token, body=json.dumps(_envelope()).encode())
        assert resp.status_code == 201
        body = resp.json()
        assert body["ok"] is True
        assert body["ingestion_id"].startswith("ing_")
        assert body["request_id"].startswith("req_")
        assert body["received_at"] == "2026-01-01T00:00:00Z"
        assert body["status"] == "received"
        expected_fingerprint = compute_report_fingerprint(
            "kubernetes", 1, valid_kubernetes_report()
        )
        assert body["report_fingerprint"] == expected_fingerprint

    def test_gitlab_report_is_accepted(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        envelope = {
            "platform": "gitlab",
            "report_schema_version": 1,
            "report": valid_gitlab_report(),
        }
        resp = _post(harness, token=token, body=json.dumps(envelope).encode())
        assert resp.status_code == 201
        assert resp.json()["status"] == "received"

    def test_report_bytes_are_persisted_via_the_blob_store(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        resp = _post(harness, token=token, body=json.dumps(_envelope()).encode())
        ingestion_id = resp.json()["ingestion_id"]
        storage_key = f"tenant-a/{ingestion_id}"
        stored = harness.blob_store.get(storage_key)
        assert stored is not None
        assert json.loads(stored) == valid_kubernetes_report()


class TestAuthentication:
    def test_missing_authorization_header_is_401(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _post(harness, token=None, body=json.dumps(_envelope()).encode())
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"

    def test_malformed_bearer_token_is_401(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _post(
            harness,
            token=None,
            body=json.dumps(_envelope()).encode(),
            extra_headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 401

    def test_revoked_token_is_401(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        # find and revoke the lookup_id
        lookup_id = token.split(".")[0]
        harness.token_store.mark_revoked(lookup_id)
        resp = _post(harness, token=token, body=json.dumps(_envelope()).encode())
        assert resp.status_code == 401

    def test_token_without_write_scope_is_403(self) -> None:
        from cloudops_guard.ingestion.models import TokenScope

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a", scopes=frozenset({TokenScope.REPORTS_READ}))
        resp = _post(harness, token=token, body=json.dumps(_envelope()).encode())
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_token_in_query_string_is_never_honored(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                f"/api/v1/reports?token={token}",
                headers={"Content-Type": "application/json"},
                content=json.dumps(_envelope()).encode(),
            )

        resp = with_client(harness, _do)
        assert resp.status_code == 401


class TestContentTypeAndEncoding:
    def test_missing_content_type_is_415(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        resp = _post(harness, token=token, body=json.dumps(_envelope()).encode(), content_type=None)
        assert resp.status_code == 415
        assert resp.json()["error"] == "unsupported_content_type"

    def test_parameterized_content_type_is_415(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        resp = _post(
            harness,
            token=token,
            body=json.dumps(_envelope()).encode(),
            content_type="application/json; charset=utf-8",
        )
        assert resp.status_code == 415

    def test_content_encoding_header_is_415(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        resp = _post(
            harness,
            token=token,
            body=json.dumps(_envelope()).encode(),
            content_encoding="gzip",
        )
        assert resp.status_code == 415
        assert resp.json()["error"] == "unsupported_content_encoding"

    def test_identity_content_encoding_is_also_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        resp = _post(
            harness,
            token=token,
            body=json.dumps(_envelope()).encode(),
            content_encoding="identity",
        )
        assert resp.status_code == 415


class TestStrictJsonDecoding:
    def test_malformed_json_is_400_invalid_request(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        resp = _post(harness, token=token, body=b"{not valid json")
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_duplicate_top_level_key_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = b'{"platform":"kubernetes","platform":"gitlab","report_schema_version":1,"report":{}}'
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_duplicate_nested_key_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = (
            b'{"platform":"kubernetes","report_schema_version":1,'
            b'"report":{"cluster_context":"a","cluster_context":"b"}}'
        )
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_bare_nan_literal_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = b'{"platform":"kubernetes","report_schema_version":NaN,"report":{}}'
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_bare_infinity_literal_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = b'{"platform":"kubernetes","report_schema_version":Infinity,"report":{}}'
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400

    def test_malformed_utf8_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = b'{"platform":"kubernetes","report_schema_version":1,"report":{"x":"\xff\xfe"}}'
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400

    def test_lone_surrogate_escape_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = b'{"platform":"kubernetes","report_schema_version":1,"report":{"x":"\\ud800"}}'
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_report_schema_version_as_numeric_string_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = b'{"platform":"kubernetes","report_schema_version":"1","report":{}}'
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_report_schema_version_as_boolean_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = b'{"platform":"kubernetes","report_schema_version":true,"report":{}}'
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400

    def test_report_schema_version_as_non_integral_float_is_rejected(self) -> None:
        # Correction-pass item 2: a JSON number with an INTEGER value
        # (e.g. `1.0`) is now accepted (see TestFloatSchemaVersionAccepted
        # below) -- only a genuinely fractional value like `1.5` is
        # rejected, since it can never represent a supported schema
        # version regardless of type.
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = b'{"platform":"kubernetes","report_schema_version":1.5,"report":{}}'
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_platform_as_number_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = b'{"platform":1,"report_schema_version":1,"report":{}}'
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400

    def test_wrong_case_platform_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = b'{"platform":"Kubernetes","report_schema_version":1,"report":{}}'
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400


class TestExcessiveNestingDepthViaPostEndpoint:
    """**Second correction pass, item 4**: the real, end-to-end
    `POST /api/v1/reports` behavior for a document deep enough to have
    previously let a bare `RecursionError` escape as an unsanitized
    `500 internal_error` -- must instead be a normal, sanitized
    `400 invalid_request`, before authentication succeeds or fingerprint
    computation is ever attempted.
    """

    def test_deeply_nested_top_level_document_is_400_not_500(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        raw = (b"[" * 1000) + b"1" + (b"]" * 1000)
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_deep_content_inside_an_ignored_extra_report_field_is_400_not_500(
        self,
    ) -> None:
        # The deep content sits inside a field the Kubernetes AuditReport
        # Pydantic model would ordinarily just ignore as unknown extra
        # data -- proving the depth ceiling is enforced over the WHOLE
        # decoded document, before Pydantic ever gets a chance to decide
        # what it would or would not have validated.
        import json as _json

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        deep = _json.loads((b"[" * 1000) + b"1" + (b"]" * 1000))
        report = valid_kubernetes_report()
        report["unexpected_deeply_nested_extra_field"] = deep
        body = _envelope(report=report)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"


class TestClosedEnvelope:
    def test_unknown_top_level_field_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope()
        body["extra_field"] = "x"
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_tenant_id_field_is_rejected_outright(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope()
        body["tenant_id"] = "tenant-b"
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400

    def test_customer_id_field_is_rejected_outright(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope()
        body["customer_id"] = "tenant-b"
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400

    def test_missing_platform_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope()
        del body["platform"]
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400

    def test_report_as_non_object_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(report="not-an-object")
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400

    def test_top_level_body_as_array_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        resp = _post(harness, token=token, body=b"[]")
        assert resp.status_code == 400

    def test_idempotency_key_over_200_chars_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(idempotency_key="x" * 201)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400

    def test_idempotency_key_of_exactly_200_chars_is_accepted(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(idempotency_key="x" * 200)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 201

    def test_idempotency_key_as_non_string_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(idempotency_key=12345)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400


class TestReportSchemaVersionSupport:
    def test_unsupported_schema_version_is_400(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(report_schema_version=999)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_report_schema_version"

    def test_error_response_never_names_the_supported_values(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(report_schema_version=999)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert set(resp.json().keys()) == {"ok", "error", "request_id"}

    def test_negative_schema_version_is_400(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(report_schema_version=-1)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_report_schema_version"


class TestReportValidation:
    def test_report_failing_platform_schema_is_400_invalid_report(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(report={"not": "a valid AuditReport"})
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_report"

    def test_platform_mismatch_with_report_shape_is_invalid_report(self) -> None:
        # platform says kubernetes, but the report body is shaped like a
        # GitLab report -- the server independently re-validates report
        # shape against the claimed platform, it never trusts platform
        # alone.
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(platform="kubernetes", report=valid_gitlab_report())
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_report"

    def test_summary_mismatch_is_rejected(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()
        report["summary"] = {"critical": 5, "high": 0, "medium": 0, "low": 0}
        body = _envelope(report=report)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_report"

    def test_findings_over_ceiling_is_rejected_as_invalid_report(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        finding_template = valid_kubernetes_report()["findings"][0]
        findings = [dict(finding_template) for _ in range(10_001)]
        report = valid_kubernetes_report(findings=findings)
        report["summary"] = {"critical": 0, "high": 10_001, "medium": 0, "low": 0}
        body = _envelope(report=report)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_report"

    def test_report_over_max_report_bytes_is_413(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        finding_template = valid_kubernetes_report()["findings"][0]
        oversized_evidence = "x" * (11 * 1024 * 1024)
        big_finding = dict(finding_template)
        big_finding["evidence"] = oversized_evidence
        report = valid_kubernetes_report(findings=[big_finding])
        report["summary"] = {"critical": 0, "high": 1, "medium": 0, "low": 0}
        body = _envelope(report=report)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 413
        assert resp.json()["error"] == "payload_too_large"

    def test_unknown_field_inside_report_is_rejected_by_existing_pydantic_model(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()
        report["findings"][0]["unexpected_extra_field"] = "x"
        body = _envelope(report=report)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        # Pydantic's existing, released model behavior for AuditReport /
        # Finding governs here -- this test documents whichever behavior
        # that already is (extra fields on Finding are, by default,
        # ignored by pydantic unless the model forbids them); it exists to
        # catch an accidental *change* to that behavior, not to assert a
        # specific tightening this phase never intended to add.
        assert resp.status_code in (201, 400)


class TestRequestBodySizeCeilings:
    def test_declared_content_length_over_wire_limit_is_413_with_zero_body_reads(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")

        class _SpyReceive:
            def __init__(self) -> None:
                self.call_count = 0

            async def __call__(self) -> dict:
                self.call_count += 1
                raise AssertionError(
                    "body must never be read when declared Content-Length is too large"
                )

        spy_receive = _SpyReceive()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/reports",
            "headers": [
                (b"authorization", f"Bearer {token}".encode()),
                (b"content-type", b"application/json"),
                (b"content-length", b"99999999"),
            ],
            "query_string": b"",
            "client": ("203.0.113.5", 12345),
        }
        sent: list[dict] = []

        async def send(message: dict) -> None:
            sent.append(message)

        import asyncio

        asyncio.run(harness.app(scope, spy_receive, send))
        assert spy_receive.call_count == 0
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        assert status == 413

    def test_actual_streamed_bytes_over_wire_limit_stop_at_first_over_limit_chunk(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")

        chunk = b"x" * (5 * 1024 * 1024)  # 5 MiB per chunk
        chunks_sent = {"count": 0}

        async def receive() -> dict:
            chunks_sent["count"] += 1
            if chunks_sent["count"] <= 3:
                return {"type": "http.request", "body": chunk, "more_body": True}
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/reports",
            "headers": [
                (b"authorization", f"Bearer {token}".encode()),
                (b"content-type", b"application/json"),
            ],
            "query_string": b"",
            "client": ("203.0.113.5", 12345),
        }
        sent: list[dict] = []

        async def send(message: dict) -> None:
            sent.append(message)

        import asyncio

        asyncio.run(harness.app(scope, receive, send))
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        assert status == 413
        # 10 MiB (2 chunks) is within MAX_REQUEST_BODY_BYTES; the 3rd
        # chunk pushes total over the limit and must be the last one
        # actually consumed -- a well-behaved implementation never keeps
        # reading further chunks once the limit is exceeded.
        assert chunks_sent["count"] == 3


class TestIdempotencySemantics:
    def test_identical_content_replay_without_key_is_200_same_id(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = json.dumps(_envelope()).encode()
        first = _post(harness, token=token, body=body)
        assert first.status_code == 201
        second = _post(harness, token=token, body=body)
        assert second.status_code == 200
        assert second.json()["ingestion_id"] == first.json()["ingestion_id"]

    def test_different_content_creates_a_distinct_ingestion(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        first = _post(harness, token=token, body=json.dumps(_envelope()).encode())
        other_report = valid_kubernetes_report()
        other_report["cluster_context"] = "staging"
        second_body = _envelope(report=other_report)
        second = _post(harness, token=token, body=json.dumps(second_body).encode())
        assert second.status_code == 201
        assert second.json()["ingestion_id"] != first.json()["ingestion_id"]

    def test_same_idempotency_key_same_fingerprint_is_replay(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(idempotency_key="key-1")
        first = _post(harness, token=token, body=json.dumps(body).encode())
        assert first.status_code == 201
        second = _post(harness, token=token, body=json.dumps(body).encode())
        assert second.status_code == 200
        assert second.json()["ingestion_id"] == first.json()["ingestion_id"]

    def test_same_key_different_fingerprint_is_400_invalid_request(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        first = _post(
            harness, token=token, body=json.dumps(_envelope(idempotency_key="key-1")).encode()
        )
        assert first.status_code == 201

        other_report = valid_kubernetes_report()
        other_report["cluster_context"] = "different"
        second_body = _envelope(report=other_report, idempotency_key="key-1")
        second = _post(harness, token=token, body=json.dumps(second_body).encode())
        assert second.status_code == 400
        assert second.json()["error"] == "invalid_request"

    def test_key_reused_after_24h_inclusive_boundary_is_still_a_replay(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(idempotency_key="key-1")
        first = _post(harness, token=token, body=json.dumps(body).encode())
        assert first.status_code == 201

        import datetime as dt

        harness.clock.advance(dt.timedelta(hours=24))  # exactly T + 24h, inclusive
        second = _post(harness, token=token, body=json.dumps(body).encode())
        assert second.status_code == 200
        assert second.json()["ingestion_id"] == first.json()["ingestion_id"]

    def test_key_boundary_isolated_from_content_dedup_still_replays_at_inclusive_boundary(
        self,
    ) -> None:
        # Isolates the key-binding boundary itself (as opposed to the
        # test above, where content-based dedup alone would already
        # explain the replay): different content on the second call, so
        # only the still-active key binding can cause a replay here.
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        first_body = _envelope(idempotency_key="key-1")
        first = _post(harness, token=token, body=json.dumps(first_body).encode())
        assert first.status_code == 201

        import datetime as dt

        harness.clock.advance(dt.timedelta(hours=24))  # exactly T + 24h, inclusive

        other_report = valid_kubernetes_report()
        other_report["cluster_context"] = "staging"
        second_body = _envelope(report=other_report, idempotency_key="key-1")
        second = _post(harness, token=token, body=json.dumps(second_body).encode())
        # The key binding is still active (inclusive boundary) and the
        # fingerprint differs from the bound record's -- per §E step 3,
        # this is a genuine conflict, not a replay.
        assert second.status_code == 400
        assert second.json()["error"] == "invalid_request"

    def test_key_reused_strictly_after_24h_window_creates_a_fresh_ingestion(self) -> None:
        # Different CONTENT on the second call (a different
        # cluster_context, hence a different fingerprint) is essential
        # here: content-based dedup (step 2 of §H's algorithm) has no
        # time limit at all as long as the original record stays
        # "received," so byte-identical content would still replay via
        # step 2 regardless of the idempotency-key window -- this test is
        # specifically isolating the *key*-binding expiry (step 3), which
        # only matters when the content itself does NOT already match.
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        first_body = _envelope(idempotency_key="key-1")
        first = _post(harness, token=token, body=json.dumps(first_body).encode())
        assert first.status_code == 201

        import datetime as dt

        harness.clock.advance(dt.timedelta(hours=24, microseconds=1))

        other_report = valid_kubernetes_report()
        other_report["cluster_context"] = "staging"
        second_body = _envelope(report=other_report, idempotency_key="key-1")
        second = _post(harness, token=token, body=json.dumps(second_body).encode())
        assert second.status_code == 201
        assert second.json()["ingestion_id"] != first.json()["ingestion_id"]

    def test_replay_after_retirement_creates_a_genuinely_new_record(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = json.dumps(_envelope()).encode()
        first = _post(harness, token=token, body=body)
        ingestion_id = first.json()["ingestion_id"]

        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        second = _post(harness, token=token, body=body)
        assert second.status_code == 201
        assert second.json()["ingestion_id"] != ingestion_id

    def test_dedup_is_tenant_scoped(self) -> None:
        harness = IngestionApiTestHarness()
        token_a = harness.issue_token("tenant-a")
        token_b = harness.issue_token("tenant-b")
        body = json.dumps(_envelope()).encode()
        first = _post(harness, token=token_a, body=body)
        second = _post(harness, token=token_b, body=body)
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["ingestion_id"] != second.json()["ingestion_id"]


class TestFloatSchemaVersionAccepted:
    """Correction-pass item 2: `report_schema_version: 1.0` is a JSON
    number with an integer value, and the approved contract accepts it,
    fingerprinting identically to `report_schema_version: 1`.
    """

    def test_report_schema_version_1_0_is_accepted(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body = _envelope(report_schema_version=1.0)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 201

    def test_report_schema_version_1_0_and_1_fingerprint_identically(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()

        int_body = _envelope(report_schema_version=1, report=report)
        int_resp = _post(harness, token=token, body=json.dumps(int_body).encode())
        assert int_resp.status_code == 201

        # A DIFFERENT tenant to avoid this second request being treated
        # as a content-based replay of the first (same fingerprint would
        # otherwise short-circuit to a 200, never exercising a second,
        # independent fingerprint computation).
        token_b = harness.issue_token("tenant-b")
        float_body = _envelope(report_schema_version=1.0, report=report)
        float_resp = _post(harness, token=token_b, body=json.dumps(float_body).encode())
        assert float_resp.status_code == 201

        assert int_resp.json()["report_fingerprint"] == float_resp.json()["report_fingerprint"]

    def test_report_schema_version_1_0_replays_against_an_existing_integer_ingestion(
        self,
    ) -> None:
        # Same tenant this time: 1 and 1.0 fingerprint identically, so a
        # second request with 1.0 against the same content must be
        # treated as a replay of the first (1) -- proving the dedup path
        # itself, not just the pure fingerprint function, treats them as
        # the same identity.
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()

        first = _post(
            harness,
            token=token,
            body=json.dumps(_envelope(report_schema_version=1, report=report)).encode(),
        )
        assert first.status_code == 201

        second = _post(
            harness,
            token=token,
            body=json.dumps(_envelope(report_schema_version=1.0, report=report)).encode(),
        )
        assert second.status_code == 200
        assert second.json()["ingestion_id"] == first.json()["ingestion_id"]


class TestNumericDomainRejection:
    """Correction-pass item 2: unsafe integers and non-finite numbers
    anywhere in the report must be rejected with the fixed sanitized
    `400 invalid_request` envelope -- never a `500 internal_error`.
    """

    def test_unsafe_integer_in_an_unvalidated_extra_field_is_400_not_500(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()
        report["findings"][0]["extra_unsafe_number"] = 2**53
        body = _envelope(report=report)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_negative_unsafe_integer_is_400_not_500(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()
        report["findings"][0]["extra_unsafe_number"] = -(2**53)
        body = _envelope(report=report)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_exponential_overflow_to_infinity_is_400_not_500(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()
        report["findings"][0]["extra_inf_marker"] = None
        raw_report = json.dumps(report).replace(
            '"extra_inf_marker": null', '"extra_inf_marker": 1e400'
        )
        raw = (
            b'{"platform":"kubernetes","report_schema_version":1,"report":'
            + raw_report.encode()
            + b"}"
        )
        resp = _post(harness, token=token, body=raw)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_unsafe_integer_nested_deep_inside_the_report_is_400_not_500(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()
        report["findings"][0]["nested_extra"] = {"deep": {"deeper": [1, 2, 2**53]}}
        body = _envelope(report=report)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_max_safe_integer_boundary_is_accepted(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()
        report["findings"][0]["extra_safe_number"] = 2**53 - 1
        body = _envelope(report=report)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 201

    def test_min_safe_integer_boundary_is_accepted(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()
        report["findings"][0]["extra_safe_number"] = -(2**53 - 1)
        body = _envelope(report=report)
        resp = _post(harness, token=token, body=json.dumps(body).encode())
        assert resp.status_code == 201


class TestAuthenticationBeforeBodyRead:
    """**Second correction pass, item 2**: `receive()` must never be
    called for `POST /api/v1/reports` when the request never
    authenticates -- a missing, malformed, duplicated, or invalid
    credential, a rate-limited caller, or one with insufficient scope
    must all be rejected *before* `read_bounded_body` ever awaits
    `request.stream()`. Each test below drives the real, raw ASGI `app`
    directly (mirroring `TestRequestBodySizeCeilings` above) with a spy
    `receive` that raises `AssertionError` the instant it is called --
    so a regression that moves body-reading back before authentication
    fails loudly and specifically, not merely via a wrong status code.
    """

    @staticmethod
    def _run(
        headers: list[tuple[bytes, bytes]], *, harness: IngestionApiTestHarness | None = None
    ) -> tuple[int, _SpyReceive]:
        import asyncio

        if harness is None:
            harness = IngestionApiTestHarness()

        spy_receive = _SpyReceive()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/reports",
            "headers": headers,
            "query_string": b"",
            "client": ("203.0.113.5", 12345),
        }
        sent: list[dict] = []

        async def send(message: dict) -> None:
            sent.append(message)

        asyncio.run(harness.app(scope, spy_receive, send))
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        return status, spy_receive

    def test_missing_credential_never_reads_body(self) -> None:
        status, spy = self._run(
            [
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
            ]
        )
        assert status == 401
        assert spy.call_count == 0

    def test_malformed_credential_never_reads_body(self) -> None:
        status, spy = self._run(
            [
                (b"authorization", b"NotBearer xyz"),
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
            ]
        )
        assert status == 401
        assert spy.call_count == 0

    def test_duplicated_credential_never_reads_body(self) -> None:
        status, spy = self._run(
            [
                (b"authorization", b"Bearer aaa.bbb"),
                (b"authorization", b"Bearer aaa.bbb"),
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
            ]
        )
        assert status == 401
        assert spy.call_count == 0

    def test_invalid_credential_never_reads_body(self) -> None:
        status, spy = self._run(
            [
                (b"authorization", b"Bearer unknown-lookup-id.some-secret"),
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
            ]
        )
        assert status == 401
        assert spy.call_count == 0

    def test_rate_limited_credential_never_reads_body(self) -> None:
        # Layer 2 (source-scoped) blocks map to the generic 401, not 429
        # (`AuthenticationCoordinator.authenticate` step 1: an
        # `AuthenticationFailed`, indistinguishable from any other
        # authentication failure by §G's own design) -- 429 is Layer 3
        # only, a legitimately-authenticated token that has exceeded its
        # own ordinary request-rate budget. Exhausting that budget before
        # this request still rejects strictly before `read_bounded_body`
        # (`_authenticate` raises `ApiError(RATE_LIMITED)` from within the
        # same pre-body-read offload).
        from cloudops_guard.ingestion.abuse_protection import token_scope_key

        harness = IngestionApiTestHarness(token_rate_threshold=1)
        token = harness.issue_token("tenant-a")
        lookup_id = token.split(".")[0]
        harness.token_rate_limiter.check_and_record_request(token_scope_key(lookup_id))

        status, spy = self._run(
            [
                (b"authorization", f"Bearer {token}".encode()),
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
            ],
            harness=harness,
        )
        assert status == 429
        assert spy.call_count == 0

    def test_insufficient_scope_credential_never_reads_body(self) -> None:
        from cloudops_guard.ingestion.models import TokenScope

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a", scopes=frozenset({TokenScope.REPORTS_READ}))

        status, spy = self._run(
            [
                (b"authorization", f"Bearer {token}".encode()),
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
            ],
            harness=harness,
        )
        assert status == 403
        assert spy.call_count == 0

    def test_valid_authenticated_request_still_reads_body_and_succeeds(self) -> None:
        # Control: proves the spy/harness plumbing itself is sound, and
        # that a genuinely valid request is unaffected by the reordering
        # -- `receive()` is called (to supply the body) and the request
        # still succeeds end to end.
        import asyncio

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        body_bytes = json.dumps(_envelope()).encode()

        class _BodyReceive:
            def __init__(self, body: bytes) -> None:
                self._body = body
                self.call_count = 0

            async def __call__(self) -> dict:
                self.call_count += 1
                return {"type": "http.request", "body": self._body, "more_body": False}

        receive = _BodyReceive(body_bytes)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/reports",
            "headers": [
                (b"authorization", f"Bearer {token}".encode()),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body_bytes)).encode()),
            ],
            "query_string": b"",
            "client": ("203.0.113.5", 12345),
        }
        sent: list[dict] = []

        async def send(message: dict) -> None:
            sent.append(message)

        asyncio.run(harness.app(scope, receive, send))
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        assert status == 201
        assert receive.call_count >= 1


class _SpyReceive:
    def __init__(self) -> None:
        self.call_count = 0

    async def __call__(self) -> dict:
        self.call_count += 1
        raise AssertionError("receive() must never be called before authentication succeeds")
