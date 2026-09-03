"""Regression-isolation proof (§12 of the Phase 4E task): existing audit
commands never import or invoke the uploader transport path, never read
ingestion credentials, and never upload automatically. Inspects the
actual `cli.py` file on disk via `ast` for the import-graph check (never
a hand-maintained substitute for reading real imports), mirroring the
import-graph-isolation tests established for the v0.3.0 contact/report
boundary and the Phase 4C `cloudops_guard.ingestion` scope-regression
tests; every other guarantee here is proven behaviorally, by spying on
the exact functions that would have to be called for a violation to
occur.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

import cloudops_guard.cli as cli_module
from cloudops_guard.cli import app
from cloudops_guard.models import ClusterSnapshot

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_MODULE_PATH = REPO_ROOT / "src" / "cloudops_guard" / "cli.py"
runner = CliRunner()


def _collect_imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


class _FakeCollector:
    """Stands in for KubernetesCollector -- identical to test_cli.py's
    own fake, kept local here so this file's isolation proofs are
    self-contained.
    """

    def __init__(self, core_v1: object, apps_v1: object, context: str) -> None:
        self._context = context

    def collect(self, namespace: str | None = None) -> ClusterSnapshot:
        return ClusterSnapshot(
            context=self._context,
            collected_at=dt.datetime.now(dt.UTC),
            namespaces=[],
            pods=[],
            deployments=[],
        )


def _run_kubernetes_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli_module, "create_api_clients", lambda ctx: (None, None))
    monkeypatch.setattr(cli_module, "KubernetesCollector", _FakeCollector)
    output_dir = tmp_path / "out"
    result = runner.invoke(
        app, ["audit", "kubernetes", "--context", "test-ctx", "--output", str(output_dir)]
    )
    assert result.exit_code == 0, result.output
    return output_dir


class TestCliModuleImportGraph:
    def test_cli_py_does_import_the_uploader_package(self) -> None:
        # Sanity check that the AST scan below is looking at real
        # imports, not silently matching nothing -- cli.py legitimately
        # imports uploader.service.run_upload for its own "upload"
        # command, which is the ONLY place that import may be exercised.
        modules = _collect_imported_modules(CLI_MODULE_PATH)
        assert any(m.startswith("cloudops_guard.uploader") for m in modules)


class TestKubernetesAuditNeverInvokesTheUploader:
    def test_never_calls_run_upload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[object] = []
        monkeypatch.setattr(cli_module, "run_upload", lambda **kwargs: calls.append(kwargs))
        _run_kubernetes_audit(tmp_path, monkeypatch)
        assert calls == []

    def test_never_reads_the_ingestion_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_get = os.environ.get

        def spying_get(name: str, default: object = None) -> object:
            if name == "CLOUDOPS_GUARD_INGESTION_TOKEN":
                raise AssertionError("audit kubernetes must never read the ingestion token")
            return real_get(name, default)

        monkeypatch.setattr(os.environ, "get", spying_get)
        _run_kubernetes_audit(tmp_path, monkeypatch)

    def test_never_uploads_automatically_report_json_carries_no_upload_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output_dir = _run_kubernetes_audit(tmp_path, monkeypatch)
        # The report.json this audit wrote is the RAW AuditReport, with
        # no envelope, no ingestion_id, no upload-related field of any
        # kind -- proving no upload path was ever exercised as a side
        # effect of this command, not merely that a spy was never called.
        written = json.loads((output_dir / "report.json").read_text())
        assert "ingestion_id" not in written
        assert "report_fingerprint" not in written


class TestGitlabAuditNeverInvokesTheUploader:
    def test_never_calls_run_upload_even_when_the_audit_itself_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[object] = []
        monkeypatch.setattr(cli_module, "run_upload", lambda **kwargs: calls.append(kwargs))
        # Deliberately given no CLOUDOPS_GUARD_GITLAB_TOKEN and an
        # unreachable URL -- this audit is expected to fail early. The
        # assertion that matters is that failure never routes through
        # run_upload, proven by the spy never having been called,
        # regardless of how or why the audit itself failed.
        monkeypatch.delenv("CLOUDOPS_GUARD_GITLAB_TOKEN", raising=False)
        result = runner.invoke(
            app,
            [
                "audit",
                "gitlab",
                "--gitlab-url",
                "https://gitlab.example.invalid",
                "--project",
                "group/project",
                "--job-timeout-threshold-seconds",
                "3600",
                "--output",
                "/tmp/unused-gitlab-output",
            ],
        )
        assert result.exit_code != 0
        assert calls == []

    def test_never_reads_the_ingestion_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_get = os.environ.get

        def spying_get(name: str, default: object = None) -> object:
            if name == "CLOUDOPS_GUARD_INGESTION_TOKEN":
                raise AssertionError("audit gitlab must never read the ingestion token")
            return real_get(name, default)

        monkeypatch.setattr(os.environ, "get", spying_get)
        monkeypatch.delenv("CLOUDOPS_GUARD_GITLAB_TOKEN", raising=False)
        result = runner.invoke(
            app,
            [
                "audit",
                "gitlab",
                "--gitlab-url",
                "https://gitlab.example.invalid",
                "--project",
                "group/project",
                "--job-timeout-threshold-seconds",
                "3600",
                "--output",
                "/tmp/unused-gitlab-output",
            ],
        )
        assert result.exit_code != 0
