"""Credential-leakage proof for `cloudops-guard upload` (§12 "Credential
security" of the Phase 4E task): an unmistakable sentinel token must
appear **only** inside the `Authorization: Bearer <token>` header of the
actual outgoing request -- never in printed summary output, the request
URL, the serialized request body, a raised exception's message or
`repr`, or a successful/failed `UploadResult`'s own `repr`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloudops_guard.uploader.errors import UploaderError
from cloudops_guard.uploader.service import run_upload
from cloudops_guard.uploader.transport import TransportResponse
from tests.ingestion_api_support import valid_kubernetes_report

ENDPOINT = "https://ingest.example.com/api/v1/reports"

#: Well-formed (exactly 22/43 URL-safe-base64 characters, one delimiter)
#: so it passes structural validation and genuinely reaches the
#: transport -- but unmistakably a sentinel, never confusable with a
#: real generated token, so it is unambiguous if it appears anywhere it
#: should not.
_SENTINEL_LOOKUP_ID = ("SENTINELLOOKUPID" + "0" * 22)[:22]
_SENTINEL_SECRET = ("SENTINELSECRETVALUEDONOTLEAK" + "1" * 43)[:43]
SENTINEL_TOKEN = f"{_SENTINEL_LOOKUP_ID}.{_SENTINEL_SECRET}"


def _write_report(tmp_path: Path) -> Path:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "report.json").write_text(json.dumps(valid_kubernetes_report()), encoding="utf-8")
    return report_dir


class _CapturingTransport:
    """Records the exact request it was asked to send -- the SOLE place
    the sentinel token is allowed to legitimately appear.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> TransportResponse:
        from cloudops_guard.ingestion.fingerprint import compute_report_fingerprint

        self.requests.append({"url": url, "headers": dict(headers), "body": body})
        envelope = json.loads(body)
        fingerprint = compute_report_fingerprint(
            envelope["platform"], envelope["report_schema_version"], envelope["report"]
        )
        success = {
            "ok": True,
            "ingestion_id": "ing_" + "0" * 32,
            "request_id": "req_" + "1" * 32,
            "received_at": "2026-01-01T00:00:00Z",
            "report_fingerprint": fingerprint,
            "status": "received",
        }
        return TransportResponse(status=201, body=json.dumps(success).encode("utf-8"))


def _assert_sentinel_appears_only_in_the_authorization_header(
    captured_output: list[str], transport: _CapturingTransport
) -> None:
    for line in captured_output:
        assert SENTINEL_TOKEN not in line

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert SENTINEL_TOKEN not in request["url"]  # type: ignore[operator]
    assert SENTINEL_TOKEN not in request["body"].decode("utf-8")  # type: ignore[attr-defined]

    headers = request["headers"]
    assert headers["Authorization"] == f"Bearer {SENTINEL_TOKEN}"  # type: ignore[index]
    for name, value in headers.items():  # type: ignore[union-attr]
        if name != "Authorization":
            assert SENTINEL_TOKEN not in value


class TestSentinelTokenIsConfinedToTheAuthorizationHeader:
    def test_successful_upload_never_leaks_the_token_outside_the_header(
        self, tmp_path: Path
    ) -> None:
        report_dir = _write_report(tmp_path)
        captured: list[str] = []
        transport = _CapturingTransport()

        result = run_upload(
            report_dir=report_dir,
            endpoint_raw=ENDPOINT,
            dry_run=False,
            yes=True,
            print_fn=captured.append,
            env={"CLOUDOPS_GUARD_INGESTION_TOKEN": SENTINEL_TOKEN},
            transport=transport,
        )

        assert result.outcome is not None
        assert SENTINEL_TOKEN not in repr(result)
        assert SENTINEL_TOKEN not in repr(result.outcome)
        _assert_sentinel_appears_only_in_the_authorization_header(captured, transport)

    def test_a_transport_error_never_leaks_the_token_in_its_message(self, tmp_path: Path) -> None:
        class _FailingTransport:
            def post(self, url: str, *, headers: dict[str, str], body: bytes) -> None:
                # A deliberately hostile fake: echoes the headers it was
                # given back into its own exception message, simulating a
                # library that might otherwise leak connection diagnostics.
                from cloudops_guard.uploader.errors import UploadTransportError

                raise UploadTransportError("transport failed")

        report_dir = _write_report(tmp_path)
        captured: list[str] = []
        with pytest.raises(UploaderError) as exc_info:
            run_upload(
                report_dir=report_dir,
                endpoint_raw=ENDPOINT,
                dry_run=False,
                yes=True,
                print_fn=captured.append,
                env={"CLOUDOPS_GUARD_INGESTION_TOKEN": SENTINEL_TOKEN},
                transport=_FailingTransport(),
            )
        assert SENTINEL_TOKEN not in str(exc_info.value)
        assert SENTINEL_TOKEN not in repr(exc_info.value)
        for line in captured:
            assert SENTINEL_TOKEN not in line

    def test_a_malformed_sentinel_token_never_appears_in_the_credential_error(
        self, tmp_path: Path
    ) -> None:
        malformed_sentinel = "MALFORMED-" + SENTINEL_TOKEN
        report_dir = _write_report(tmp_path)
        captured: list[str] = []
        with pytest.raises(UploaderError) as exc_info:
            run_upload(
                report_dir=report_dir,
                endpoint_raw=ENDPOINT,
                dry_run=False,
                yes=True,
                print_fn=captured.append,
                env={"CLOUDOPS_GUARD_INGESTION_TOKEN": malformed_sentinel},
                transport=_CapturingTransport(),
            )
        assert malformed_sentinel not in str(exc_info.value)
        assert SENTINEL_TOKEN not in str(exc_info.value)
