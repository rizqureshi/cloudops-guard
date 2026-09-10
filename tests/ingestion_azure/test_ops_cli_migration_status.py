"""Tests for `ops_cli.py migration-status`'s execution-name correlation
wrapper (correction pass, item 1): the deployment workflow's migration/
compatibility gate reads this command's output back out of Log
Analytics, which aggregates *every* job execution's stdout into one
shared table -- without a way to confirm which execution a given JSON
blob actually came from, the gate could accept stale output from an
earlier, unrelated execution within the same time window. Real local
PostgreSQL; CLI invoked exactly as the workflow invokes it (`typer`'s own
`CliRunner`, not a direct Python function call), so this exercises the
same stdout `ops_cli.py` actually produces.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from cloudops_guard.ingestion_azure.ops_cli import app

pytestmark = pytest.mark.postgres

runner = CliRunner()


def _env(postgres_conninfo: str, **overrides: str) -> dict[str, str]:
    base = {
        "COG_INGESTION_REGION": "canadacentral",
        "COG_METADATA_DB_CONNINFO": postgres_conninfo,
    }
    base.update(overrides)
    return base


def test_output_includes_the_real_migration_status_fields(postgres_conninfo: str) -> None:
    result = runner.invoke(app, ["migration-status"], env=_env(postgres_conninfo))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    for key in (
        "packaged_versions",
        "applied_versions",
        "pending_versions",
        "drifted_versions",
        "unexpected_applied_versions",
        "up_to_date",
    ):
        assert key in payload, f"missing key: {key}"
    assert payload["up_to_date"] is True


def test_execution_name_is_null_when_the_env_var_is_absent(postgres_conninfo: str) -> None:
    """Running outside a real Container Apps Job execution (e.g. a local
    developer invocation) must never crash -- the correlation field is
    simply `null`, not a `KeyError`/`None`-shaped string.
    """
    result = runner.invoke(app, ["migration-status"], env=_env(postgres_conninfo))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["execution_name"] is None


def test_execution_name_reflects_the_real_container_apps_env_var(postgres_conninfo: str) -> None:
    """The exact, real environment variable Azure Container Apps Jobs
    documents as being injected per-execution -- confirms the gate's
    correlation mechanism actually threads this value through to the
    printed JSON.
    """
    result = runner.invoke(
        app,
        ["migration-status"],
        env=_env(postgres_conninfo, CONTAINER_APP_JOB_EXECUTION_NAME="migrate-abc123"),
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["execution_name"] == "migrate-abc123"


def test_output_is_a_single_well_formed_json_document(postgres_conninfo: str) -> None:
    """The workflow's gate parses this output structurally, not with
    `grep` -- confirms `typer.echo` never appends stray trailing content
    (e.g. a second echoed line) that would make `json.loads` ambiguous.
    """
    result = runner.invoke(app, ["migration-status"], env=_env(postgres_conninfo))
    assert result.exit_code == 0, result.output
    # Exactly one JSON value parses from the full output with nothing left over.
    decoder = json.JSONDecoder()
    _value, end = decoder.raw_decode(result.output)
    assert result.output[end:].strip() == ""


def test_json_is_printed_on_a_single_line(postgres_conninfo: str) -> None:
    """Azure Container Apps captures job stdout per line -- a
    pretty-printed, multi-line JSON document would be split across
    several separate Log Analytics rows, only the first of which would
    carry the `execution_name` a correlating query filters on. Must stay
    exactly one line so the whole document is one atomic, correlatable
    Log Analytics row.
    """
    result = runner.invoke(app, ["migration-status"], env=_env(postgres_conninfo))
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected exactly one non-blank output line, got: {lines!r}"
