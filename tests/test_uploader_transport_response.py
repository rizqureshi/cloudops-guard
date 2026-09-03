"""Transport/response-interpretation tests for
`cloudops_guard.uploader.response` (safe status/body handling) and
`cloudops_guard.uploader.transport` (redirect rejection, bounded reads,
exception classification) -- via a fake `UploadTransport` plus, for the
production transport's own internal behavior, direct unit tests against
`Urllib3UploadTransport`'s helper functions.
"""

from __future__ import annotations

import json

import pytest
import urllib3.exceptions as urllib3_exceptions

from cloudops_guard.uploader.errors import FingerprintMismatchError, UploadTransportError
from cloudops_guard.uploader.response import UploadOutcome, interpret_response
from cloudops_guard.uploader.transport import (
    MAX_RESPONSE_BODY_BYTES,
    TransportResponse,
    _classify_transport_exception,
    _finalize_response,
)

FINGERPRINT = "sha256:" + "a" * 64
OTHER_FINGERPRINT = "sha256:" + "b" * 64
VALID_INGESTION_ID = "ing_" + "0" * 32
VALID_REQUEST_ID = "req_" + "1" * 32
VALID_TIMESTAMP = "2026-01-01T00:00:00Z"


def _envelope(**fields: object) -> bytes:
    return json.dumps(fields).encode("utf-8")


def _success_envelope(**overrides: object) -> bytes:
    fields = {
        "ok": True,
        "ingestion_id": VALID_INGESTION_ID,
        "request_id": VALID_REQUEST_ID,
        "received_at": VALID_TIMESTAMP,
        "report_fingerprint": FINGERPRINT,
        "status": "received",
    }
    fields.update(overrides)
    return _envelope(**fields)


def _error_envelope(*, status: int, **overrides: object) -> bytes:
    code_by_status = {
        400: "invalid_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        413: "payload_too_large",
        415: "unsupported_content_type",
        429: "rate_limited",
        500: "internal_error",
    }
    fields = {"ok": False, "error": code_by_status[status], "request_id": VALID_REQUEST_ID}
    fields.update(overrides)
    return _envelope(**fields)


class TestSuccessResponses:
    def test_201_creation_with_matching_fingerprint(self) -> None:
        response = TransportResponse(status=201, body=_success_envelope())
        outcome = interpret_response(response, expected_fingerprint=FINGERPRINT)
        assert outcome == UploadOutcome(
            created=True,
            ingestion_id=VALID_INGESTION_ID,
            request_id=VALID_REQUEST_ID,
            status="received",
            report_fingerprint=FINGERPRINT,
        )

    def test_200_replay_with_matching_fingerprint(self) -> None:
        response = TransportResponse(status=200, body=_success_envelope())
        outcome = interpret_response(response, expected_fingerprint=FINGERPRINT)
        assert outcome.created is False

    def test_fingerprint_mismatch_is_never_reported_as_success(self) -> None:
        body = _success_envelope(report_fingerprint=OTHER_FINGERPRINT)
        response = TransportResponse(status=201, body=body)
        with pytest.raises(FingerprintMismatchError):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_success_response_that_is_not_a_json_object_is_an_error(self) -> None:
        response = TransportResponse(status=201, body=b"[1,2,3]")
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)


class TestDocumentedErrorStatuses:
    @pytest.mark.parametrize(
        "status,code",
        [
            (400, "invalid_request"),
            (401, "unauthorized"),
            (403, "forbidden"),
            (404, "not_found"),
            (405, "method_not_allowed"),
            (413, "payload_too_large"),
            (415, "unsupported_content_type"),
            (429, "rate_limited"),
            (500, "internal_error"),
        ],
    )
    def test_every_documented_error_status_is_handled_safely(self, status: int, code: str) -> None:
        response = TransportResponse(status=status, body=_error_envelope(status=status))
        with pytest.raises(UploadTransportError) as exc_info:
            interpret_response(response, expected_fingerprint=FINGERPRINT)
        message = str(exc_info.value)
        assert str(status) in message
        assert code in message
        assert VALID_REQUEST_ID in message

    def test_malformed_error_envelope_still_produces_a_safe_message(self) -> None:
        response = TransportResponse(status=400, body=b'{"unexpected": "shape"}')
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_422_is_not_a_status_the_real_contract_defines(self) -> None:
        # Correction pass, item 2: 422 is not in
        # ingestion_api.errors.HTTP_STATUS_BY_CODE at all -- reproduced
        # before this fix as silently accepted with an
        # "invalid_report"/422 pairing that does not exist anywhere in
        # the real server contract (invalid_report is actually 400).
        response = TransportResponse(
            status=422,
            body=_envelope(ok=False, error="invalid_report", request_id=VALID_REQUEST_ID),
        )
        with pytest.raises(UploadTransportError, match="unexpected HTTP status"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_wrong_error_code_for_the_returned_http_status_is_rejected(self) -> None:
        # A real, defined code ("unauthorized") but paired with a status
        # it does not actually correspond to (401's own status is 401,
        # not 400) -- must be rejected, not silently accepted just
        # because the code itself is individually valid.
        response = TransportResponse(
            status=400, body=_envelope(ok=False, error="unauthorized", request_id=VALID_REQUEST_ID)
        )
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)


class TestMalformedAndUnexpectedResponses:
    def test_non_json_body_is_rejected(self) -> None:
        response = TransportResponse(status=201, body=b"not json at all")
        with pytest.raises(UploadTransportError, match="not valid"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_invalid_utf8_body_is_rejected(self) -> None:
        response = TransportResponse(status=201, body=b"\xff\xfe")
        with pytest.raises(UploadTransportError, match="not valid"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_unexpected_status_is_rejected(self) -> None:
        response = TransportResponse(status=204, body=b"")
        with pytest.raises(UploadTransportError, match="unexpected HTTP status"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_never_echoes_raw_body_content_into_the_error_message(self) -> None:
        secret_shaped_body = b'{"unexpected": "AKIAFAKESENTINELVALUE12345"}'
        response = TransportResponse(status=400, body=secret_shaped_body)
        with pytest.raises(UploadTransportError) as exc_info:
            interpret_response(response, expected_fingerprint=FINGERPRINT)
        assert "AKIAFAKESENTINELVALUE12345" not in str(exc_info.value)


class TestAdversarialSuccessEnvelopes:
    """**Correction pass, item 2.** Every case here was independently
    reproduced as *accepted* (or as leaking a raw response value) before
    this fix -- each must now be rejected with the one fixed, generic
    message, never a status/error-shaped exception carrying any value
    from the response itself.
    """

    def test_duplicate_keys_are_rejected(self) -> None:
        raw = (
            b'{"ok":true,"ok":false,"ingestion_id":"' + VALID_INGESTION_ID.encode() + b'",'
            b'"request_id":"' + VALID_REQUEST_ID.encode() + b'",'
            b'"received_at":"' + VALID_TIMESTAMP.encode() + b'",'
            b'"report_fingerprint":"' + FINGERPRINT.encode() + b'","status":"received"}'
        )
        response = TransportResponse(status=201, body=raw)
        with pytest.raises(UploadTransportError, match="not valid"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_missing_ok_is_rejected(self) -> None:
        body = json.loads(_success_envelope())
        del body["ok"]
        response = TransportResponse(status=201, body=_envelope(**body))
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_missing_received_at_is_rejected(self) -> None:
        body = json.loads(_success_envelope())
        del body["received_at"]
        response = TransportResponse(status=201, body=_envelope(**body))
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_ok_false_on_a_success_status_is_rejected(self) -> None:
        response = TransportResponse(status=201, body=_success_envelope(ok=False))
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_ok_as_the_string_true_is_rejected(self) -> None:
        # bool is an int subclass in Python and JSON true/false decode to
        # Python bool -- but a STRING "true" must never be treated as
        # equivalent.
        response = TransportResponse(status=201, body=_success_envelope(ok="true"))
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_ok_as_integer_one_is_rejected(self) -> None:
        response = TransportResponse(status=201, body=_success_envelope(ok=1))
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_arbitrary_status_value_is_rejected(self) -> None:
        response = TransportResponse(status=201, body=_success_envelope(status="retired"))
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_unknown_extra_field_is_rejected(self) -> None:
        response = TransportResponse(
            status=201, body=_success_envelope(unexpected_field="anything")
        )
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    @pytest.mark.parametrize("field", ["ingestion_id", "request_id"])
    @pytest.mark.parametrize(
        "value",
        [
            "\x1b[31mnot a real id\x1b[0m",  # ANSI escape, wrong shape entirely
            "not-the-right-shape-at-all",
            "ing_" + "G" * 32,  # wrong charset (uppercase, non-hex)
            "ing_" + "0" * 31,  # one character short
            "ing_" + "0" * 33,  # one character too long
        ],
    )
    def test_invalid_identifier_formats_are_rejected(self, field: str, value: str) -> None:
        response = TransportResponse(status=201, body=_success_envelope(**{field: value}))
        with pytest.raises(UploadTransportError) as exc_info:
            interpret_response(response, expected_fingerprint=FINGERPRINT)
        assert "did not match the expected envelope" in str(exc_info.value)
        assert value not in str(exc_info.value)

    @staticmethod
    def _valid_identifier_for_field(field: str) -> str:
        return VALID_INGESTION_ID if field == "ingestion_id" else VALID_REQUEST_ID

    @pytest.mark.parametrize("field", ["ingestion_id", "request_id"])
    @pytest.mark.parametrize(
        "control_suffix",
        ["\n", "\r", "\t", "\x1b[31m", "\x00", "\x7f"],
    )
    def test_an_otherwise_valid_identifier_with_a_trailing_control_character_is_rejected(
        self, field: str, control_suffix: str
    ) -> None:
        # **Second correction pass, item 1.** The pre-fix `^...$` +
        # `.match()` regexes accepted a syntactically valid identifier
        # immediately followed by a single newline (`$` matches
        # immediately before a trailing `\n`, not only at the true end
        # of the string) -- reproduced and now closed by switching to
        # unanchored patterns + `.fullmatch()`. Every value here is
        # otherwise a genuinely, fully valid identifier for the field
        # under test (never a shape that was already wrong for some
        # other reason), with exactly one control character appended.
        value = self._valid_identifier_for_field(field) + control_suffix
        response = TransportResponse(status=201, body=_success_envelope(**{field: value}))
        with pytest.raises(UploadTransportError) as exc_info:
            interpret_response(response, expected_fingerprint=FINGERPRINT)
        message = str(exc_info.value)
        assert "did not match the expected envelope" in message
        assert value not in message
        assert control_suffix not in message

    @pytest.mark.parametrize("field", ["ingestion_id", "request_id"])
    @pytest.mark.parametrize("control_prefix", ["\n", "\r", "\t", "\x1b[31m"])
    def test_an_otherwise_valid_identifier_with_a_leading_control_character_is_rejected(
        self, field: str, control_prefix: str
    ) -> None:
        value = control_prefix + self._valid_identifier_for_field(field)
        response = TransportResponse(status=201, body=_success_envelope(**{field: value}))
        with pytest.raises(UploadTransportError) as exc_info:
            interpret_response(response, expected_fingerprint=FINGERPRINT)
        message = str(exc_info.value)
        assert "did not match the expected envelope" in message
        assert value not in message

    @pytest.mark.parametrize(
        "value",
        [
            "not-a-timestamp",
            "2026-01-01 00:00:00Z",  # space instead of T
            "2026-01-01T00:00:00+00:00",  # bare offset, not Z-suffixed
            "2026-01-01T00:00:00",  # missing timezone entirely
            "2026-01-01T00:00:00\x1bZ",  # embedded ANSI escape
            "2026-01-01T00:00:00Z" + "0" * 100,  # implausibly long
            # **Second correction pass, item 2**: `datetime.fromisoformat`
            # tolerates every one of these, but the real server's
            # `_format_timestamp` (`value.astimezone(dt.UTC).isoformat()`,
            # `"+00:00"` -> `"Z"`) never emits any of them -- each was
            # independently confirmed accepted before this fix.
            "20260101T000000Z",  # compact/basic ISO form, no separators
            "2026-W01-1T00:00:00Z",  # ISO week date
            "2026-01-01t00:00:00Z",  # lowercase t
            "2026-01-01T00:00:00z",  # lowercase z
            "2026-01-01T00:00Z",  # missing seconds
            "2026-02-30T00:00:00Z",  # invalid calendar date (Feb 30)
            "2026-13-01T00:00:00Z",  # invalid month
            "2026-01-32T00:00:00Z",  # invalid day
            "2026-01-01T25:00:00Z",  # invalid hour
            "2026-01-01T00:60:00Z",  # invalid minute
            "2026-01-01T00:00:60Z",  # invalid second
            "2026-01-01T00:00:00.123Z",  # 3 fractional digits, not 6
            "2026-01-01T00:00:00.1234567Z",  # 7 fractional digits, not 6
            "2026-01-01T00:00:00Z ",  # trailing whitespace
            "2026-01-01T00:00:00Z\n",  # trailing newline (item 1's same $-before-\n class of bug)
            " 2026-01-01T00:00:00Z",  # leading whitespace
        ],
    )
    def test_invalid_received_at_formats_are_rejected(self, value: str) -> None:
        response = TransportResponse(status=201, body=_success_envelope(received_at=value))
        with pytest.raises(UploadTransportError) as exc_info:
            interpret_response(response, expected_fingerprint=FINGERPRINT)
        assert "did not match the expected envelope" in str(exc_info.value)
        assert value not in str(exc_info.value)

    @pytest.mark.parametrize(
        "value",
        [
            "2026-01-01T00:00:00Z",  # no fractional seconds -- exact server shape
            "2026-01-01T00:00:00.123456Z",  # exact 6-digit fractional precision
            "2026-12-31T23:59:59Z",  # boundary calendar/time values, still valid
        ],
    )
    def test_valid_received_at_formats_matching_the_real_server_shape_are_accepted(
        self, value: str
    ) -> None:
        response = TransportResponse(status=201, body=_success_envelope(received_at=value))
        outcome = interpret_response(response, expected_fingerprint=FINGERPRINT)
        assert outcome.created is True

    def test_malformed_fingerprint_shape_is_rejected_before_mismatch_comparison(self) -> None:
        response = TransportResponse(
            status=201, body=_success_envelope(report_fingerprint="sha256:not-hex")
        )
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    @pytest.mark.parametrize("control_suffix", ["\n", "\r", "\t", "\x1b[31m", "\x00"])
    def test_an_otherwise_valid_fingerprint_with_a_trailing_control_character_is_rejected(
        self, control_suffix: str
    ) -> None:
        value = FINGERPRINT + control_suffix
        response = TransportResponse(status=201, body=_success_envelope(report_fingerprint=value))
        with pytest.raises(UploadTransportError) as exc_info:
            interpret_response(response, expected_fingerprint=FINGERPRINT)
        message = str(exc_info.value)
        assert "did not match the expected envelope" in message
        assert value not in message
        assert control_suffix not in message

    def test_wrong_type_for_every_field_is_rejected(self) -> None:
        for field in (
            "ingestion_id",
            "request_id",
            "received_at",
            "report_fingerprint",
            "status",
        ):
            response = TransportResponse(status=201, body=_success_envelope(**{field: 12345}))
            with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
                interpret_response(response, expected_fingerprint=FINGERPRINT)


class TestAdversarialErrorEnvelopes:
    def test_missing_ok_is_rejected(self) -> None:
        response = TransportResponse(
            status=400, body=_envelope(error="invalid_request", request_id=VALID_REQUEST_ID)
        )
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_ok_true_on_an_error_status_is_rejected(self) -> None:
        response = TransportResponse(
            status=400,
            body=_envelope(ok=True, error="invalid_request", request_id=VALID_REQUEST_ID),
        )
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_unknown_extra_field_is_rejected(self) -> None:
        response = TransportResponse(
            status=400,
            body=_envelope(
                ok=False,
                error="invalid_request",
                request_id=VALID_REQUEST_ID,
                extra="anything",
            ),
        )
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    def test_unknown_error_code_is_rejected(self) -> None:
        response = TransportResponse(
            status=400,
            body=_envelope(ok=False, error="totally_made_up_code", request_id=VALID_REQUEST_ID),
        )
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)

    @pytest.mark.parametrize(
        "value",
        [
            "\x1b[31minjected\x1b[0m",
            "not-the-right-shape",
        ],
    )
    def test_invalid_request_id_format_is_rejected(self, value: str) -> None:
        response = TransportResponse(
            status=400, body=_envelope(ok=False, error="invalid_request", request_id=value)
        )
        with pytest.raises(UploadTransportError) as exc_info:
            interpret_response(response, expected_fingerprint=FINGERPRINT)
        assert value not in str(exc_info.value)

    @pytest.mark.parametrize("control_suffix", ["\n", "\r", "\t", "\x1b[31m", "\x00"])
    def test_an_otherwise_valid_request_id_with_a_trailing_control_character_is_rejected(
        self, control_suffix: str
    ) -> None:
        # **Second correction pass, item 1**: the original test here used
        # 31 hex characters before the newline, so it failed because of
        # incorrect length rather than actually proving the newline
        # itself was rejected -- fixed to use the full, genuinely-valid
        # 32-character `VALID_REQUEST_ID`.
        value = VALID_REQUEST_ID + control_suffix
        response = TransportResponse(
            status=400, body=_envelope(ok=False, error="invalid_request", request_id=value)
        )
        with pytest.raises(UploadTransportError) as exc_info:
            interpret_response(response, expected_fingerprint=FINGERPRINT)
        message = str(exc_info.value)
        assert value not in message
        assert control_suffix not in message

    def test_very_long_error_code_is_rejected(self) -> None:
        response = TransportResponse(
            status=400,
            body=_envelope(ok=False, error="invalid_request" * 500, request_id=VALID_REQUEST_ID),
        )
        with pytest.raises(UploadTransportError, match="did not match the expected envelope"):
            interpret_response(response, expected_fingerprint=FINGERPRINT)


class TestTransportExceptionClassification:
    def test_dns_failure_is_classified(self) -> None:
        exc = urllib3_exceptions.NameResolutionError("bad.invalid", None, Exception("boom"))
        assert "DNS" in _classify_transport_exception(exc)

    def test_tls_failure_is_classified(self) -> None:
        assert "TLS" in _classify_transport_exception(urllib3_exceptions.SSLError("bad cert"))

    def test_connect_timeout_is_classified(self) -> None:
        assert "timed out" in _classify_transport_exception(
            urllib3_exceptions.ConnectTimeoutError("timeout")
        )

    def test_read_timeout_is_classified(self) -> None:
        assert "did not respond" in _classify_transport_exception(
            urllib3_exceptions.ReadTimeoutError(None, "/api/v1/reports", "timeout")
        )

    def test_generic_http_error_is_classified(self) -> None:
        assert _classify_transport_exception(urllib3_exceptions.HTTPError("boom"))

    def test_max_retry_error_is_unwrapped_to_its_reason(self) -> None:
        inner = urllib3_exceptions.SSLError("bad cert")
        wrapped = urllib3_exceptions.MaxRetryError(None, "/api/v1/reports", reason=inner)
        assert "TLS" in _classify_transport_exception(wrapped)


class TestResponseBoundedness:
    def test_max_response_body_bytes_is_bounded_and_small(self) -> None:
        # A sanity ceiling, not an exact value contract -- every
        # documented response body is a small, fixed envelope.
        assert 0 < MAX_RESPONSE_BODY_BYTES <= 1024 * 1024


_STREAM_SENTINEL = "RAW_STREAM_SENTINEL_" + ("Q" * 40)
_HEADERS_SENTINEL = "RAW_HEADERS_SENTINEL_" + ("Z" * 40)
_RELEASE_SENTINEL = "RAW_RELEASE_SENTINEL_" + ("X" * 40)


class _Headers:
    def __init__(self, *, raises: bool) -> None:
        self._raises = raises

    def get(self, name: str) -> str | None:
        if self._raises:
            raise RuntimeError(_HEADERS_SENTINEL)
        return None


class _FakeResponse:
    """A minimal duck-typed stand-in for `urllib3.HTTPResponse`, letting
    each of its three moving parts (`headers.get`, `stream`, `release_conn`)
    independently misbehave -- exactly the reproduction the correction
    pass names.
    """

    def __init__(
        self,
        *,
        status: int = 200,
        headers_raise: bool = False,
        stream_chunks: list[bytes] | None = None,
        stream_raises_after: int | None = None,
        stream_raises_immediately: bool = False,
        release_raises: bool = False,
    ) -> None:
        self.status = status
        self.headers = _Headers(raises=headers_raise)
        self._stream_chunks = stream_chunks if stream_chunks is not None else [b"{}"]
        self._stream_raises_after = stream_raises_after
        self._stream_raises_immediately = stream_raises_immediately
        self._release_raises = release_raises
        self.release_called = False

    def stream(self, chunk_size: int):  # noqa: ANN201 -- generator, matches urllib3's own duck type
        if self._stream_raises_immediately:
            raise RuntimeError(_STREAM_SENTINEL)
        for index, chunk in enumerate(self._stream_chunks):
            if self._stream_raises_after is not None and index == self._stream_raises_after:
                raise RuntimeError(_STREAM_SENTINEL)
            yield chunk

    def release_conn(self) -> None:
        self.release_called = True
        if self._release_raises:
            raise RuntimeError(_RELEASE_SENTINEL)


class TestResponseLifecycleSanitization:
    """**Correction pass, item 1.** Reproduced before fix: a
    `response.stream(...)` call that raises a native `RuntimeError`
    propagated that exact raw exception straight out of `post()` --
    `_finalize_response`'s own `try`/`except`/`finally` at the time
    covered only `response.release_conn()`, never the header-inspection/
    streaming step above it.
    """

    def test_stream_raising_immediately_is_sanitized(self) -> None:
        response = _FakeResponse(stream_raises_immediately=True)
        with pytest.raises(UploadTransportError) as exc_info:
            _finalize_response(response)
        assert _STREAM_SENTINEL not in str(exc_info.value)
        assert _STREAM_SENTINEL not in repr(exc_info.value)
        assert response.release_called is True  # cleanup still ran

    def test_stream_iterator_raising_after_one_chunk_is_sanitized(self) -> None:
        response = _FakeResponse(stream_chunks=[b"{", b"broken"], stream_raises_after=1)
        with pytest.raises(UploadTransportError) as exc_info:
            _finalize_response(response)
        assert _STREAM_SENTINEL not in str(exc_info.value)
        assert response.release_called is True

    def test_headers_get_raising_is_sanitized(self) -> None:
        response = _FakeResponse(headers_raise=True)
        with pytest.raises(UploadTransportError) as exc_info:
            _finalize_response(response)
        assert _HEADERS_SENTINEL not in str(exc_info.value)
        assert response.release_called is True

    def test_release_conn_raising_after_successful_read_is_sanitized(self) -> None:
        response = _FakeResponse(release_raises=True)
        with pytest.raises(UploadTransportError) as exc_info:
            _finalize_response(response)
        assert _RELEASE_SENTINEL not in str(exc_info.value)
        assert response.release_called is True

    def test_stream_and_release_both_raising_surfaces_only_the_stream_failure(
        self,
    ) -> None:
        # The primary (streaming) failure must win -- the cleanup
        # failure must never mask it, and must never itself leak.
        response = _FakeResponse(stream_raises_immediately=True, release_raises=True)
        with pytest.raises(UploadTransportError) as exc_info:
            _finalize_response(response)
        assert _STREAM_SENTINEL not in str(exc_info.value)
        assert _RELEASE_SENTINEL not in str(exc_info.value)
        assert response.release_called is True

    def test_a_deliberately_raised_uploadtransporterror_passes_through_unwrapped(
        self,
    ) -> None:
        # The redirect check inside _read_response_body raises
        # UploadTransportError directly -- it must never be re-wrapped
        # or its own message altered by the sanitizing boundary around it.
        response = _FakeResponse(status=302)
        with pytest.raises(UploadTransportError, match="redirect"):
            _finalize_response(response)

    def test_successful_response_still_releases_the_connection(self) -> None:
        response = _FakeResponse(status=201, stream_chunks=[b'{"ok":true}'])
        result = _finalize_response(response)
        assert result == TransportResponse(status=201, body=b'{"ok":true}')


_SECOND_STATUS_SENTINEL = "SECOND_STATUS_RAW_SENTINEL_" + ("Y" * 40)
_FIRST_STATUS_SENTINEL = "FIRST_STATUS_RAW_SENTINEL_" + ("W" * 40)


class _StatusAccessCountingResponse:
    """A duck-typed fake whose `.status` is a genuine Python `property`
    (never a plain attribute) so it can independently misbehave on the
    *first* vs. *second* access -- the exact distinction correction-pass
    item 3 requires: `_read_response_body`'s own single legitimate
    access must succeed, but any access beyond that one must never
    happen at all.
    """

    def __init__(
        self,
        *,
        first_status: object = 201,
        raise_on_first: bool = False,
        raise_on_second: bool = False,
        stream_chunks: list[bytes] | None = None,
        release_raises: bool = False,
    ) -> None:
        self._first_status = first_status
        self._raise_on_first = raise_on_first
        self._raise_on_second = raise_on_second
        self._access_count = 0
        self.headers = _Headers(raises=False)
        self._stream_chunks = stream_chunks if stream_chunks is not None else [b'{"ok":true}']
        self._release_raises = release_raises
        self.release_called = False

    @property
    def status(self) -> object:
        self._access_count += 1
        if self._access_count == 1:
            if self._raise_on_first:
                raise RuntimeError(_FIRST_STATUS_SENTINEL)
            return self._first_status
        if self._raise_on_second:
            raise RuntimeError(_SECOND_STATUS_SENTINEL)
        return self._first_status

    @property
    def access_count(self) -> int:
        return self._access_count

    def stream(self, chunk_size: int):  # noqa: ANN201 -- generator, matches urllib3's own duck type
        yield from self._stream_chunks

    def release_conn(self) -> None:
        self.release_called = True
        if self._release_raises:
            raise RuntimeError(_RELEASE_SENTINEL)


class TestStatusCaptureSanitization:
    """**Second correction pass, item 3.** `_finalize_response` used to
    build its `TransportResponse` with a *second*, unprotected
    `response.status` access, made after the sanitizing `try`/`except`/
    `finally` had already completed (and after `release_conn()` had
    already run) -- independently reproduced with a fake `.status`
    property returning `201` on its first access and raising
    `RuntimeError("SECOND_STATUS_RAW_SENTINEL...")` on its second,
    letting that raw `RuntimeError` escape `post()` entirely
    unsanitized. `_read_response_body` now captures `response.status`
    exactly once and returns it; `_finalize_response` never touches
    `response.status` again.
    """

    def test_status_raising_on_first_access_is_sanitized(self) -> None:
        response = _StatusAccessCountingResponse(raise_on_first=True)
        with pytest.raises(UploadTransportError) as exc_info:
            _finalize_response(response)
        assert _FIRST_STATUS_SENTINEL not in str(exc_info.value)
        assert _FIRST_STATUS_SENTINEL not in repr(exc_info.value)

    def test_status_raising_on_second_access_never_escapes_and_proves_no_second_access(
        self,
    ) -> None:
        response = _StatusAccessCountingResponse(first_status=201, raise_on_second=True)
        result = _finalize_response(response)
        # If `.status` had been accessed a second time, `raise_on_second`
        # would have fired and this call would have raised instead of
        # returning -- reaching here is itself the proof.
        assert result == TransportResponse(status=201, body=b'{"ok":true}')
        assert response.access_count == 1

    def test_status_is_accessed_exactly_once_on_every_path(self) -> None:
        for release_raises in (False, True):
            response = _StatusAccessCountingResponse(
                first_status=200, release_raises=release_raises
            )
            try:
                _finalize_response(response)
            except UploadTransportError:
                pass  # the release-failure path raises; still only one status access
            assert response.access_count == 1

    @pytest.mark.parametrize(
        "bad_status",
        [
            True,  # bool is an int subclass -- must never validate as 1
            False,
            "201",
            201.0,
            object(),
            None,
            99,  # below the real HTTP status range
            600,  # above the real HTTP status range
            -1,
        ],
    )
    def test_malformed_status_types_and_out_of_range_values_are_rejected(
        self, bad_status: object
    ) -> None:
        response = _StatusAccessCountingResponse(first_status=bad_status)
        with pytest.raises(UploadTransportError, match="malformed HTTP status"):
            _finalize_response(response)

    def test_cleanup_failure_combined_with_status_failure_never_leaks_either_sentinel(
        self,
    ) -> None:
        response = _StatusAccessCountingResponse(raise_on_first=True, release_raises=True)
        with pytest.raises(UploadTransportError) as exc_info:
            _finalize_response(response)
        assert _FIRST_STATUS_SENTINEL not in str(exc_info.value)
        assert _RELEASE_SENTINEL not in str(exc_info.value)
        assert response.release_called is True
        assert response.release_called is True
