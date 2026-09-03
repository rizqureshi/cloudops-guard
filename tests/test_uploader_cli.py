"""CLI-level tests for `cloudops-guard upload`, via Typer's `CliRunner`
-- option parsing, help text, exit-code stability, and the wiring
between `cli.py` and `uploader.service.run_upload`. Confirmation-branch
coverage (accepted/rejected/EOF/Ctrl-C/case/whitespace) lives in
`tests/test_uploader_confirmation.py`, exercised directly against
`confirmation.request_confirmation` -- `CliRunner`'s own simulated stdin
is never a real TTY, so it cannot itself exercise the interactive-accept
path (see `tests/test_uploader_zero_network.py`'s and
`test_uploader_confirmation.py`'s own use of `service.run_upload`'s
injectable `is_interactive`/`read_line` for that).

**CI-restoration correction.** `TestHelp.test_upload_help_documents_every_option`
used to assert plain substrings (e.g. `"--report-dir" in result.stdout`)
against the raw, rendered `--help` text. That passed reliably locally
(macOS, Python 3.13, an interactive terminal) but failed on the CI
runner (Ubuntu, Python 3.12, no real terminal) with `'--report-dir' in
result.stdout` false. Root cause, independently reproduced locally
under the project's own locked Typer/Click/Rich versions (`typer
0.27.0`/`click 8.5.0`/`rich 15.0.0`) by explicitly setting
`FORCE_COLOR=1` (which reproduces byte-for-byte the same
`\x1b[1m`-prefixed ANSI pattern seen in the CI failure output -- the
exact environment variable GitHub Actions itself sets to enable color
was not independently identified, but the reproduced rendering is a
byte-for-byte match): whenever color output is active, Rich's syntax
highlighter renders an option name as *multiple separately-styled
spans* rather than one contiguous run of characters -- `--report-dir`
literally becomes `\x1b[1;36m-\x1b[0m\x1b[1;36m-report\x1b[0m\x1b[1;36m-dir\x1b[0m`
(three separate ANSI-wrapped fragments: `-`, `-report`, `-dir`), so the
literal substring `--report-dir` never appears contiguously in the raw
captured string at all -- not merely obscured by escape codes
*around* it, but genuinely interrupted *inside* it. This is
independent of terminal width: the CI failure's own captured output
shows a full 80-column-wide box (Click's own non-TTY fallback width),
not a narrow one. A separate, narrower-width failure mode was also
investigated: below approximately 42-44 columns, Rich truncates the
option name itself with an ellipsis (`--repor…`) -- a distinct, correct
Rich behavior at genuinely tiny widths that no test-side normalization
could or should recover from, and confirmed *not* the mechanism behind
the actual CI failure (see `TestRenderedHelpText` below for the width
this suite actually exercises).

The fix is two-layered: (1) `TestUploadCommandOptionMetadata` inspects
the real `click.Option` objects Typer registers for `upload` directly
from the actual application object (`typer.main.get_command(app)`) --
entirely independent of how any terminal renders `--help`, and the
primary source of truth for "is this option registered" from here on;
(2) `TestRenderedHelpText` retains coverage that `--help` still renders
successfully and mentions every option to a real user, but normalizes
the captured text with `click.unstyle()` (removing the ANSI spans
described above, which re-joins `--report-dir`'s three fragments back
into one contiguous string) followed by whitespace collapsing (which
additionally tolerates Rich wrapping a long description across
multiple lines/columns) before checking substrings -- run across
several terminal widths, including one with forced color output
(reproducing the exact CI failure mechanism) and a narrow-but-not-
truncating width, rather than assuming any single raw byte sequence.

**Implementation note, discovered while building
`TestUploadCommandOptionMetadata`**: the project's locked `typer==0.27.0`
vendors its own internal Click fork (`typer._click`) -- `TyperCommand`/
`TyperOption`, the real runtime types Typer builds its command tree
from, inherit from `typer._click.core.Command`/`Parameter`, **not**
from the separately pip-installed `click` package's own
`click.core.Command`/`Option` classes (confirmed directly: `isinstance(
upload_command, click.Command)` is `False`, even though `upload_command`
is a completely real, unmodified object Typer itself constructed).
Metadata-based isinstance checks below therefore use `typer.core.
TyperCommand`/`TyperOption` -- Typer's own public, documented types for
exactly this purpose -- rather than the top-level `click` package,
which remains used only for `click.unstyle()` (a plain string
function, unaffected by this type-hierarchy split) in the
rendered-output tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
from typer.core import TyperCommand, TyperOption
from typer.main import get_command
from typer.testing import CliRunner

from cloudops_guard.cli import app
from tests.ingestion_api_support import valid_gitlab_report, valid_kubernetes_report

runner = CliRunner()

ENDPOINT = "https://ingest.example.com/api/v1/reports"


def _write_report(tmp_path: Path, report: dict) -> Path:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return report_dir


def _get_upload_command() -> TyperCommand:
    """The real command Typer generates for `upload`, obtained from the
    actual application object -- never a hand-written stand-in that
    could pass without the real app registering anything.
    """
    root = get_command(app)
    upload_command = root.commands.get("upload")  # type: ignore[attr-defined]
    assert upload_command is not None, "the real Typer app has no 'upload' command"
    return upload_command


def _option_by_long_flag(command: TyperCommand, flag: str) -> TyperOption:
    for param in command.params:
        if isinstance(param, TyperOption) and flag in param.opts:
            return param
    raise AssertionError(f"{flag!r} is not a registered option on the real upload command")


def _normalize_rendered_help(raw: str) -> str:
    """`click.unstyle()` removes ANSI/terminal styling -- critically,
    this is what re-joins an option name Rich has split into multiple
    separately-styled spans (see module docstring) back into one
    contiguous run of characters, not merely a cosmetic cleanup.
    Collapsing all remaining whitespace runs into single spaces
    additionally tolerates Rich wrapping a long line across multiple
    columns. Never attempts to recover text Rich has genuinely
    ellipsis-truncated at an extremely narrow width -- that content is
    not present in the raw output at all, so no normalization can
    reconstruct it.
    """
    return " ".join(click.unstyle(raw).split())


class TestUploadCommandOptionMetadata:
    """Inspects the real option objects Typer registers for `upload`
    directly, independent of `--help` rendering in any terminal width
    or color mode -- the primary, rendering-independent proof that
    every option is actually registered. Uses `typer.core.TyperCommand`/
    `TyperOption` for isinstance checks, not the top-level `click`
    package -- see module docstring for why.
    """

    def test_upload_command_is_registered_on_the_real_app(self) -> None:
        assert isinstance(_get_upload_command(), TyperCommand)

    def test_every_expected_long_option_is_registered(self) -> None:
        command = _get_upload_command()
        registered = {
            opt for param in command.params if isinstance(param, TyperOption) for opt in param.opts
        }
        for flag in ("--report-dir", "--endpoint", "--dry-run", "--yes"):
            assert flag in registered

    def test_report_dir_is_a_required_option_with_no_default_or_envvar(self) -> None:
        option = _option_by_long_flag(_get_upload_command(), "--report-dir")
        assert option.required is True
        assert option.default is None
        assert option.envvar is None
        assert option.hidden is False
        assert option.help

    def test_endpoint_is_required_and_reads_the_documented_environment_variable(self) -> None:
        option = _option_by_long_flag(_get_upload_command(), "--endpoint")
        assert option.required is True
        assert option.default is None
        assert option.envvar == "CLOUDOPS_GUARD_INGESTION_URL"
        assert option.hidden is False
        assert option.help

    def test_dry_run_is_an_optional_flag_defaulting_to_false(self) -> None:
        option = _option_by_long_flag(_get_upload_command(), "--dry-run")
        assert option.required is False
        assert option.default is False
        assert option.is_flag is True
        assert option.envvar is None
        assert option.hidden is False
        assert option.help

    def test_yes_is_an_optional_flag_defaulting_to_false(self) -> None:
        option = _option_by_long_flag(_get_upload_command(), "--yes")
        assert option.required is False
        assert option.default is False
        assert option.is_flag is True
        assert option.envvar is None
        assert option.hidden is False
        assert option.help

    def test_no_registered_option_exposes_a_token_flag(self) -> None:
        command = _get_upload_command()
        long_flags = {
            opt for param in command.params if isinstance(param, TyperOption) for opt in param.opts
        }
        assert not any("token" in flag.lower() for flag in long_flags)


class TestRenderedHelpText:
    """Rendered-output coverage: `--help` still exits successfully and
    every option is still visible to a real user, normalized against
    terminal-rendering variation (color/styling and line-wrapping)
    rather than assuming any particular raw byte sequence.
    """

    def test_upload_appears_in_top_level_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "upload" in _normalize_rendered_help(result.stdout)

    @pytest.mark.parametrize("columns", [200, 120, 80, 65])
    def test_upload_help_documents_every_option_across_terminal_widths(
        self, monkeypatch: pytest.MonkeyPatch, columns: int
    ) -> None:
        # 80 is Click's own fallback width when no real terminal is
        # present (confirmed to match the CI failure's own captured box
        # width); 65 is a narrower width that still stays above two
        # independently-measured Rich ellipsis-truncation floors: the
        # option names themselves start truncating (e.g. `--repor…`)
        # below ~42-44 columns, and the longer
        # `CLOUDOPS_GUARD_INGESTION_URL` env-var name starts truncating
        # separately, at a wider ~60-64-column floor (it is a longer
        # string, so it stops fitting the panel sooner). Neither
        # truncation floor is tested here -- once Rich has genuinely
        # dropped characters from the rendered text, no test-side
        # normalization can recover them; see module docstring.
        monkeypatch.setenv("COLUMNS", str(columns))
        result = runner.invoke(app, ["upload", "--help"])
        assert result.exit_code == 0
        normalized = _normalize_rendered_help(result.stdout)
        for option in ("--report-dir", "--endpoint", "--dry-run", "--yes"):
            assert option in normalized
        assert "CLOUDOPS_GUARD_INGESTION_URL" in normalized

    def test_upload_help_survives_forced_color_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reproduces the exact CI failure mechanism directly: with color
        # forced on, Rich splits `--report-dir` into three separately-
        # styled ANSI spans (`-`, `-report`, `-dir`) -- confirmed to
        # reproduce byte-for-byte the same escape pattern CI's own
        # failure captured.
        monkeypatch.setenv("FORCE_COLOR", "1")
        monkeypatch.setenv("COLUMNS", "80")
        result = runner.invoke(app, ["upload", "--help"])
        assert result.exit_code == 0
        normalized = _normalize_rendered_help(result.stdout)
        for option in ("--report-dir", "--endpoint", "--dry-run", "--yes"):
            assert option in normalized

    def test_upload_help_never_mentions_a_token_option(self) -> None:
        result = runner.invoke(app, ["upload", "--help"])
        assert "--token" not in _normalize_rendered_help(result.stdout)


class TestMissingReportDirectoryOrFile:
    def test_missing_report_dir_fails_with_nonzero_exit(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "upload",
                "--report-dir",
                str(tmp_path / "does-not-exist"),
                "--endpoint",
                ENDPOINT,
                "--dry-run",
            ],
        )
        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_report_dir_without_report_json_fails_with_nonzero_exit(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = runner.invoke(
            app,
            ["upload", "--report-dir", str(empty_dir), "--endpoint", ENDPOINT, "--dry-run"],
        )
        assert result.exit_code != 0


class TestInvalidReports:
    def test_invalid_kubernetes_report_fails(self, tmp_path: Path) -> None:
        report_dir = _write_report(tmp_path, {"not": "a report"})
        result = runner.invoke(
            app,
            ["upload", "--report-dir", str(report_dir), "--endpoint", ENDPOINT, "--dry-run"],
        )
        assert result.exit_code != 0

    def test_invalid_gitlab_report_fails(self, tmp_path: Path) -> None:
        report = valid_gitlab_report()
        del report["default_branch"]
        report_dir = _write_report(tmp_path, report)
        result = runner.invoke(
            app,
            ["upload", "--report-dir", str(report_dir), "--endpoint", ENDPOINT, "--dry-run"],
        )
        assert result.exit_code != 0


class TestDeterministicPlatformSelectionThroughTheCli:
    def test_kubernetes_report_dry_run_reports_kubernetes(self, tmp_path: Path) -> None:
        report_dir = _write_report(tmp_path, valid_kubernetes_report())
        result = runner.invoke(
            app,
            ["upload", "--report-dir", str(report_dir), "--endpoint", ENDPOINT, "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Platform:          kubernetes" in result.stdout

    def test_gitlab_report_dry_run_reports_gitlab(self, tmp_path: Path) -> None:
        report_dir = _write_report(tmp_path, valid_gitlab_report())
        result = runner.invoke(
            app,
            ["upload", "--report-dir", str(report_dir), "--endpoint", ENDPOINT, "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Platform:          gitlab" in result.stdout


class TestDryRunAndYesMutualExclusion:
    def test_both_flags_together_fails_with_a_clear_message(self, tmp_path: Path) -> None:
        report_dir = _write_report(tmp_path, valid_kubernetes_report())
        result = runner.invoke(
            app,
            [
                "upload",
                "--report-dir",
                str(report_dir),
                "--endpoint",
                ENDPOINT,
                "--dry-run",
                "--yes",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output


class TestNonInteractiveStdinFailsClosed:
    def test_no_yes_no_dry_run_fails_closed(self, tmp_path: Path) -> None:
        report_dir = _write_report(tmp_path, valid_kubernetes_report())
        result = runner.invoke(
            app, ["upload", "--report-dir", str(report_dir), "--endpoint", ENDPOINT]
        )
        assert result.exit_code != 0
        assert "not interactive" in result.output

    def test_still_prints_the_local_summary_before_failing_closed(self, tmp_path: Path) -> None:
        # The summary is local-only and safe to show even on this path --
        # only the network request itself must never happen (proven
        # separately, at the socket/transport level, in
        # test_uploader_zero_network.py).
        report_dir = _write_report(tmp_path, valid_kubernetes_report())
        result = runner.invoke(
            app, ["upload", "--report-dir", str(report_dir), "--endpoint", ENDPOINT]
        )
        assert "upload summary" in result.stdout


class TestStableExitBehavior:
    def test_dry_run_success_exits_zero(self, tmp_path: Path) -> None:
        report_dir = _write_report(tmp_path, valid_kubernetes_report())
        result = runner.invoke(
            app,
            ["upload", "--report-dir", str(report_dir), "--endpoint", ENDPOINT, "--dry-run"],
        )
        assert result.exit_code == 0

    def test_endpoint_validation_failure_exits_nonzero(self, tmp_path: Path) -> None:
        report_dir = _write_report(tmp_path, valid_kubernetes_report())
        result = runner.invoke(
            app,
            [
                "upload",
                "--report-dir",
                str(report_dir),
                "--endpoint",
                "http://ingest.example.com/api/v1/reports",
                "--dry-run",
            ],
        )
        assert result.exit_code != 0

    def test_missing_required_option_exits_nonzero(self) -> None:
        result = runner.invoke(app, ["upload", "--dry-run"])
        assert result.exit_code != 0

    def test_endpoint_from_environment_variable_is_honored(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("CLOUDOPS_GUARD_INGESTION_URL", ENDPOINT)
        report_dir = _write_report(tmp_path, valid_kubernetes_report())
        result = runner.invoke(app, ["upload", "--report-dir", str(report_dir), "--dry-run"])
        assert result.exit_code == 0
        assert ENDPOINT in result.stdout
