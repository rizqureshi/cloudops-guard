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
"""

from __future__ import annotations

import json
from pathlib import Path

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


class TestHelp:
    def test_upload_appears_in_top_level_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "upload" in result.stdout

    def test_upload_help_documents_every_option(self) -> None:
        result = runner.invoke(app, ["upload", "--help"])
        assert result.exit_code == 0
        for option in ("--report-dir", "--endpoint", "--dry-run", "--yes"):
            assert option in result.stdout
        assert "CLOUDOPS_GUARD_INGESTION_URL" in result.stdout

    def test_upload_help_never_mentions_a_token_option(self) -> None:
        result = runner.invoke(app, ["upload", "--help"])
        assert "--token" not in result.stdout


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
