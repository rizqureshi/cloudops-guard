"""Exact confirmation-policy tests for
`cloudops_guard.uploader.confirmation`.
"""

from __future__ import annotations

import pytest

from cloudops_guard.uploader.confirmation import build_prompt, request_confirmation
from cloudops_guard.uploader.errors import ConfirmationAborted, NonInteractiveConfirmationRequired

ENDPOINT = "https://ingest.example.com/api/v1/reports"


def _interactive() -> bool:
    return True


class TestPromptText:
    def test_prompt_contains_the_exact_required_phrase_and_endpoint(self) -> None:
        # Correction pass, item 5: the milestone's exact specification is
        # `"Type UPLOAD to confirm sending this report to <endpoint>: "`
        # -- one trailing space after the colon.
        prompt = build_prompt(ENDPOINT)
        assert prompt == f"Type UPLOAD to confirm sending this report to {ENDPOINT}: "


class TestAcceptedInput:
    def test_exact_upload_is_accepted(self) -> None:
        # Must not raise.
        request_confirmation(ENDPOINT, is_interactive=_interactive, read_line=lambda p: "UPLOAD")


class TestRejectedInput:
    @pytest.mark.parametrize(
        "typed",
        [
            "upload",
            "Upload",
            "UPLOAD ",
            " UPLOAD",
            " UPLOAD ",
            "UPLOAD\t",
            "",
            "yes",
            "y",
            "UPLOAD!",
        ],
    )
    def test_wrong_input_is_rejected(self, typed: str) -> None:
        with pytest.raises(ConfirmationAborted):
            request_confirmation(ENDPOINT, is_interactive=_interactive, read_line=lambda p: typed)

    def test_eof_is_rejected(self) -> None:
        def raise_eof(prompt: str) -> str:
            raise EOFError

        with pytest.raises(ConfirmationAborted, match="end of input"):
            request_confirmation(ENDPOINT, is_interactive=_interactive, read_line=raise_eof)

    def test_keyboard_interrupt_is_rejected_not_propagated(self) -> None:
        def raise_interrupt(prompt: str) -> str:
            raise KeyboardInterrupt

        with pytest.raises(ConfirmationAborted, match="interrupted"):
            request_confirmation(ENDPOINT, is_interactive=_interactive, read_line=raise_interrupt)


class TestNonInteractive:
    def test_noninteractive_stdin_fails_closed_without_calling_read_line(self) -> None:
        calls: list[str] = []

        def spy_read_line(prompt: str) -> str:
            calls.append(prompt)
            return "UPLOAD"

        with pytest.raises(NonInteractiveConfirmationRequired):
            request_confirmation(ENDPOINT, is_interactive=lambda: False, read_line=spy_read_line)
        assert calls == []  # read_line must never be reached -- no risk of hanging
