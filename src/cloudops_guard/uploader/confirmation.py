"""Exact, case-sensitive confirmation policy for `cloudops-guard upload`.

Both `is_interactive`/`read_line` are injectable, defaulting to
`sys.stdin.isatty`/`input` respectively, so tests can deterministically
exercise every branch (accepted, rejected, EOF, Ctrl-C, noninteractive)
without depending on the real process's actual stdin -- see
`tests/test_uploader_confirmation.py`. Neither the default nor an
injected implementation is ever called by `--dry-run` or `--yes`
(`service.py` skips this module entirely on those paths).
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from .errors import ConfirmationAborted, NonInteractiveConfirmationRequired

CONFIRMATION_PHRASE = "UPLOAD"


def build_prompt(endpoint: str) -> str:
    # Correction pass, item 5: the milestone's exact specification is
    # `"Type UPLOAD to confirm sending this report to <endpoint>: "` --
    # one trailing space after the colon, which the original
    # implementation omitted.
    return f"Type UPLOAD to confirm sending this report to {endpoint}: "


def request_confirmation(
    endpoint: str,
    *,
    is_interactive: Callable[[], bool] | None = None,
    read_line: Callable[[str], str] | None = None,
) -> None:
    """Prints the exact required prompt and requires the exact,
    case-sensitive response `UPLOAD` -- no leading/trailing whitespace is
    ever trimmed into acceptance; ` UPLOAD`, `UPLOAD `, `upload`, and
    `Upload` are all rejected exactly like any other wrong input.

    Raises `NonInteractiveConfirmationRequired` immediately, **without
    ever calling `read_line`**, if `is_interactive()` is false -- this is
    what makes noninteractive stdin fail closed instead of risking a
    hang waiting for input that will never arrive. Raises
    `ConfirmationAborted` for a blank response, any other non-exact
    response, `EOFError` (stdin closed/exhausted), or `KeyboardInterrupt`
    (Ctrl-C) -- returns normally (no return value) only for the exact
    string `UPLOAD`. No network access happens in this function under
    any outcome.
    """
    interactive_check = is_interactive if is_interactive is not None else sys.stdin.isatty
    if not interactive_check():
        raise NonInteractiveConfirmationRequired(
            "stdin is not interactive; pass --yes or --dry-run for non-interactive use."
        )

    read = read_line if read_line is not None else input
    try:
        response = read(build_prompt(endpoint))
    except EOFError:
        raise ConfirmationAborted("confirmation aborted: end of input.") from None
    except KeyboardInterrupt:
        raise ConfirmationAborted("confirmation aborted: interrupted.") from None

    if response != CONFIRMATION_PHRASE:
        raise ConfirmationAborted("confirmation aborted: input did not exactly match UPLOAD.")
