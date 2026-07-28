"""CLI tests using Typer's test runner.

The Kubernetes API is never contacted here: create_api_clients and
KubernetesCollector are monkeypatched with in-memory fakes, so these tests
only exercise CLI wiring, config precedence and exit-code behavior.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from typer.testing import CliRunner

import cloudops_guard.cli as cli_module
from cloudops_guard.cli import app
from cloudops_guard.collectors.kubernetes import CollectorError
from cloudops_guard.models import ClusterSnapshot

runner = CliRunner()


class _FakeCollector:
    """Stands in for KubernetesCollector; records the namespace it was asked for."""

    last_namespace: str | None = None

    def __init__(self, core_v1: object, apps_v1: object, context: str) -> None:
        self._context = context

    def collect(self, namespace: str | None = None) -> ClusterSnapshot:
        _FakeCollector.last_namespace = namespace
        return ClusterSnapshot(
            context=self._context,
            collected_at=dt.datetime.now(dt.UTC),
            namespaces=[],
            pods=[],
            deployments=[],
        )


def _patch_collector(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "create_api_clients", lambda context: (None, None))
    monkeypatch.setattr(cli_module, "KubernetesCollector", _FakeCollector)


def _forbid_collection(monkeypatch) -> None:
    """Fail loudly if collection is ever attempted, for invalid-input tests."""

    def fail(context: str):
        raise AssertionError("collection should not be attempted for invalid input")

    monkeypatch.setattr(cli_module, "create_api_clients", fail)


# --- Successful runs ----------------------------------------------------------


def test_successful_audit_exits_zero_and_writes_reports(tmp_path: Path, monkeypatch) -> None:
    _patch_collector(monkeypatch)
    output_dir = tmp_path / "out"

    result = runner.invoke(
        app, ["audit", "kubernetes", "--context", "test-ctx", "--output", str(output_dir)]
    )

    assert result.exit_code == 0
    assert (output_dir / "report.json").is_file()
    assert (output_dir / "report.html").is_file()


def test_cli_namespace_overrides_config_file(tmp_path: Path, monkeypatch) -> None:
    _patch_collector(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("namespace: from-config\n")

    result = runner.invoke(
        app,
        [
            "audit",
            "kubernetes",
            "--context",
            "test-ctx",
            "--output",
            str(tmp_path / "out"),
            "--config",
            str(config_path),
            "--namespace",
            "from-cli",
        ],
    )

    assert result.exit_code == 0
    assert _FakeCollector.last_namespace == "from-cli"


def test_config_namespace_used_when_cli_omits_it(tmp_path: Path, monkeypatch) -> None:
    _patch_collector(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("namespace: from-config\n")

    result = runner.invoke(
        app,
        [
            "audit",
            "kubernetes",
            "--context",
            "test-ctx",
            "--output",
            str(tmp_path / "out"),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    assert _FakeCollector.last_namespace == "from-config"


def test_cli_restart_threshold_overrides_config_file(tmp_path: Path, monkeypatch) -> None:
    _patch_collector(monkeypatch)
    captured: dict[str, object] = {}
    original_evaluate = cli_module.evaluate

    def spy_evaluate(snapshot, **kwargs):
        captured.update(kwargs)
        return original_evaluate(snapshot, **kwargs)

    monkeypatch.setattr(cli_module, "evaluate", spy_evaluate)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("restart_threshold: 3\n")

    result = runner.invoke(
        app,
        [
            "audit",
            "kubernetes",
            "--context",
            "test-ctx",
            "--output",
            str(tmp_path / "out"),
            "--config",
            str(config_path),
            "--restart-threshold",
            "9",
        ],
    )

    assert result.exit_code == 0
    assert captured["restart_threshold"] == 9


# --- Invalid input: exit code 1, no collection attempted ---------------------


def test_restart_threshold_zero_exits_one(tmp_path: Path, monkeypatch) -> None:
    _forbid_collection(monkeypatch)

    result = runner.invoke(
        app,
        [
            "audit",
            "kubernetes",
            "--context",
            "test-ctx",
            "--output",
            str(tmp_path),
            "--restart-threshold",
            "0",
        ],
    )

    assert result.exit_code == 1
    assert "Invalid configuration" in result.output


def test_negative_restart_threshold_exits_one(tmp_path: Path, monkeypatch) -> None:
    _forbid_collection(monkeypatch)

    result = runner.invoke(
        app,
        [
            "audit",
            "kubernetes",
            "--context",
            "test-ctx",
            "--output",
            str(tmp_path),
            "--restart-threshold",
            "-1",
        ],
    )

    assert result.exit_code == 1


def test_empty_namespace_exits_one(tmp_path: Path, monkeypatch) -> None:
    _forbid_collection(monkeypatch)

    result = runner.invoke(
        app,
        [
            "audit",
            "kubernetes",
            "--context",
            "test-ctx",
            "--output",
            str(tmp_path),
            "--namespace",
            "   ",
        ],
    )

    assert result.exit_code == 1


def test_unknown_config_key_exits_one_with_helpful_message(tmp_path: Path, monkeypatch) -> None:
    _forbid_collection(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("bogus_key: 1\n")

    result = runner.invoke(
        app,
        [
            "audit",
            "kubernetes",
            "--context",
            "test-ctx",
            "--output",
            str(tmp_path),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 1
    assert "bogus_key" in result.output


def test_missing_config_file_exits_one(tmp_path: Path, monkeypatch) -> None:
    _forbid_collection(monkeypatch)
    missing = tmp_path / "does-not-exist.yaml"

    result = runner.invoke(
        app,
        [
            "audit",
            "kubernetes",
            "--context",
            "test-ctx",
            "--output",
            str(tmp_path),
            "--config",
            str(missing),
        ],
    )

    assert result.exit_code == 1
    assert "not found" in result.output.lower()


# --- Collection failure surfaces as exit code 1 -------------------------------


def test_collection_failure_exits_one(tmp_path: Path, monkeypatch) -> None:
    def raise_collector_error(context: str):
        raise CollectorError("List pods failed (HTTP 403: Forbidden)")

    monkeypatch.setattr(cli_module, "create_api_clients", raise_collector_error)

    result = runner.invoke(
        app, ["audit", "kubernetes", "--context", "test-ctx", "--output", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "Collection failed" in result.output
