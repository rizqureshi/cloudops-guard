"""CloudOps Guard command-line interface."""

from __future__ import annotations

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
from cloudops_guard.collectors.kubernetes import (
    CollectorError,
    KubernetesCollector,
    create_api_clients,
)
from cloudops_guard.config import load_config
from cloudops_guard.engine.evaluator import evaluate
from cloudops_guard.models import AuditReport
from cloudops_guard.reports.generator import generate_reports

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
    help="CloudOps Guard: read-only auditing for Kubernetes reliability, security and cost.",
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
        help="Pod restart count at or above which a finding is raised.",
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


if __name__ == "__main__":
    app()
