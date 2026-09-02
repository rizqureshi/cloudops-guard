"""Sanitized-logging tests (§C, task 14): captures actual emitted log
records for success, validation-failure, auth-failure, and internal-error
paths, and searches both the captured text and this module's own source
for distinctive injected secret/report markers that must never appear.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from cloudops_guard.ingestion_api.logging_utils import log_request_outcome
from tests.ingestion_api_support import (
    IngestionApiTestHarness,
    valid_kubernetes_report,
    with_client,
)

SECRET_MARKER = "SECRET-MARKER-do-not-log-me-9f8a7b6c"
REPORT_CONTENT_MARKER = "REPORT-CONTENT-MARKER-evidence-string-12345"


class TestLogRequestOutcomeAllowlist:
    def test_only_allowlisted_fields_are_ever_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cloudops_guard.ingestion_api"):
            log_request_outcome(
                request_id="req_1",
                http_status=201,
                latency_ms=12.5,
                ingestion_id="ing_1",
                tenant_id="tenant-a",
                report_fingerprint="sha256:" + "0" * 64,
                status="received",
                reason=None,
                byte_count=1234,
            )
        assert len(caplog.records) == 1
        fields = json.loads(caplog.records[0].message)
        assert set(fields.keys()) == {
            "request_id",
            "http_status",
            "latency_ms",
            "ingestion_id",
            "tenant_id",
            "report_fingerprint",
            "status",
            "byte_count",
        }

    def test_function_signature_has_no_kwargs_escape_hatch(self) -> None:
        import inspect

        signature = inspect.signature(log_request_outcome)
        kinds = {p.kind for p in signature.parameters.values()}
        assert inspect.Parameter.VAR_KEYWORD not in kinds
        assert inspect.Parameter.VAR_POSITIONAL not in kinds


class TestNoSecretOrReportContentInLogsAcrossRealRequests:
    def test_successful_post_never_logs_report_content_or_token(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        report = valid_kubernetes_report()
        report["findings"][0]["evidence"] = REPORT_CONTENT_MARKER

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                "/api/v1/reports",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(
                    {"platform": "kubernetes", "report_schema_version": 1, "report": report}
                ),
            )

        with caplog.at_level(logging.INFO, logger="cloudops_guard.ingestion_api"):
            resp = with_client(harness, _do)
        assert resp.status_code == 201

        log_text = caplog.text
        assert REPORT_CONTENT_MARKER not in log_text
        assert token not in log_text
        assert token.split(".")[1] not in log_text  # the secret half specifically

    def test_validation_failure_never_logs_report_content(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        bad_report = {"marker": REPORT_CONTENT_MARKER}

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                "/api/v1/reports",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(
                    {"platform": "kubernetes", "report_schema_version": 1, "report": bad_report}
                ),
            )

        with caplog.at_level(logging.INFO, logger="cloudops_guard.ingestion_api"):
            resp = with_client(harness, _do)
        assert resp.status_code == 400
        assert REPORT_CONTENT_MARKER not in caplog.text

    def test_auth_failure_never_logs_the_presented_token(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        harness = IngestionApiTestHarness()
        fake_token = f"{'a' * 22}.{SECRET_MARKER}{'b' * 20}"

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get(
                "/api/v1/reports/ing_whatever",
                headers={"Authorization": f"Bearer {fake_token}"},
            )

        with caplog.at_level(logging.INFO, logger="cloudops_guard.ingestion_api"):
            resp = with_client(harness, _do)
        assert resp.status_code == 401
        assert SECRET_MARKER not in caplog.text
        assert fake_token not in caplog.text

    def test_internal_error_never_logs_the_exception_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import dataclasses

        harness = IngestionApiTestHarness()

        class _ExplodingMetadataStore:
            def get(self, tenant_id: str, ingestion_id: str) -> None:
                raise RuntimeError(f"internal detail: {SECRET_MARKER}")

        from cloudops_guard.ingestion_api.app import create_app

        broken_config = dataclasses.replace(
            harness.config,
            metadata_store=_ExplodingMetadataStore(),  # type: ignore[arg-type]
        )
        harness.app = create_app(broken_config)
        token = harness.issue_token("tenant-a")

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get(
                "/api/v1/reports/ing_whatever", headers={"Authorization": f"Bearer {token}"}
            )

        with caplog.at_level(logging.INFO, logger="cloudops_guard.ingestion_api"):
            resp = with_client(harness, _do)
        assert resp.status_code == 500
        assert SECRET_MARKER not in caplog.text
        assert "RuntimeError" not in caplog.text


def test_logging_utils_has_no_free_form_extra_or_kwargs_parameter() -> None:
    """A cheap, direct source-level guard on `log_request_outcome`'s own
    parameter list specifically (never its prose docstring, which
    legitimately discusses tokens/report fields as things this function
    must *not* accept) -- catches a regression where someone widens the
    signature with a `**kwargs`/`extra` escape hatch instead of adding a
    new, deliberately-allowlisted parameter. The behavioral tests above
    are the stronger, direct proof that no secret/report content is
    actually logged; this is a narrower, purely structural regression
    guard on the function boundary itself.
    """
    import inspect

    source_lines = inspect.getsource(log_request_outcome).splitlines()
    signature_lines = source_lines[: source_lines.index(") -> None:") + 1]
    signature_text = "\n".join(signature_lines).lower()
    # "report_fingerprint" is a legitimately allowlisted parameter (an
    # opaque hash, not content) -- excluded from the "report"/"finding"
    # substring checks below by checking for "findings"/"evidence"
    # specifically rather than the bare, over-broad "report".
    forbidden_substrings = ["kwargs", "**", "extra", "findings", "evidence"]
    for forbidden in forbidden_substrings:
        assert forbidden not in signature_text, (
            f"log_request_outcome's signature unexpectedly contains {forbidden!r}"
        )
