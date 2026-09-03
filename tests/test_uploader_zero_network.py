"""Zero-network-before-confirmation guarantees for `cloudops-guard
upload` (§3/§12 "Zero-network guarantees" of the Phase 4E task).

Two independent layers of proof for every scenario that must never touch
the network: (1) an injected fake `UploadTransport` that raises
`AssertionError` if `.post()` is ever called, proving `service.run_upload`
itself never reaches the transport boundary; (2) a real socket/DNS
poison (`socket.socket.connect`/`socket.getaddrinfo` monkeypatched to
raise) proving nothing *else* -- not this package, not a library it
depends on -- opens a connection or resolves a hostname either.
`TestPoisonIsNonVacuous` proves the poison itself actually fires when a
real network attempt genuinely is made, so the "clean" results above
are not simply an inert monkeypatch.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from cloudops_guard.uploader.errors import (
    ConfirmationAborted,
    EndpointValidationError,
    LocalReportError,
    NonInteractiveConfirmationRequired,
)
from cloudops_guard.uploader.service import run_upload
from cloudops_guard.uploader.transport import Urllib3UploadTransport
from tests.ingestion_api_support import valid_kubernetes_report

ENDPOINT = "https://ingest.example.com/api/v1/reports"


class _ForbiddenTransport:
    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> None:
        raise AssertionError("the transport must never be reached in this scenario")


@pytest.fixture
def poisoned_sockets(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Monkeypatches socket connection/DNS at the lowest practical level
    -- any attempt to open a connection or resolve a hostname during the
    test appends a marker here (and raises, so the caller sees a loud
    failure too) instead of silently succeeding or hanging.
    """
    calls: list[str] = []

    def poisoned_connect(self: socket.socket, address: object) -> None:
        calls.append(f"connect:{address!r}")
        raise AssertionError(f"socket.connect must never be called in this scenario: {address!r}")

    def poisoned_getaddrinfo(*args: object, **kwargs: object) -> None:
        calls.append(f"getaddrinfo:{args!r}")
        raise AssertionError(f"socket.getaddrinfo must never be called in this scenario: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", poisoned_connect)
    monkeypatch.setattr(socket, "getaddrinfo", poisoned_getaddrinfo)
    return calls


def _write_report(tmp_path: Path) -> Path:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "report.json").write_text(json.dumps(valid_kubernetes_report()), encoding="utf-8")
    return report_dir


class TestNoNetworkBeforeConfirmation:
    def test_dry_run_never_reaches_the_transport(self, tmp_path: Path, poisoned_sockets) -> None:
        report_dir = _write_report(tmp_path)
        result = run_upload(
            report_dir=report_dir,
            endpoint_raw=ENDPOINT,
            dry_run=True,
            yes=False,
            print_fn=lambda _: None,
            transport=_ForbiddenTransport(),
        )
        assert result.dry_run is True
        assert poisoned_sockets == []

    def test_rejected_confirmation_never_reaches_the_transport(
        self, tmp_path: Path, poisoned_sockets
    ) -> None:
        report_dir = _write_report(tmp_path)
        with pytest.raises(ConfirmationAborted):
            run_upload(
                report_dir=report_dir,
                endpoint_raw=ENDPOINT,
                dry_run=False,
                yes=False,
                print_fn=lambda _: None,
                transport=_ForbiddenTransport(),
                is_interactive=lambda: True,
                read_line=lambda prompt: "not-upload",
            )
        assert poisoned_sockets == []

    def test_eof_never_reaches_the_transport(self, tmp_path: Path, poisoned_sockets) -> None:
        def raise_eof(prompt: str) -> str:
            raise EOFError

        report_dir = _write_report(tmp_path)
        with pytest.raises(ConfirmationAborted):
            run_upload(
                report_dir=report_dir,
                endpoint_raw=ENDPOINT,
                dry_run=False,
                yes=False,
                print_fn=lambda _: None,
                transport=_ForbiddenTransport(),
                is_interactive=lambda: True,
                read_line=raise_eof,
            )
        assert poisoned_sockets == []

    def test_keyboard_interrupt_never_reaches_the_transport(
        self, tmp_path: Path, poisoned_sockets
    ) -> None:
        def raise_interrupt(prompt: str) -> str:
            raise KeyboardInterrupt

        report_dir = _write_report(tmp_path)
        with pytest.raises(ConfirmationAborted):
            run_upload(
                report_dir=report_dir,
                endpoint_raw=ENDPOINT,
                dry_run=False,
                yes=False,
                print_fn=lambda _: None,
                transport=_ForbiddenTransport(),
                is_interactive=lambda: True,
                read_line=raise_interrupt,
            )
        assert poisoned_sockets == []

    def test_noninteractive_failure_never_reaches_the_transport(
        self, tmp_path: Path, poisoned_sockets
    ) -> None:
        report_dir = _write_report(tmp_path)
        with pytest.raises(NonInteractiveConfirmationRequired):
            run_upload(
                report_dir=report_dir,
                endpoint_raw=ENDPOINT,
                dry_run=False,
                yes=False,
                print_fn=lambda _: None,
                transport=_ForbiddenTransport(),
                is_interactive=lambda: False,
                read_line=lambda prompt: "UPLOAD",
            )
        assert poisoned_sockets == []

    def test_local_validation_failure_never_reaches_the_transport(
        self, tmp_path: Path, poisoned_sockets
    ) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        (report_dir / "report.json").write_bytes(b"{not valid json")
        with pytest.raises(LocalReportError):
            run_upload(
                report_dir=report_dir,
                endpoint_raw=ENDPOINT,
                dry_run=False,
                yes=True,
                print_fn=lambda _: None,
                transport=_ForbiddenTransport(),
            )
        assert poisoned_sockets == []

    def test_malformed_endpoint_never_reaches_confirmation_credentials_or_transport(
        self, tmp_path: Path, poisoned_sockets
    ) -> None:
        # **Correction, hexadecimal IPv4 notation**: `validate_endpoint`
        # runs first in `run_upload`, before local report loading,
        # confirmation, credential access, or the transport -- proven
        # here specifically for the new legacy-numeric-IPv4 rejection
        # (`0x7f.0.0.1`), not merely structurally implied by call order.
        # `is_interactive`/`read_line` both raise if ever called, so
        # confirmation is proven unreached, not merely unobserved.
        def forbidden_is_interactive() -> bool:
            raise AssertionError("confirmation must never be reached for a malformed endpoint.")

        def forbidden_read_line(prompt: str) -> str:
            raise AssertionError("confirmation must never be reached for a malformed endpoint.")

        with pytest.raises(EndpointValidationError):
            run_upload(
                report_dir=tmp_path / "does-not-need-to-exist",
                endpoint_raw="https://0x7f.0.0.1/api/v1/reports",
                dry_run=False,
                yes=False,
                print_fn=lambda _: None,
                transport=_ForbiddenTransport(),
                is_interactive=forbidden_is_interactive,
                read_line=forbidden_read_line,
            )
        assert poisoned_sockets == []

    def test_producing_the_summary_itself_never_touches_the_network(
        self, tmp_path: Path, poisoned_sockets
    ) -> None:
        # The summary is printed as part of every non-error path, always
        # before confirmation -- captured here via dry-run, which is the
        # only mode guaranteed to stop immediately afterward.
        report_dir = _write_report(tmp_path)
        captured: list[str] = []
        run_upload(
            report_dir=report_dir,
            endpoint_raw=ENDPOINT,
            dry_run=True,
            yes=False,
            print_fn=captured.append,
            transport=_ForbiddenTransport(),
        )
        assert any("upload summary" in line for line in captured)
        assert poisoned_sockets == []


class TestCredentialsNeverReadBeforeAuthorization:
    def test_dry_run_never_reads_the_credential_env_var(self, tmp_path: Path) -> None:
        class _SpyEnv(dict):
            def get(self, key, default=None):  # type: ignore[override]
                if key == "CLOUDOPS_GUARD_INGESTION_TOKEN":
                    raise AssertionError("dry-run must never read the credential")
                return super().get(key, default)

        report_dir = _write_report(tmp_path)
        result = run_upload(
            report_dir=report_dir,
            endpoint_raw=ENDPOINT,
            dry_run=True,
            yes=False,
            print_fn=lambda _: None,
            env=_SpyEnv(),
            transport=_ForbiddenTransport(),
        )
        assert result.dry_run is True

    def test_rejected_confirmation_never_reads_the_credential_env_var(self, tmp_path: Path) -> None:
        class _SpyEnv(dict):
            def get(self, key, default=None):  # type: ignore[override]
                if key == "CLOUDOPS_GUARD_INGESTION_TOKEN":
                    raise AssertionError("a rejected confirmation must never read the credential")
                return super().get(key, default)

        report_dir = _write_report(tmp_path)
        with pytest.raises(ConfirmationAborted):
            run_upload(
                report_dir=report_dir,
                endpoint_raw=ENDPOINT,
                dry_run=False,
                yes=False,
                print_fn=lambda _: None,
                env=_SpyEnv(),
                transport=_ForbiddenTransport(),
                is_interactive=lambda: True,
                read_line=lambda prompt: "no",
            )


class TestPoisonIsNonVacuous:
    def test_the_socket_poison_actually_fires_for_a_real_dns_lookup(self, poisoned_sockets) -> None:
        # The most direct possible proof, independent of urllib3's own
        # exception handling (Urllib3UploadTransport.post() deliberately
        # catches every native exception and reclassifies it -- see
        # transport.py -- so asserting against the *raw* AssertionError
        # through that path would be testing the wrong layer). This
        # confirms the exact primitive poisoned_sockets patches actually
        # raises when called directly.
        with pytest.raises(AssertionError, match="getaddrinfo must never be called"):
            socket.getaddrinfo("ingest.example.invalid", 443)

    def test_a_real_transport_attempt_trips_the_poison_and_is_reclassified_safely(
        self, poisoned_sockets
    ) -> None:
        # Proves poisoned_sockets is not an inert monkeypatch for the
        # actual production transport: a genuine attempt to reach the
        # network trips the poison (recorded in poisoned_sockets, which
        # is populated before the poison itself raises), and
        # Urllib3UploadTransport.post() -- which never lets a native
        # exception escape uncaught -- surfaces it as a sanitized
        # UploadTransportError rather than a raw AssertionError.
        from cloudops_guard.uploader.errors import UploadTransportError

        transport = Urllib3UploadTransport(connect_timeout=1.0, read_timeout=1.0)
        with pytest.raises(UploadTransportError):
            transport.post(
                "https://ingest.example.invalid/api/v1/reports",
                headers={"Content-Type": "application/json"},
                body=b'{"platform":"kubernetes","report_schema_version":1,"report":{}}',
            )
        assert poisoned_sockets != []
