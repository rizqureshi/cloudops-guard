"""CloudOps Guard command-line interface."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import typer
from pydantic import ValidationError

from cloudops_guard.checks.kubernetes import (
    CHECK_EXCESSIVE_RESTARTS,
    CHECK_MUTABLE_IMAGE_TAG,
    CHECK_NO_CPU_LIMIT,
    CHECK_NO_CPU_REQUEST,
    CHECK_NO_MEMORY_LIMIT,
    CHECK_NO_MEMORY_REQUEST,
)
from cloudops_guard.collectors.gitlab import (
    GitLabClient,
    GitLabClientError,
    GitLabCollector,
    canonicalize_gitlab_project,
    load_gitlab_token,
    normalize_gitlab_base_url,
)
from cloudops_guard.collectors.kubernetes import (
    CollectorError,
    KubernetesCollector,
    create_api_clients,
)
from cloudops_guard.config import load_config
from cloudops_guard.engine.evaluator import evaluate, evaluate_gitlab
from cloudops_guard.models import AuditReport, GitLabAuditReport
from cloudops_guard.reports.generator import generate_gitlab_reports, generate_reports

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("cloudops_guard")

# Referenced for documentation purposes; keeps check IDs importable from one place.
_KNOWN_CHECKS = (
    CHECK_NO_CPU_REQUEST,
    CHECK_NO_MEMORY_REQUEST,
    CHECK_NO_CPU_LIMIT,
    CHECK_NO_MEMORY_LIMIT,
    CHECK_MUTABLE_IMAGE_TAG,
    CHECK_EXCESSIVE_RESTARTS,
)

app = typer.Typer(
    help=(
        "CloudOps Guard: read-only auditing for Kubernetes and GitLab CI/CD "
        "reliability, security and cost."
    ),
    no_args_is_help=True,
)
audit_app = typer.Typer(
    help="Run a read-only audit against a target platform.", no_args_is_help=True
)
app.add_typer(audit_app, name="audit")


@audit_app.command("kubernetes")
def audit_kubernetes(
    context: str = typer.Option(..., "--context", help="Kubernetes context to audit."),
    output: Path = typer.Option(
        ..., "--output", help="Directory to write report.json and report.html into."
    ),
    namespace: str | None = typer.Option(
        None, "--namespace", help="Restrict the audit to a single namespace."
    ),
    config_path: Path | None = typer.Option(
        None, "--config", help="Path to a YAML configuration file."
    ),
    restart_threshold: int | None = typer.Option(
        None,
        "--restart-threshold",
        help="Per-container restart count at or above which a finding is raised.",
    ),
) -> None:
    """Audit a Kubernetes cluster context and write report.json and report.html."""
    try:
        file_config = load_config(config_path)
        effective_config = file_config.with_overrides(
            namespace=namespace, restart_threshold=restart_threshold
        )
    except ValidationError as exc:
        typer.secho(
            f"Invalid configuration: {_format_validation_error(exc)}", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=1) from None
    except (FileNotFoundError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    logger.info("Auditing Kubernetes context %r", context)

    try:
        core_v1, apps_v1 = create_api_clients(context)
        collector = KubernetesCollector(core_v1, apps_v1, context)
        snapshot = collector.collect(namespace=effective_config.namespace)
    except CollectorError as exc:
        typer.secho(f"Collection failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    report = evaluate(
        snapshot,
        restart_threshold=effective_config.restart_threshold,
        namespace_filter=effective_config.namespace,
    )

    try:
        json_path, html_path = generate_reports(report, output)
    except OSError as exc:
        typer.secho(f"Failed to write report to {output}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    _print_summary(report, json_path, html_path)


@audit_app.command("gitlab")
def audit_gitlab(
    gitlab_url: str = typer.Option(
        ..., "--gitlab-url", help="Base URL of the GitLab instance to audit."
    ),
    project: str = typer.Option(
        ...,
        "--project",
        help="Project to audit: a numeric project ID or full path (e.g. group/subgroup/project).",
    ),
    job_timeout_threshold_seconds: int = typer.Option(
        ...,
        "--job-timeout-threshold-seconds",
        min=1,
        help=(
            "Project job timeout (seconds) that GL-REL-001 flags when the configured "
            "project job timeout strictly exceeds this value (equal does not raise a "
            "finding). Required -- v0.2.0 has no product-level default."
        ),
    ),
    output: Path = typer.Option(
        ..., "--output", help="Directory to write report.json and report.html into."
    ),
) -> None:
    """Audit a single GitLab project and write report.json and report.html.

    The GitLab access token is read only from the CLOUDOPS_GUARD_GITLAB_TOKEN
    environment variable -- it is never accepted as a CLI option and never read
    from a configuration file.
    """
    try:
        normalized_url = normalize_gitlab_base_url(gitlab_url)
        canonical_project = canonicalize_gitlab_project(project)
    except GitLabClientError as exc:
        typer.secho(f"Invalid GitLab input: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    try:
        token = load_gitlab_token()
        client = GitLabClient(normalized_url, token)
        collector = GitLabCollector(client)
    except GitLabClientError as exc:
        typer.secho(f"GitLab setup failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    try:
        project_snapshot = collector.collect_project_snapshot(canonical_project)
        ci_config_snapshot = collector.collect_ci_config_snapshot(project_snapshot)
    except GitLabClientError as exc:
        typer.secho(f"Collection failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    audited_at = dt.datetime.now(dt.UTC)
    try:
        report = evaluate_gitlab(
            project_snapshot,
            ci_config_snapshot,
            audited_at=audited_at,
            job_timeout_threshold_seconds=job_timeout_threshold_seconds,
        )
    except ValueError as exc:
        typer.secho(f"Evaluation failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    try:
        json_path, html_path = generate_gitlab_reports(report, output)
    except OSError as exc:
        typer.secho(f"Failed to write report to {output}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    _print_gitlab_summary(report, json_path, html_path)


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        loc = ".".join(str(p) for p in error["loc"]) or "config"
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)


def _print_summary(report: AuditReport, json_path: Path, html_path: Path) -> None:
    summary = report.summary
    typer.echo()
    typer.echo(f"CloudOps Guard audit of context '{report.cluster_context}' complete.")
    typer.secho(f"  Critical: {summary.critical}", fg=typer.colors.RED)
    typer.secho(f"  High:     {summary.high}", fg=typer.colors.YELLOW)
    typer.secho(f"  Medium:   {summary.medium}", fg=typer.colors.BLUE)
    typer.secho(f"  Low:      {summary.low}", fg=typer.colors.WHITE)
    typer.echo(f"  Total:    {summary.total}")
    typer.echo()
    typer.echo(f"Reports written to:\n  {json_path}\n  {html_path}")


def _print_gitlab_summary(report: GitLabAuditReport, json_path: Path, html_path: Path) -> None:
    # Identity comes from the completed report, not the raw --gitlab-url/
    # --project CLI input, so the printed values are the normalized/
    # canonical ones the audit actually ran against.
    summary = report.summary
    typer.echo()
    typer.echo(f"CloudOps Guard audit of GitLab project '{report.project_path}' complete.")
    typer.echo(f"  GitLab:    {report.gitlab_url}")
    typer.secho(f"  Critical: {summary.critical}", fg=typer.colors.RED)
    typer.secho(f"  High:     {summary.high}", fg=typer.colors.YELLOW)
    typer.secho(f"  Medium:   {summary.medium}", fg=typer.colors.BLUE)
    typer.secho(f"  Low:      {summary.low}", fg=typer.colors.WHITE)
    typer.echo(f"  Total:    {summary.total}")
    typer.echo()
    typer.echo(f"Reports written to:\n  {json_path}\n  {html_path}")


if __name__ == "__main__":
    app()
