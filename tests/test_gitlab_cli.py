"""Tests for `cloudops-guard audit gitlab` (v0.2.0 Phase 2E-A CLI integration).

No test in this file contacts GitLab or any other network service:
`GitLabClient`/`GitLabCollector` are monkeypatched with in-memory fakes that
return real, normalized Pydantic snapshots, so `evaluate_gitlab` and
`generate_gitlab_reports` run unmodified against them for the end-to-end
tests. These tests only exercise CLI wiring (input validation order, token
handling, call counts/order, error-prefix contracts, exit codes) -- not
collector/evaluator/check logic, which is already covered by their own test
suites.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import urllib3
from typer.testing import CliRunner

import cloudops_guard.cli as cli_module
from cloudops_guard.cli import app
from cloudops_guard.collectors.gitlab import GITLAB_TOKEN_ENV_VAR, GitLabClientError
from cloudops_guard.models import (
    GitLabAuditReport,
    GitLabCiConfigSnapshot,
    GitLabFinding,
    GitLabProjectSettings,
    GitLabProjectSnapshot,
    GitLabProtectedBranchRule,
)

runner = CliRunner()

SYNTHETIC_TOKEN = "glpat-SYNTH3T1C-CLI-TEST-TOKEN-000000000000"  # test fixture only, never real


@pytest.fixture(autouse=True)
def _default_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A default token for every test, so tests unrelated to token handling

    don't need to set one explicitly. Tests that specifically exercise
    missing/blank/wrong-env-var token behavior override this via their own
    monkeypatch.setenv/delenv calls, which apply after (and win over) this
    fixture's.
    """
    monkeypatch.setenv(GITLAB_TOKEN_ENV_VAR, SYNTHETIC_TOKEN)


DEFAULT_ARGS = [
    "audit",
    "gitlab",
    "--gitlab-url",
    "https://gitlab.example.com",
    "--project",
    "42",
    "--job-timeout-threshold-seconds",
    "3600",
]


def make_project_snapshot(
    *,
    gitlab_url: str = "https://gitlab.example.com",
    project_id: int = 42,
    project_path: str = "group/project",
    default_branch: str = "main",
    build_timeout: int = 600,
    only_allow_merge_if_pipeline_succeeds: bool = True,
    collected_at: dt.datetime | None = None,
) -> GitLabProjectSnapshot:
    return GitLabProjectSnapshot(
        gitlab_url=gitlab_url,
        gitlab_version="18.4.1",
        enterprise=False,
        collected_at=collected_at
        if collected_at is not None
        else dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        project=GitLabProjectSettings(
            project_id=project_id,
            project_path=project_path,
            default_branch=default_branch,
            visibility="private",
            only_allow_merge_if_pipeline_succeeds=only_allow_merge_if_pipeline_succeeds,
            public_jobs=False,
            ci_push_repository_for_job_token_allowed=False,
            ci_pipeline_variables_minimum_override_role="maintainer",
            auto_cancel_pending_pipelines="enabled",
            ci_default_git_depth=50,
            build_timeout=build_timeout,
        ),
        protected_branches=[
            GitLabProtectedBranchRule(
                name="main", allow_force_push=False, role_push_access_levels=[]
            )
        ],
    )


def make_ci_snapshot(
    *, project_path: str = "group/project", collected_at: dt.datetime | None = None
) -> GitLabCiConfigSnapshot:
    return GitLabCiConfigSnapshot(
        project_path=project_path,
        collected_at=collected_at
        if collected_at is not None
        else dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        images=[],
    )


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_snapshot: GitLabProjectSnapshot | None = None,
    ci_snapshot_factory=None,
    project_error: Exception | None = None,
    ci_error: Exception | None = None,
) -> dict[str, object]:
    """Patch cli_module.GitLabClient/GitLabCollector with in-memory fakes.

    Returns a dict of call-tracking state (fresh per invocation) the test
    can inspect afterward: client construction args, collector construction
    args, and the exact order/arguments of collect_project_snapshot/
    collect_ci_config_snapshot calls.
    """
    state: dict[str, object] = {
        "client_calls": [],
        "collector_calls": [],
        "collection_order": [],
    }

    class _FakeClient:
        def __init__(self, base_url: str, token: str) -> None:
            state["client_calls"].append((base_url, token))
            self.instance_base_url = base_url

    class _FakeCollector:
        def __init__(self, client: object) -> None:
            state["collector_calls"].append(client)
            self._client = client

        def collect_project_snapshot(self, project: str, *, collected_at: object = None):
            state["collection_order"].append(("project", project))
            if project_error is not None:
                raise project_error
            return project_snapshot if project_snapshot is not None else make_project_snapshot()

        def collect_ci_config_snapshot(
            self, snapshot: GitLabProjectSnapshot, *, collected_at: object = None
        ):
            state["collection_order"].append(("ci", snapshot))
            if ci_error is not None:
                raise ci_error
            if ci_snapshot_factory is not None:
                return ci_snapshot_factory(snapshot)
            return make_ci_snapshot(project_path=snapshot.project.project_path)

    monkeypatch.setattr(cli_module, "GitLabClient", _FakeClient)
    monkeypatch.setattr(cli_module, "GitLabCollector", _FakeCollector)
    return state


def _forbid_client_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("GitLabClient must not be constructed for this input")

    monkeypatch.setattr(cli_module, "GitLabClient", fail)


def _forbid_token_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> str:
        raise AssertionError("load_gitlab_token must not be called for this input")

    monkeypatch.setattr(cli_module, "load_gitlab_token", fail)


def _no_leftover_files(output: Path) -> bool:
    return not output.exists() or not any(output.iterdir())


# --- 1, 2, 3: help contract ----------------------------------------------------


def test_audit_gitlab_help_exposes_the_four_required_options() -> None:
    # A wide COLUMNS value avoids Rich's help-text column truncating the
    # longer option names (e.g. "--job-timeout-thresho…") mid-string.
    result = runner.invoke(app, ["audit", "gitlab", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    for option in ("--gitlab-url", "--project", "--job-timeout-threshold-seconds", "--output"):
        assert option in result.output


def test_audit_gitlab_help_describes_strictly_exceeds_threshold_semantics() -> None:
    # A wide COLUMNS value avoids Rich wrapping "strictly exceeds" across a
    # line boundary; whitespace is also normalized below (collapsing
    # newlines/padding to single spaces) so wrapping elsewhere in the
    # rendered help can't split the phrase either.
    result = runner.invoke(app, ["audit", "gitlab", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    normalized = " ".join(result.output.split())
    assert "strictly exceeds" in normalized
    assert "at or above" not in normalized.lower()


def test_audit_gitlab_help_mentions_env_var_but_no_token_value() -> None:
    result = runner.invoke(app, ["audit", "gitlab", "--help"])
    assert GITLAB_TOKEN_ENV_VAR in result.output
    assert SYNTHETIC_TOKEN not in result.output


def test_audit_gitlab_has_no_token_config_or_insecure_option() -> None:
    result = runner.invoke(app, ["audit", "gitlab", "--help"])
    assert "--token" not in result.output
    assert "--config" not in result.output
    assert "--insecure" not in result.output


def test_unknown_token_option_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_client_construction(monkeypatch)
    _forbid_token_read(monkeypatch)
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", "/tmp/unused", "--token", "x"])
    assert result.exit_code != 0


# --- 4: missing required option ------------------------------------------------


def test_missing_required_option_exits_without_token_or_collection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _forbid_client_construction(monkeypatch)
    _forbid_token_read(monkeypatch)
    result = runner.invoke(
        app,
        [
            "audit",
            "gitlab",
            "--gitlab-url",
            "https://gitlab.example.com",
            "--job-timeout-threshold-seconds",
            "3600",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert _no_leftover_files(tmp_path)


# --- 5, 6, 7: threshold validation before token/client -------------------------


@pytest.mark.parametrize("bad_threshold", ["0", "-1", "abc"])
def test_invalid_threshold_rejected_before_token_or_client(
    bad_threshold: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _forbid_client_construction(monkeypatch)
    _forbid_token_read(monkeypatch)
    result = runner.invoke(
        app,
        [
            "audit",
            "gitlab",
            "--gitlab-url",
            "https://gitlab.example.com",
            "--project",
            "42",
            "--job-timeout-threshold-seconds",
            bad_threshold,
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert _no_leftover_files(tmp_path)


# --- 8, 9: URL normalization ----------------------------------------------------


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://gitlab.example.com",
        "https://gitlab.example.com/",
        "https://gitlab.example.com/api/v4",
        "https://gitlab.example.com/api/v4/",
    ],
)
def test_url_forms_normalize_to_the_same_value_before_client_construction(
    raw_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _install_fakes(monkeypatch)
    result = runner.invoke(
        app,
        [
            "audit",
            "gitlab",
            "--gitlab-url",
            raw_url,
            "--project",
            "42",
            "--job-timeout-threshold-seconds",
            "3600",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert len(state["client_calls"]) == 1
    assert state["client_calls"][0][0] == "https://gitlab.example.com"


def test_url_with_subgroup_path_prefix_normalizes_correctly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _install_fakes(monkeypatch)
    result = runner.invoke(
        app,
        [
            "audit",
            "gitlab",
            "--gitlab-url",
            "https://example.com/gitlab/api/v4/",
            "--project",
            "42",
            "--job-timeout-threshold-seconds",
            "3600",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert state["client_calls"][0][0] == "https://example.com/gitlab"


# --- 10, 11, 12: project canonicalization --------------------------------------


def test_numeric_project_id_is_canonicalized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _install_fakes(monkeypatch)
    result = runner.invoke(
        app,
        [
            "audit",
            "gitlab",
            "--gitlab-url",
            "https://gitlab.example.com",
            "--project",
            "999",
            "--job-timeout-threshold-seconds",
            "3600",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert state["collection_order"][0] == ("project", "999")


def test_raw_subgroup_path_is_encoded_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _install_fakes(monkeypatch)
    result = runner.invoke(
        app,
        [
            "audit",
            "gitlab",
            "--gitlab-url",
            "https://gitlab.example.com",
            "--project",
            "group/subgroup/project",
            "--job-timeout-threshold-seconds",
            "3600",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert state["collection_order"][0] == ("project", "group%2Fsubgroup%2Fproject")


def test_already_encoded_project_path_is_not_double_encoded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _install_fakes(monkeypatch)
    result = runner.invoke(
        app,
        [
            "audit",
            "gitlab",
            "--gitlab-url",
            "https://gitlab.example.com",
            "--project",
            "group%2Fsubgroup%2Fproject",
            "--job-timeout-threshold-seconds",
            "3600",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert state["collection_order"][0] == ("project", "group%2Fsubgroup%2Fproject")


# --- 13, 14: invalid URL/project fail before token/network access --------------


def test_invalid_url_fails_before_token_access_and_client_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _forbid_client_construction(monkeypatch)
    _forbid_token_read(monkeypatch)
    result = runner.invoke(
        app,
        [
            "audit",
            "gitlab",
            "--gitlab-url",
            "not a url",
            "--project",
            "42",
            "--job-timeout-threshold-seconds",
            "3600",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "Invalid GitLab input:" in result.output
    assert _no_leftover_files(tmp_path)


@pytest.mark.parametrize(
    "bad_project",
    ["", "https://example.com/group/project", "group//project", "group/../project"],
)
def test_invalid_project_fails_before_token_access_and_network_access(
    bad_project: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _forbid_client_construction(monkeypatch)
    _forbid_token_read(monkeypatch)
    result = runner.invoke(
        app,
        [
            "audit",
            "gitlab",
            "--gitlab-url",
            "https://gitlab.example.com",
            "--project",
            bad_project,
            "--job-timeout-threshold-seconds",
            "3600",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "Invalid GitLab input:" in result.output
    assert _no_leftover_files(tmp_path)


# --- 15, 16, 17, 18: token handling ---------------------------------------------


def test_token_is_loaded_only_from_the_approved_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(GITLAB_TOKEN_ENV_VAR, raising=False)
    monkeypatch.setenv("GITLAB_TOKEN", "some-other-env-var-value")
    _forbid_client_construction(monkeypatch)
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 1
    assert GITLAB_TOKEN_ENV_VAR in result.output
    assert _no_leftover_files(tmp_path)


def test_missing_token_exits_one_with_no_collection_or_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(GITLAB_TOKEN_ENV_VAR, raising=False)
    _forbid_client_construction(monkeypatch)
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 1
    assert "GitLab setup failed:" in result.output
    assert _no_leftover_files(tmp_path)


def test_blank_token_exits_one_with_no_collection_or_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(GITLAB_TOKEN_ENV_VAR, "   ")
    _forbid_client_construction(monkeypatch)
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 1
    assert "GitLab setup failed:" in result.output
    assert _no_leftover_files(tmp_path)


def test_token_is_passed_unchanged_to_gitlab_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(GITLAB_TOKEN_ENV_VAR, SYNTHETIC_TOKEN)
    state = _install_fakes(monkeypatch)
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert state["client_calls"] == [("https://gitlab.example.com", SYNTHETIC_TOKEN)]


# --- 19, 38: token sentinel never leaks -----------------------------------------


def test_token_sentinel_never_appears_in_output_or_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(GITLAB_TOKEN_ENV_VAR, SYNTHETIC_TOKEN)
    _install_fakes(monkeypatch)
    output_dir = tmp_path / "out"
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(output_dir)])

    assert result.exit_code == 0
    assert SYNTHETIC_TOKEN not in result.output
    assert SYNTHETIC_TOKEN not in (result.exception and str(result.exception) or "")
    json_text = (output_dir / "report.json").read_text(encoding="utf-8")
    html_text = (output_dir / "report.html").read_text(encoding="utf-8")
    assert SYNTHETIC_TOKEN not in json_text
    assert SYNTHETIC_TOKEN not in html_text


# --- 20, 21, 22, 23: exactly one client/collector, correct call order/args ------


def test_exactly_one_client_and_one_collector_are_constructed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _install_fakes(monkeypatch)
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert len(state["client_calls"]) == 1
    assert len(state["collector_calls"]) == 1


def test_ci_collection_receives_the_exact_project_snapshot_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_snapshot = make_project_snapshot()
    state = _install_fakes(monkeypatch, project_snapshot=project_snapshot)
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 0
    ci_call = state["collection_order"][1]
    assert ci_call[0] == "ci"
    assert ci_call[1] is project_snapshot


def test_collection_order_is_project_first_then_ci(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _install_fakes(monkeypatch)
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert [call[0] for call in state["collection_order"]] == ["project", "ci"]


# --- 24, 25, 26: collection failures ---------------------------------------------


def test_project_collection_failure_exits_one_and_creates_no_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(
        monkeypatch, project_error=GitLabClientError("Get project failed: not found (HTTP 404).")
    )
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 1
    assert "Collection failed:" in result.output
    assert "Traceback" not in result.output
    assert _no_leftover_files(tmp_path)


def test_ci_collection_failure_exits_one_and_creates_no_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(
        monkeypatch, ci_error=GitLabClientError("Get CI Lint result failed: not found (HTTP 404).")
    )
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 1
    assert "Collection failed:" in result.output
    assert "Traceback" not in result.output
    assert _no_leftover_files(tmp_path)


# --- 27, 28: evaluate_gitlab call contract ---------------------------------------


def test_evaluate_gitlab_called_exactly_once_with_both_snapshots_and_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)
    captured: list[dict[str, object]] = []
    original = cli_module.evaluate_gitlab

    def spy(project_snapshot, ci_config_snapshot, *, audited_at, job_timeout_threshold_seconds):
        captured.append(
            {
                "project_snapshot": project_snapshot,
                "ci_config_snapshot": ci_config_snapshot,
                "audited_at": audited_at,
                "job_timeout_threshold_seconds": job_timeout_threshold_seconds,
            }
        )
        return original(
            project_snapshot,
            ci_config_snapshot,
            audited_at=audited_at,
            job_timeout_threshold_seconds=job_timeout_threshold_seconds,
        )

    monkeypatch.setattr(cli_module, "evaluate_gitlab", spy)
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])

    assert result.exit_code == 0
    assert len(captured) == 1
    call = captured[0]
    assert isinstance(call["project_snapshot"], GitLabProjectSnapshot)
    assert isinstance(call["ci_config_snapshot"], GitLabCiConfigSnapshot)
    assert call["job_timeout_threshold_seconds"] == 3600
    audited_at = call["audited_at"]
    assert isinstance(audited_at, dt.datetime)
    assert audited_at.tzinfo is not None
    assert audited_at.utcoffset() == dt.timedelta(0)


def test_generated_at_and_finding_audited_at_share_the_same_evaluation_timestamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Real evaluate_gitlab is exercised (not monkeypatched); a finding is
    # forced (only_allow_merge_if_pipeline_succeeds=False -> GL-MR-001) so
    # there is at least one finding whose audited_at can be compared.
    project_snapshot = make_project_snapshot(only_allow_merge_if_pipeline_succeeds=False)
    _install_fakes(monkeypatch, project_snapshot=project_snapshot)
    output_dir = tmp_path / "out"
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(output_dir)])

    assert result.exit_code == 0
    raw = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert len(raw["findings"]) >= 1
    for finding in raw["findings"]:
        assert finding["audited_at"] == raw["generated_at"]


# --- 29: evaluation failure -------------------------------------------------------


def test_evaluation_failure_exits_one_and_creates_no_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A real project/CI snapshot identity mismatch triggers evaluate_gitlab's
    # own sanitized ValueError -- exercised through the real evaluator, not
    # a monkeypatched stand-in.
    project_snapshot = make_project_snapshot(project_path="group/project-a")
    _install_fakes(
        monkeypatch,
        project_snapshot=project_snapshot,
        ci_snapshot_factory=lambda snapshot: make_ci_snapshot(project_path="group/project-b"),
    )
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 1
    assert "Evaluation failed:" in result.output
    assert "Traceback" not in result.output
    assert _no_leftover_files(tmp_path)


# --- 30: generate_gitlab_reports, not Kubernetes generate_reports ---------------


def test_generate_gitlab_reports_called_once_and_kubernetes_generate_reports_never(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)
    gitlab_calls: list[object] = []
    kubernetes_calls: list[object] = []
    original = cli_module.generate_gitlab_reports

    def spy_gitlab(report, output_dir):
        gitlab_calls.append((report, output_dir))
        return original(report, output_dir)

    def spy_kubernetes(*args: object, **kwargs: object) -> None:
        kubernetes_calls.append((args, kwargs))
        raise AssertionError("Kubernetes generate_reports must not be called for a GitLab audit")

    monkeypatch.setattr(cli_module, "generate_gitlab_reports", spy_gitlab)
    monkeypatch.setattr(cli_module, "generate_reports", spy_kubernetes)

    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert len(gitlab_calls) == 1
    assert kubernetes_calls == []


# --- 31: report-writing failure ---------------------------------------------------


def test_report_write_failure_exits_one_without_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)

    def boom(report: object, output_dir: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(cli_module, "generate_gitlab_reports", boom)
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 1
    assert f"Failed to write report to {tmp_path}" in result.output
    assert "Traceback" not in result.output


# --- 32, 33, 34: successful end-to-end audit -------------------------------------


def test_successful_audit_exits_zero_and_writes_real_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)
    output_dir = tmp_path / "out"
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(output_dir)])
    assert result.exit_code == 0
    assert (output_dir / "report.json").is_file()
    assert (output_dir / "report.html").is_file()


def test_successful_json_validates_back_into_gitlab_audit_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)
    output_dir = tmp_path / "out"
    runner.invoke(app, [*DEFAULT_ARGS, "--output", str(output_dir)])
    raw = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    reloaded = GitLabAuditReport.model_validate(raw)
    assert reloaded.project_path == "group/project"


def test_successful_html_is_the_gitlab_template_not_kubernetes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)
    output_dir = tmp_path / "out"
    runner.invoke(app, [*DEFAULT_ARGS, "--output", str(output_dir)])
    html = (output_dir / "report.html").read_text(encoding="utf-8")
    assert "GitLab Audit Report" in html
    assert "Kubernetes Audit Report" not in html
    assert "cluster_context" not in html.lower()


# --- 35, 36, 37: summary content, counts, exit code with findings --------------


def test_success_summary_uses_project_path_and_gitlab_url_from_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_snapshot = make_project_snapshot(
        gitlab_url="https://gitlab.example.com", project_path="engineering/checkout"
    )
    _install_fakes(monkeypatch, project_snapshot=project_snapshot)
    output_dir = tmp_path / "out"
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(output_dir)])
    assert result.exit_code == 0
    assert "engineering/checkout" in result.output
    assert "https://gitlab.example.com" in result.output


def test_summary_counts_and_total_are_correct_and_findings_do_not_change_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # only_allow_merge_if_pipeline_succeeds=False -> exactly one GL-MR-001
    # (medium) finding, with every other check family's condition safe.
    project_snapshot = make_project_snapshot(only_allow_merge_if_pipeline_succeeds=False)
    _install_fakes(monkeypatch, project_snapshot=project_snapshot)
    output_dir = tmp_path / "out"
    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(output_dir)])

    assert result.exit_code == 0
    raw = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert raw["summary"] == {"critical": 0, "high": 0, "medium": 1, "low": 0}
    assert "Medium:   1" in result.output
    assert "Total:    1" in result.output


# --- 39: no extraneous fields introduced by CLI wiring --------------------------


def test_report_output_has_no_fields_outside_the_declared_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_snapshot = make_project_snapshot(only_allow_merge_if_pipeline_succeeds=False)
    _install_fakes(monkeypatch, project_snapshot=project_snapshot)
    output_dir = tmp_path / "out"
    runner.invoke(app, [*DEFAULT_ARGS, "--output", str(output_dir)])
    raw = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert set(raw.keys()) == set(GitLabAuditReport.model_fields)
    for finding in raw["findings"]:
        assert set(finding.keys()) == set(GitLabFinding.model_fields)
    for forbidden in (
        "merged_yaml",
        "script",
        "variables",
        "warnings",
        "errors",
        "trace",
        "artifact",
    ):
        assert forbidden not in json.dumps(raw)


# --- 41: Kubernetes command help unaffected --------------------------------------


def test_kubernetes_command_help_is_unaffected() -> None:
    result = runner.invoke(app, ["audit", "kubernetes", "--help"])
    assert result.exit_code == 0
    assert "--context" in result.output
    assert "--gitlab-url" not in result.output


# --- 42: determinism apart from the audit timestamp ------------------------------


def test_repeated_runs_are_deterministic_apart_from_the_audit_timestamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    _install_fakes(monkeypatch)
    runner.invoke(app, [*DEFAULT_ARGS, "--output", str(first_dir)])
    _install_fakes(monkeypatch)
    runner.invoke(app, [*DEFAULT_ARGS, "--output", str(second_dir)])

    first = json.loads((first_dir / "report.json").read_text(encoding="utf-8"))
    second = json.loads((second_dir / "report.json").read_text(encoding="utf-8"))
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


# --- 43: structural signature guard ----------------------------------------------


def test_audit_gitlab_has_only_the_four_documented_cli_inputs() -> None:
    import inspect

    signature = inspect.signature(cli_module.audit_gitlab)
    assert list(signature.parameters) == [
        "gitlab_url",
        "project",
        "job_timeout_threshold_seconds",
        "output",
    ]


# --- 44: no live network access ---------------------------------------------------


def test_no_real_network_access_occurs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("audit gitlab must not perform real network access in tests")

    monkeypatch.setattr(urllib3.PoolManager, "request", _forbidden)
    monkeypatch.setattr(urllib3.PoolManager, "urlopen", _forbidden)
    _install_fakes(monkeypatch)

    result = runner.invoke(app, [*DEFAULT_ARGS, "--output", str(tmp_path)])
    assert result.exit_code == 0
