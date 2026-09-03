"""Top-level orchestration for `cloudops-guard upload` -- the one place
that sequences local validation, the local summary, confirmation policy,
credential acquisition, and the network request, in the exact order the
privacy boundary requires. `cli.py`'s `upload` command is a thin wrapper
around `run_upload`: it owns only argument parsing, output formatting,
and exit-code mapping.

**Ordering is the whole point of this module**: `validate_endpoint` and
`load_and_validate_local_report` (and therefore `compute_report_fingerprint`)
always run first, and `format_local_summary`'s output is always printed,
*before* anything else. `--dry-run` returns immediately after that --
`load_ingestion_token`, `build_request_body`, and the injected
`UploadTransport` are never reached. Otherwise, `request_confirmation`
(skipped only when `yes=True`) runs next, and only once it -- or the
`--yes` flag -- has been satisfied does `load_ingestion_token` run,
followed by request construction and the actual network call. No
network access of any kind is possible before that point: nothing above
it in this function imports, constructs, or calls `transport.py`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .confirmation import request_confirmation
from .credentials import load_ingestion_token
from .endpoint import validate_endpoint
from .envelope import build_request_body
from .local_report import LocalReport, load_and_validate_local_report
from .response import UploadOutcome, interpret_response
from .summary import format_local_summary
from .transport import UploadTransport, Urllib3UploadTransport


@dataclass(frozen=True, slots=True)
class UploadResult:
    """The outcome of `run_upload`. `outcome` is `None` exactly when
    `dry_run` is `True` -- a dry run never contacts the network, so
    there is no server-verified outcome to report.
    """

    dry_run: bool
    local_report: LocalReport
    outcome: UploadOutcome | None


def run_upload(
    *,
    report_dir: Path,
    endpoint_raw: str,
    dry_run: bool,
    yes: bool,
    print_fn: Callable[[str], None] = print,
    env: Mapping[str, str] | None = None,
    transport: UploadTransport | None = None,
    is_interactive: Callable[[], bool] | None = None,
    read_line: Callable[[str], str] | None = None,
) -> UploadResult:
    """Runs the complete upload flow. `dry_run`/`yes` must not both be
    `True` -- enforced by the CLI layer before this function is ever
    called (kept as an explicit, defensive `AssertionError` here too,
    never silently resolved one way or the other).

    `print_fn`/`env`/`transport`/`is_interactive`/`read_line` are all
    injectable, defaulting to `print`/the real process environment/the
    production `Urllib3UploadTransport`/`sys.stdin.isatty`/`input`
    respectively -- tests inject fakes for every one of them (see
    `tests/test_uploader_zero_network.py`,
    `tests/test_uploader_credentials.py`,
    `tests/test_uploader_confirmation.py`).

    Raises `LocalReportError`, `EndpointValidationError`,
    `ConfirmationAborted`, `NonInteractiveConfirmationRequired`,
    `CredentialError`, `UploadTransportError`, or
    `FingerprintMismatchError` for the corresponding failure --
    `cli.py` catches all of these uniformly and exits non-zero.
    """
    assert not (dry_run and yes), "--dry-run and --yes are mutually exclusive."

    endpoint = validate_endpoint(endpoint_raw)
    local_report = load_and_validate_local_report(report_dir)
    print_fn(format_local_summary(local_report, endpoint))

    if dry_run:
        return UploadResult(dry_run=True, local_report=local_report, outcome=None)

    if not yes:
        request_confirmation(endpoint, is_interactive=is_interactive, read_line=read_line)

    token = load_ingestion_token(env)
    body = build_request_body(local_report.platform, local_report.report)
    active_transport = transport if transport is not None else Urllib3UploadTransport()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    response = active_transport.post(endpoint, headers=headers, body=body)
    outcome = interpret_response(response, expected_fingerprint=local_report.fingerprint)

    return UploadResult(dry_run=False, local_report=local_report, outcome=outcome)
