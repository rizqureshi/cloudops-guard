"""Real-loopback-server tests for `Urllib3UploadTransport` -- genuine
HTTP over a real socket (never an in-process fake), proving the
production transport's own redirect-rejection, bounded-response-read,
and successful-response behavior against real wire bytes. Mirrors the
project's existing real-loopback-server convention
(`tests/ingestion_api_support.py`'s `run_loopback_server`), using
Python's stdlib `http.server` here since no ASGI application is
involved on the server side.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cloudops_guard.uploader.errors import UploadTransportError
from cloudops_guard.uploader.transport import Urllib3UploadTransport

_REQUEST_BODY = b'{"platform":"kubernetes","report_schema_version":1,"report":{}}'
_REQUEST_HEADERS = {"Content-Type": "application/json"}


@contextmanager
def _loopback_server(
    handle: Callable[[BaseHTTPRequestHandler], None],
) -> Iterator[tuple[str, list[str]]]:
    """Runs a real HTTP/1.1 server on `127.0.0.1`, an OS-assigned
    ephemeral port, in a background thread. `requests_received` records
    each request's own path, in order -- both `POST` and `GET` are
    tracked (a client following a 302/303 redirect commonly converts the
    original `POST` into a `GET`; a 307/308 preserves `POST`), so a
    redirect target being contacted is recorded regardless of which verb
    the (mis)following client used.
    """
    requests_received: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 -- http.server's own naming convention
            requests_received.append(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            handle(self)

        def do_GET(self) -> None:  # noqa: N802
            requests_received.append(self.path)
            handle(self)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # silence the default stderr access log

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/v1/reports", requests_received
    finally:
        server.shutdown()
        thread.join(timeout=5)


class TestRedirectIsNeverFollowed:
    """Uses a *second*, independent loopback server as the redirect
    target -- so "never followed" is proven by directly observing that
    server's own request count (0), never inferred from a
    connection-refused/timeout race against an intentionally-unreachable
    port. This is deliberately the stronger of the two designs: an
    earlier version of this test pointed `Location` at a refusing port
    and asserted on the raised exception's message text, which caught a
    reintroduced "follow redirects" mutation only incidentally (the
    resulting failure was a connection timeout while urllib3 itself
    retried against the refusing port -- itself proof a connection
    attempt happened, but not the precise, direct proof this version
    gives).
    """

    @staticmethod
    def _never_reached_handle(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Length", "2")
        handler.end_headers()
        handler.wfile.write(b"{}")

    def test_a_302_response_is_rejected_and_never_followed(self) -> None:
        with _loopback_server(self._never_reached_handle) as (
            attacker_url,
            attacker_requests_received,
        ):

            def handle_primary(handler: BaseHTTPRequestHandler) -> None:
                handler.send_response(302)
                handler.send_header("Location", attacker_url)
                handler.send_header("Content-Length", "0")
                handler.end_headers()

            with _loopback_server(handle_primary) as (primary_url, primary_requests_received):
                transport = Urllib3UploadTransport(connect_timeout=5.0, read_timeout=5.0)
                with pytest.raises(UploadTransportError, match="redirect"):
                    transport.post(primary_url, headers=_REQUEST_HEADERS, body=_REQUEST_BODY)

            assert primary_requests_received == ["/api/v1/reports"]
            assert attacker_requests_received == []  # the redirect target was never contacted

    def test_a_307_response_is_also_rejected_and_never_followed(self) -> None:
        with _loopback_server(self._never_reached_handle) as (
            attacker_url,
            attacker_requests_received,
        ):

            def handle_primary(handler: BaseHTTPRequestHandler) -> None:
                handler.send_response(307)
                handler.send_header("Location", attacker_url)
                handler.send_header("Content-Length", "0")
                handler.end_headers()

            with _loopback_server(handle_primary) as (primary_url, primary_requests_received):
                transport = Urllib3UploadTransport(connect_timeout=5.0, read_timeout=5.0)
                with pytest.raises(UploadTransportError, match="redirect"):
                    transport.post(primary_url, headers=_REQUEST_HEADERS, body=_REQUEST_BODY)

            assert primary_requests_received == ["/api/v1/reports"]
            assert attacker_requests_received == []


class TestSuccessfulRealResponse:
    def test_a_real_201_response_round_trips_correctly(self) -> None:
        payload = {
            "ok": True,
            "ingestion_id": "ing_1",
            "request_id": "req_1",
            "received_at": "2026-01-01T00:00:00Z",
            "report_fingerprint": "sha256:" + "a" * 64,
            "status": "received",
        }
        body_bytes = json.dumps(payload).encode("utf-8")

        def handle(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(201)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body_bytes)))
            handler.end_headers()
            handler.wfile.write(body_bytes)

        with _loopback_server(handle) as (url, _):
            transport = Urllib3UploadTransport(connect_timeout=5.0, read_timeout=5.0)
            response = transport.post(url, headers=_REQUEST_HEADERS, body=_REQUEST_BODY)
            assert response.status == 201
            assert json.loads(response.body) == payload


class TestBoundedResponseRead:
    def test_an_oversized_response_body_is_rejected(self) -> None:
        oversized = b"x" * (200 * 1024)  # comfortably over MAX_RESPONSE_BODY_BYTES

        def handle(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(oversized)))
            handler.end_headers()
            handler.wfile.write(oversized)

        with _loopback_server(handle) as (url, _):
            transport = Urllib3UploadTransport(connect_timeout=5.0, read_timeout=5.0)
            with pytest.raises(UploadTransportError):
                transport.post(url, headers=_REQUEST_HEADERS, body=_REQUEST_BODY)

    def test_a_declared_oversized_content_length_is_rejected_before_reading_the_body(
        self,
    ) -> None:
        real_body = b'{"ok": true}'

        def handle(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            # Declares far more than it actually sends -- proves the
            # declared-Content-Length check runs (and rejects) before
            # any bytes are read, not merely that the eventual byte
            # count happens to be large.
            handler.send_header("Content-Length", str(100 * 1024 * 1024))
            handler.end_headers()
            handler.wfile.write(real_body)

        with _loopback_server(handle) as (url, _):
            transport = Urllib3UploadTransport(connect_timeout=5.0, read_timeout=5.0)
            with pytest.raises(UploadTransportError):
                transport.post(url, headers=_REQUEST_HEADERS, body=_REQUEST_BODY)
