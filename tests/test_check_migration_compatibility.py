"""Tests for `scripts/check_migration_compatibility.py` (correction pass,
item 1) -- the structural replacement for the original workflow's
`grep`-based migration gate, which searched for JSON shapes
(`"drift": true`, a non-empty `"pending"` array) that `migration_status()`
never actually produces. Pure stdlib; no Azure extra required.
`az_command` is exercised with a real, executable fake `az` shim (never a
`subprocess.run` mock).
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_migration_compatibility as gate  # noqa: E402

_EXECUTION_NAME = "cog-migrate-abc123"

_GOOD_STATUS = {
    "execution_name": _EXECUTION_NAME,
    "packaged_versions": [1, 2, 3],
    "applied_versions": [1, 2, 3],
    "pending_versions": [],
    "drifted_versions": [],
    "unexpected_applied_versions": [],
    "up_to_date": True,
}


def _make_fake_az(
    tmp_path: Path, *, stdout: str = "", exit_code: int = 0, name: str = "fake-az"
) -> list[str]:
    script = tmp_path / name
    script.write_text(
        f"#!/usr/bin/env python3\nimport sys\nsys.stdout.write({stdout!r})\nsys.exit({exit_code})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return [sys.executable, str(script)]


def _executions_json(status: str, *, name: str = _EXECUTION_NAME) -> str:
    return json.dumps([{"name": name, "properties": {"status": status}}])


def _log_rows(*lines: str) -> str:
    return json.dumps([{"Log_s": line} for line in lines])


class TestPollExecutionStatus:
    def test_returns_succeeded_immediately(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout=_executions_json("Succeeded"))
        result = gate.poll_execution_status(
            job_name="j", resource_group="rg", execution_name=_EXECUTION_NAME, az_command=az
        )
        assert result == "Succeeded"

    def test_returns_failed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout=_executions_json("Failed"))
        result = gate.poll_execution_status(
            job_name="j", resource_group="rg", execution_name=_EXECUTION_NAME, az_command=az
        )
        assert result == "Failed"

    def test_az_failure_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout="", exit_code=1)
        with pytest.raises(gate.CompatibilityCheckError, match="failed"):
            gate.poll_execution_status(
                job_name="j",
                resource_group="rg",
                execution_name=_EXECUTION_NAME,
                az_command=az,
                max_attempts=1,
                poll_interval_seconds=0,
            )

    def test_invalid_json_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout="not json")
        with pytest.raises(gate.CompatibilityCheckError, match="valid JSON"):
            gate.poll_execution_status(
                job_name="j",
                resource_group="rg",
                execution_name=_EXECUTION_NAME,
                az_command=az,
                max_attempts=1,
                poll_interval_seconds=0,
            )

    def test_non_list_response_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout=json.dumps({"not": "a list"}))
        with pytest.raises(gate.CompatibilityCheckError, match="not a list"):
            gate.poll_execution_status(
                job_name="j",
                resource_group="rg",
                execution_name=_EXECUTION_NAME,
                az_command=az,
                max_attempts=1,
                poll_interval_seconds=0,
            )

    def test_duplicate_execution_names_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(
            tmp_path,
            stdout=json.dumps(
                [
                    {"name": _EXECUTION_NAME, "properties": {"status": "Succeeded"}},
                    {"name": _EXECUTION_NAME, "properties": {"status": "Succeeded"}},
                ]
            ),
        )
        with pytest.raises(gate.CompatibilityCheckError, match="ambiguous"):
            gate.poll_execution_status(
                job_name="j",
                resource_group="rg",
                execution_name=_EXECUTION_NAME,
                az_command=az,
                max_attempts=1,
                poll_interval_seconds=0,
            )

    def test_never_reaching_terminal_status_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout=_executions_json("Running"))
        with pytest.raises(gate.CompatibilityCheckError, match="did not reach a terminal status"):
            gate.poll_execution_status(
                job_name="j",
                resource_group="rg",
                execution_name=_EXECUTION_NAME,
                az_command=az,
                max_attempts=2,
                poll_interval_seconds=0,
            )

    def test_unrelated_execution_name_does_not_satisfy_the_poll(self, tmp_path: Path) -> None:
        """A different execution's status must never be mistaken for
        this one's -- the exact bug class this correlation logic exists
        to prevent.
        """
        az = _make_fake_az(
            tmp_path, stdout=_executions_json("Succeeded", name="some-other-execution")
        )
        with pytest.raises(gate.CompatibilityCheckError, match="did not reach a terminal status"):
            gate.poll_execution_status(
                job_name="j",
                resource_group="rg",
                execution_name=_EXECUTION_NAME,
                az_command=az,
                max_attempts=1,
                poll_interval_seconds=0,
            )


class TestFetchLogLines:
    def test_returns_log_lines(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout=_log_rows("line one", "line two"))
        lines = gate.fetch_log_lines(
            job_name="j",
            resource_group="rg",
            workspace_id="ws",
            execution_name=_EXECUTION_NAME,
            az_command=az,
        )
        assert lines == ["line one", "line two"]

    def test_query_correlates_via_container_group_name_startswith(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Correction pass, item 6: the query's *primary* per-execution
        correlation must be `ContainerGroupName_s startswith
        execution_name` -- Microsoft's own documented job-run-log query
        shape (https://learn.microsoft.com/en-us/azure/container-apps/
        jobs-get-started-cli#query-job-run-logs) -- never
        `ContainerAppName_s` alone, which identifies the job, not one
        specific execution of it. Captures the real `--analytics-query`
        argument the script actually constructs and passes to `az`,
        rather than asserting behavior only.
        """
        captured_args: list[list[str]] = []
        real_az = _make_fake_az(tmp_path, stdout=_log_rows("x"))
        real_run = gate.subprocess.run

        def capturing_run(cmd, **kwargs):
            captured_args.append(cmd)
            return real_run(real_az + cmd[1:], **kwargs)

        monkeypatch.setattr(gate.subprocess, "run", capturing_run)

        gate.fetch_log_lines(
            job_name="my-job",
            resource_group="rg",
            workspace_id="ws",
            execution_name=_EXECUTION_NAME,
            az_command=["az"],
        )

        assert captured_args, "expected at least one az invocation"
        query_arg_index = captured_args[0].index("--analytics-query") + 1
        query = captured_args[0][query_arg_index]
        assert f"ContainerGroupName_s startswith '{_EXECUTION_NAME}'" in query
        assert "ContainerAppName_s == 'my-job'" in query

    @pytest.mark.parametrize(
        "unsafe_value",
        [
            "job'; drop table x; --",
            "job\nwhere 1 == 1",
            "job`",
            "job|where true",
            "job with spaces",
            "",
        ],
    )
    def test_unsafe_job_name_is_rejected_before_constructing_a_query(
        self, tmp_path: Path, unsafe_value: str
    ) -> None:
        az = _make_fake_az(tmp_path, stdout=_log_rows("irrelevant"))
        with pytest.raises(gate.CompatibilityCheckError, match="unsafe to interpolate"):
            gate.fetch_log_lines(
                job_name=unsafe_value,
                resource_group="rg",
                workspace_id="ws",
                execution_name=_EXECUTION_NAME,
                az_command=az,
            )

    @pytest.mark.parametrize(
        "unsafe_value",
        [
            "exec'; drop table x; --",
            "exec\nwhere 1 == 1",
            "exec`",
            "exec|where true",
            "exec with spaces",
            "",
        ],
    )
    def test_unsafe_execution_name_is_rejected_before_constructing_a_query(
        self, tmp_path: Path, unsafe_value: str
    ) -> None:
        az = _make_fake_az(tmp_path, stdout=_log_rows("irrelevant"))
        with pytest.raises(gate.CompatibilityCheckError, match="unsafe to interpolate"):
            gate.fetch_log_lines(
                job_name="j",
                resource_group="rg",
                workspace_id="ws",
                execution_name=unsafe_value,
                az_command=az,
            )

    def test_az_failure_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout="", exit_code=1)
        with pytest.raises(gate.CompatibilityCheckError, match="failed"):
            gate.fetch_log_lines(
                job_name="j",
                resource_group="rg",
                workspace_id="ws",
                execution_name=_EXECUTION_NAME,
                az_command=az,
            )

    def test_malformed_row_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout=json.dumps([{"NotLog_s": "x"}]))
        with pytest.raises(gate.CompatibilityCheckError, match="Log_s"):
            gate.fetch_log_lines(
                job_name="j",
                resource_group="rg",
                workspace_id="ws",
                execution_name=_EXECUTION_NAME,
                az_command=az,
            )


class TestExtractCorrelatedStatus:
    def test_finds_the_correlated_document(self) -> None:
        lines = [
            "some unrelated log noise",
            json.dumps(_GOOD_STATUS),
        ]
        result = gate.extract_correlated_status(lines, execution_name=_EXECUTION_NAME)
        assert result == _GOOD_STATUS

    def test_no_correlated_document_is_fail_closed(self) -> None:
        other = {**_GOOD_STATUS, "execution_name": "a-different-execution"}
        with pytest.raises(gate.CompatibilityCheckError, match="no migration-status output"):
            gate.extract_correlated_status([json.dumps(other)], execution_name=_EXECUTION_NAME)

    def test_no_json_at_all_is_fail_closed(self) -> None:
        with pytest.raises(gate.CompatibilityCheckError, match="no migration-status output"):
            gate.extract_correlated_status(
                ["plain text", "more text"], execution_name=_EXECUTION_NAME
            )

    def test_two_distinct_correlated_documents_is_ambiguous_and_fail_closed(self) -> None:
        conflicting = {**_GOOD_STATUS, "up_to_date": False, "pending_versions": [4]}
        with pytest.raises(gate.CompatibilityCheckError, match="ambiguous"):
            gate.extract_correlated_status(
                [json.dumps(_GOOD_STATUS), json.dumps(conflicting)], execution_name=_EXECUTION_NAME
            )

    def test_duplicate_identical_lines_are_not_ambiguous(self) -> None:
        """Log Analytics can duplicate ingestion of the same line --
        de-duplicated identical documents must not trip the ambiguity
        guard.
        """
        result = gate.extract_correlated_status(
            [json.dumps(_GOOD_STATUS), json.dumps(_GOOD_STATUS)], execution_name=_EXECUTION_NAME
        )
        assert result == _GOOD_STATUS

    def test_a_substring_collision_in_the_raw_line_is_not_enough(self) -> None:
        """The `ContainerGroupName_s startswith`/`ContainerAppName_s ==`
        pre-filters in `fetch_log_lines` could, in principle, pass
        through a line naming this execution as a substring without it
        actually being the correlated `execution_name` field --
        confirms the *parsed field* is what actually gates acceptance,
        not mere textual presence.
        """
        decoy = {**_GOOD_STATUS, "execution_name": _EXECUTION_NAME + "-nope"}
        with pytest.raises(gate.CompatibilityCheckError, match="no migration-status output"):
            gate.extract_correlated_status([json.dumps(decoy)], execution_name=_EXECUTION_NAME)


class TestPollForCorrelatedStatus:
    """Correction pass, item 6: `poll_for_correlated_status` tolerates
    Log Analytics ingestion delay by retrying a zero-match result, but
    fails immediately (never retries) on an ambiguous one -- more
    polling cannot resolve an ambiguity, only risk compounding it.
    """

    def test_immediate_match_returns_without_polling(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout=_log_rows(json.dumps(_GOOD_STATUS)))
        result = gate.poll_for_correlated_status(
            job_name="j",
            resource_group="rg",
            workspace_id="ws",
            execution_name=_EXECUTION_NAME,
            az_command=az,
            log_poll_interval_seconds=0,
            max_log_poll_attempts=1,
        )
        assert result == _GOOD_STATUS

    def test_delayed_ingestion_is_retried_until_the_document_appears(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulates real Log Analytics ingestion delay: the first two
        polls return no correlated line at all (as if the execution's
        own log line had not yet been ingested); the third returns it.
        """
        call_count = {"n": 0}
        real_run = gate.subprocess.run

        def fake_run(cmd, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                az = _make_fake_az(tmp_path, stdout=_log_rows("unrelated noise, not yet ingested"))
            else:
                az = _make_fake_az(tmp_path, stdout=_log_rows(json.dumps(_GOOD_STATUS)))
            return real_run(az + cmd[1:], **kwargs)

        monkeypatch.setattr(gate.subprocess, "run", fake_run)

        result = gate.poll_for_correlated_status(
            job_name="j",
            resource_group="rg",
            workspace_id="ws",
            execution_name=_EXECUTION_NAME,
            az_command=["az"],
            log_poll_interval_seconds=0,
            max_log_poll_attempts=5,
        )
        assert result == _GOOD_STATUS
        assert call_count["n"] == 3

    def test_exhausting_attempts_with_no_match_ever_appearing_is_fail_closed(
        self, tmp_path: Path
    ) -> None:
        az = _make_fake_az(tmp_path, stdout=_log_rows("never correlates"))
        with pytest.raises(gate.CompatibilityCheckError, match="no migration-status output"):
            gate.poll_for_correlated_status(
                job_name="j",
                resource_group="rg",
                workspace_id="ws",
                execution_name=_EXECUTION_NAME,
                az_command=az,
                log_poll_interval_seconds=0,
                max_log_poll_attempts=3,
            )

    def test_ambiguous_result_fails_immediately_without_retrying(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An ambiguous first poll must fail on attempt 1 -- retrying an
        ambiguity can only surface more candidates, never fewer.
        """
        conflicting = {**_GOOD_STATUS, "up_to_date": False, "pending_versions": [4]}
        call_count = {"n": 0}
        real_run = gate.subprocess.run

        def fake_run(cmd, **kwargs):
            call_count["n"] += 1
            az = _make_fake_az(
                tmp_path, stdout=_log_rows(json.dumps(_GOOD_STATUS), json.dumps(conflicting))
            )
            return real_run(az + cmd[1:], **kwargs)

        monkeypatch.setattr(gate.subprocess, "run", fake_run)

        with pytest.raises(gate.CompatibilityCheckError, match="ambiguous"):
            gate.poll_for_correlated_status(
                job_name="j",
                resource_group="rg",
                workspace_id="ws",
                execution_name=_EXECUTION_NAME,
                az_command=["az"],
                log_poll_interval_seconds=0,
                max_log_poll_attempts=5,
            )
        assert call_count["n"] == 1, "an ambiguous result must never be retried"

    def test_malformed_and_unrelated_lines_are_ignored_while_polling(self, tmp_path: Path) -> None:
        az = _make_fake_az(
            tmp_path,
            stdout=_log_rows(
                "plain text noise",
                "not json {{{",
                json.dumps({"execution_name": "some-other-execution", "up_to_date": True}),
                json.dumps(_GOOD_STATUS),
            ),
        )
        result = gate.poll_for_correlated_status(
            job_name="j",
            resource_group="rg",
            workspace_id="ws",
            execution_name=_EXECUTION_NAME,
            az_command=az,
            log_poll_interval_seconds=0,
            max_log_poll_attempts=1,
        )
        assert result == _GOOD_STATUS

    def test_duplicated_identical_lines_while_polling_are_not_ambiguous(
        self, tmp_path: Path
    ) -> None:
        az = _make_fake_az(
            tmp_path, stdout=_log_rows(json.dumps(_GOOD_STATUS), json.dumps(_GOOD_STATUS))
        )
        result = gate.poll_for_correlated_status(
            job_name="j",
            resource_group="rg",
            workspace_id="ws",
            execution_name=_EXECUTION_NAME,
            az_command=az,
            log_poll_interval_seconds=0,
            max_log_poll_attempts=1,
        )
        assert result == _GOOD_STATUS


class TestValidateStatusDocument:
    def test_accepts_a_genuinely_compatible_status(self) -> None:
        gate.validate_status_document(_GOOD_STATUS)  # must not raise

    @pytest.mark.parametrize("missing_key", gate._REQUIRED_KEYS)
    def test_rejects_a_missing_key(self, missing_key: str) -> None:
        broken = {k: v for k, v in _GOOD_STATUS.items() if k != missing_key}
        with pytest.raises(gate.CompatibilityCheckError, match="missing required key"):
            gate.validate_status_document(broken)

    def test_rejects_pending_versions(self) -> None:
        broken = {**_GOOD_STATUS, "pending_versions": [4], "up_to_date": False}
        with pytest.raises(gate.CompatibilityCheckError, match="pending, unapplied"):
            gate.validate_status_document(broken)

    def test_rejects_drifted_versions(self) -> None:
        broken = {**_GOOD_STATUS, "drifted_versions": [2], "up_to_date": False}
        with pytest.raises(gate.CompatibilityCheckError, match="drifted"):
            gate.validate_status_document(broken)

    def test_rejects_unexpected_applied_versions(self) -> None:
        """The rollback-safety guarantee: a schema-incompatible rollback
        target must be rejected."""
        broken = {**_GOOD_STATUS, "unexpected_applied_versions": [999], "up_to_date": False}
        with pytest.raises(gate.CompatibilityCheckError, match="rollback-incompatible"):
            gate.validate_status_document(broken)

    def test_rejects_up_to_date_false_even_with_empty_lists(self) -> None:
        """Defense in depth: even if every list is (incorrectly) empty,
        an explicit `up_to_date: false` must still be refused, never
        inferred as compatible from the lists alone.
        """
        broken = {**_GOOD_STATUS, "up_to_date": False}
        with pytest.raises(gate.CompatibilityCheckError, match="up_to_date"):
            gate.validate_status_document(broken)

    def test_rejects_wrong_type_for_up_to_date(self) -> None:
        broken = {**_GOOD_STATUS, "up_to_date": "true"}
        with pytest.raises(gate.CompatibilityCheckError, match="boolean"):
            gate.validate_status_document(broken)

    @pytest.mark.parametrize("list_key", gate._LIST_KEYS)
    def test_rejects_wrong_type_for_a_list_field(self, list_key: str) -> None:
        broken = {**_GOOD_STATUS, list_key: "not-a-list"}
        with pytest.raises(gate.CompatibilityCheckError, match="must be a list"):
            gate.validate_status_document(broken)


class TestMainEndToEnd:
    def test_full_success_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        exec_list_az = _make_fake_az(
            tmp_path, stdout=_executions_json("Succeeded"), name="exec-list"
        )
        log_query_az = _make_fake_az(
            tmp_path, stdout=_log_rows(json.dumps(_GOOD_STATUS)), name="log-query"
        )

        real_run = gate.subprocess.run

        def fake_run(cmd, **kwargs):
            if "execution" in cmd:
                return real_run(exec_list_az + cmd[1:], **kwargs)
            return real_run(log_query_az + cmd[1:], **kwargs)

        monkeypatch.setattr(gate.subprocess, "run", fake_run)

        exit_code = gate.main(
            [
                "--job-name",
                "j",
                "--resource-group",
                "rg",
                "--execution-name",
                _EXECUTION_NAME,
                "--log-analytics-workspace-id",
                "ws",
                "--poll-interval-seconds",
                "0",
                "--max-attempts",
                "1",
            ]
        )
        assert exit_code == 0

    def test_pending_migration_blocks_deployment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad_status = {**_GOOD_STATUS, "pending_versions": [4], "up_to_date": False}
        exec_list_az = _make_fake_az(
            tmp_path, stdout=_executions_json("Succeeded"), name="exec-list"
        )
        log_query_az = _make_fake_az(
            tmp_path, stdout=_log_rows(json.dumps(bad_status)), name="log-query"
        )
        real_run = gate.subprocess.run

        def fake_run(cmd, **kwargs):
            if "execution" in cmd:
                return real_run(exec_list_az + cmd[1:], **kwargs)
            return real_run(log_query_az + cmd[1:], **kwargs)

        monkeypatch.setattr(gate.subprocess, "run", fake_run)

        exit_code = gate.main(
            [
                "--job-name",
                "j",
                "--resource-group",
                "rg",
                "--execution-name",
                _EXECUTION_NAME,
                "--log-analytics-workspace-id",
                "ws",
                "--poll-interval-seconds",
                "0",
                "--max-attempts",
                "1",
            ]
        )
        assert exit_code == 1

    def test_failed_execution_blocks_deployment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exec_list_az = _make_fake_az(tmp_path, stdout=_executions_json("Failed"), name="exec-list")
        real_run = gate.subprocess.run
        monkeypatch.setattr(
            gate.subprocess, "run", lambda cmd, **kw: real_run(exec_list_az + cmd[1:], **kw)
        )

        exit_code = gate.main(
            [
                "--job-name",
                "j",
                "--resource-group",
                "rg",
                "--execution-name",
                _EXECUTION_NAME,
                "--log-analytics-workspace-id",
                "ws",
                "--poll-interval-seconds",
                "0",
                "--max-attempts",
                "1",
            ]
        )
        assert exit_code == 1
