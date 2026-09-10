#!/usr/bin/env python3
"""Structurally validates a Container Apps Job's `migration-status`
output before a deployment/rollback is allowed to proceed (correction
pass, item 1; log-correlation query corrected in a later pass, item 6).

**The defect this replaces**: the original workflow gate used `grep` to
search rendered log text for `"drift": true` and a non-empty `"pending"`
array -- shapes `migration_status()` (`migration_runner.py`) never
actually produces. The real document uses `drifted_versions`/
`pending_versions` list fields and an `up_to_date` boolean instead, so
the original grep could never match anything, silently letting a
genuinely pending, drifted, or (for a rollback) schema-incompatible
migration state through undetected. Reproduced directly against the
real JSON shape before this script was written.

**Correction pass, item 6 -- the log-correlation query itself was
wrong**: the first version of this script correlated Log Analytics
results using `ContainerAppName_s == job_name`, which identifies the
*job*, not a specific *execution* of it -- every execution of the same
job shares the same `ContainerAppName_s`, so this filter alone could
never narrow results to the one execution just started; the script only
avoided returning stale data because it *also*, separately, re-checked
each candidate document's own parsed `execution_name` field. Microsoft's
own documented procedure for querying a specific job run's logs
(https://learn.microsoft.com/en-us/azure/container-apps/
jobs-get-started-cli#query-job-run-logs) correlates via
`ContainerGroupName_s startswith '$JOB_RUN_NAME'` instead -- fetched
directly from that page and reproduced here. The query below now uses
that same `ContainerGroupName_s startswith` filter as its primary,
execution-scoped correlation (with `ContainerAppName_s == job_name`
retained as a cheap, job-level narrowing filter, never the sole means of
correlation), and every interpolated identifier
(`job_name`/`execution_name`) is validated against a strict allowlist
pattern *before* being placed into the KQL query string -- a malformed
or control-character-bearing identifier is refused outright rather than
ever reaching string interpolation.

**Structural, not textual**: every check below parses real JSON and
inspects real fields/types -- never a regex over rendered text. Fails
closed (raises `CompatibilityCheckError`, mapped to a non-zero exit) on:
a job execution that never reaches a terminal status, or reaches
`Failed`; a malformed/non-JSON `az` response at any step; log output
that never correlates to the exact execution just started (or that
correlates to more than one distinct document, which is inherently
ambiguous); a status document missing a required key or bearing the
wrong type for one; and, of course, genuine pending/drifted/rollback-
incompatible (`unexpected_applied_versions`) migration state.

**Correlation, not merely a time window**: `ops_cli.py migration-status`
now embeds `execution_name` (Azure's own `CONTAINER_APP_JOB_EXECUTION_
NAME`, injected per-execution) in its single-line JSON output. This
script's own log query still bounds by a recent time window (Log
Analytics offers no other way to scope a query), but only ever *trusts*
a parsed document whose own `execution_name` field exactly equals the
execution this invocation itself started and polled to completion --
never merely "some migration-status output appeared recently."

**Bounded polling for Log Analytics ingestion delay**: a job execution
reaching `Succeeded` does not mean its own log line has already been
ingested into Log Analytics -- `poll_for_correlated_status` retries a
zero-match result (the execution succeeded, but nothing has correlated
*yet*) up to `max_log_poll_attempts` times, sleeping
`log_poll_interval_seconds` between attempts. An *ambiguous* result
(more than one distinct correlated document) fails immediately and is
never retried -- more polling cannot resolve an ambiguity, only
compound it by potentially surfacing a third, fourth, ... document.

Used identically for both `deploy` (the new candidate's own migrate job)
and `rollback` (the rollback target's own migrate job) -- the same
compatibility bar applies to both, and a rollback's own
`unexpected_applied_versions` check is exactly what catches "this older
image doesn't understand a migration a newer, already-deployed image
already applied."
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

_REQUIRED_KEYS = (
    "packaged_versions",
    "applied_versions",
    "pending_versions",
    "drifted_versions",
    "unexpected_applied_versions",
    "up_to_date",
)
_LIST_KEYS = ("pending_versions", "drifted_versions", "unexpected_applied_versions")

#: Azure Container Apps job/execution names are DNS-label-shaped
#: (lowercase alphanumeric and hyphens in practice), but this pattern is
#: deliberately a little more permissive (allowing `.`/`_` and mixed
#: case) while still excluding every character that could break out of a
#: single-quoted KQL string literal (`'`, backtick, pipe, newline,
#: whitespace, etc.) -- the goal is rejecting anything KQL-unsafe, not
#: reproducing Azure's own exact naming rules.
_KQL_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,253}$")


class CompatibilityCheckError(Exception):
    """Raised for any fail-closed condition below."""


def _validate_kql_safe_identifier(value: str, *, what: str) -> None:
    """Fails closed if `value` contains any character unsafe to
    interpolate directly into a KQL string literal -- called on every
    identifier `fetch_log_lines` places into its own query, *before*
    that query is ever constructed.
    """
    if not isinstance(value, str) or not _KQL_SAFE_IDENTIFIER_PATTERN.fullmatch(value):
        raise CompatibilityCheckError(
            f"{what} {value!r} contains characters unsafe to interpolate into a KQL query "
            "-- refusing to construct a query from an unvalidated identifier."
        )


def poll_execution_status(
    *,
    job_name: str,
    resource_group: str,
    execution_name: str,
    az_command: list[str] | None = None,
    poll_interval_seconds: float = 10,
    max_attempts: int = 30,
) -> str:
    """Polls `az containerapp job execution list` (structurally parsed,
    never grepped) until the named execution reaches a terminal status
    (`Succeeded`/`Failed`), or fails closed after `max_attempts`.
    """
    command_prefix = az_command or ["az"]
    for attempt in range(1, max_attempts + 1):
        try:
            result = subprocess.run(
                [
                    *command_prefix,
                    "containerapp",
                    "job",
                    "execution",
                    "list",
                    "--name",
                    job_name,
                    "--resource-group",
                    resource_group,
                    "-o",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CompatibilityCheckError("az containerapp job execution list timed out.") from exc
        if result.returncode != 0:
            raise CompatibilityCheckError(
                f"az containerapp job execution list failed: {result.stderr.strip()}"
            )
        try:
            executions = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CompatibilityCheckError(
                "az containerapp job execution list did not return valid JSON."
            ) from exc
        if not isinstance(executions, list):
            raise CompatibilityCheckError(
                "az containerapp job execution list returned an unexpected shape (not a list)."
            )
        matches = [e for e in executions if isinstance(e, dict) and e.get("name") == execution_name]
        if len(matches) > 1:
            raise CompatibilityCheckError(
                f"ambiguous: {len(matches)} executions named {execution_name!r} were returned."
            )
        if matches:
            status = matches[0].get("properties", {}).get("status")
            if status in ("Succeeded", "Failed"):
                return status
        if attempt < max_attempts:
            time.sleep(poll_interval_seconds)
    raise CompatibilityCheckError(
        f"execution {execution_name!r} did not reach a terminal status within "
        f"{max_attempts * poll_interval_seconds:.0f}s."
    )


def fetch_log_lines(
    *,
    job_name: str,
    resource_group: str,
    workspace_id: str,
    execution_name: str,
    az_command: list[str] | None = None,
    lookback_minutes: int = 10,
) -> list[str]:
    """Queries Log Analytics for this specific execution's console-log
    lines. Correlates primarily via `ContainerGroupName_s startswith
    execution_name` -- Microsoft's own documented per-execution log
    query shape -- with `ContainerAppName_s == job_name` retained as an
    additional, cheap job-level narrowing filter (never the sole
    correlation mechanism: a job's `ContainerAppName_s` is shared by
    every execution of that job). `extract_correlated_status`/
    `poll_for_correlated_status` below still perform the real,
    structural correlation check against each candidate line's own
    parsed `execution_name` field -- this query is a pre-filter, not the
    final authority.

    Both `job_name` and `execution_name` are validated against a strict,
    KQL-safe identifier pattern *before* being placed into the query
    string -- fails closed on anything containing a quote, backtick,
    pipe, newline, or other character that could otherwise alter the
    query's own structure.
    """
    _validate_kql_safe_identifier(job_name, what="job_name")
    _validate_kql_safe_identifier(execution_name, what="execution_name")

    command_prefix = az_command or ["az"]
    query = (
        f"ContainerAppConsoleLogs_CL "
        f"| where ContainerAppName_s == '{job_name}' "
        f"| where ContainerGroupName_s startswith '{execution_name}' "
        f"| where TimeGenerated > ago({lookback_minutes}m) "
        f"| order by TimeGenerated asc "
        f"| project Log_s"
    )
    try:
        result = subprocess.run(
            [
                *command_prefix,
                "monitor",
                "log-analytics",
                "query",
                "--workspace",
                workspace_id,
                "--analytics-query",
                query,
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CompatibilityCheckError("az monitor log-analytics query timed out.") from exc
    if result.returncode != 0:
        raise CompatibilityCheckError(
            f"az monitor log-analytics query failed: {result.stderr.strip()}"
        )
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CompatibilityCheckError(
            "az monitor log-analytics query did not return valid JSON."
        ) from exc
    if not isinstance(rows, list):
        raise CompatibilityCheckError(
            "az monitor log-analytics query returned an unexpected shape (not a list)."
        )
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or "Log_s" not in row:
            raise CompatibilityCheckError(
                "a log-analytics result row is missing the expected 'Log_s' field."
            )
        lines.append(row["Log_s"])
    return lines


def _correlated_documents(lines: list[str], *, execution_name: str) -> list[dict]:
    """Parses each candidate line as JSON and keeps only documents whose
    own `execution_name` field exactly equals `execution_name` -- never
    merely "appeared in the filtered query results." De-duplicates
    identical documents (Log Analytics can ingest the same line more
    than once) before returning.
    """
    matches: list[dict] = []
    for line in lines:
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(candidate, dict):
            continue
        if candidate.get("execution_name") != execution_name:
            continue
        matches.append(candidate)

    unique: list[dict] = []
    for match in matches:
        if match not in unique:
            unique.append(match)
    return unique


def extract_correlated_status(lines: list[str], *, execution_name: str) -> dict:
    """Single-shot correlation (no retry): fails closed if zero, or more
    than one *distinct*, correlated documents are found. See
    `poll_for_correlated_status` for the retrying variant used by
    `main`, which tolerates Log Analytics ingestion delay by retrying
    only the zero-match case.
    """
    unique = _correlated_documents(lines, execution_name=execution_name)

    if not unique:
        raise CompatibilityCheckError(
            f"no migration-status output correlated to execution {execution_name!r} was found "
            "in Log Analytics -- refusing to proceed without a confirmed compatibility result."
        )
    if len(unique) > 1:
        raise CompatibilityCheckError(
            f"ambiguous: {len(unique)} distinct migration-status documents correlated to "
            f"execution {execution_name!r} -- refusing to guess which is authoritative."
        )
    return unique[0]


def poll_for_correlated_status(
    *,
    job_name: str,
    resource_group: str,
    workspace_id: str,
    execution_name: str,
    az_command: list[str] | None = None,
    lookback_minutes: int = 10,
    log_poll_interval_seconds: float = 10,
    max_log_poll_attempts: int = 12,
) -> dict:
    """Correction pass, item 6: repeatedly fetches and structurally
    correlates log lines, tolerating Log Analytics ingestion delay --
    the job execution reaching `Succeeded` does not guarantee its own
    log line has already been ingested. Zero correlated matches is
    retried, up to `max_log_poll_attempts`; more than one distinct
    correlated match is inherently ambiguous and fails **immediately**,
    never retried (more polling cannot resolve an ambiguity, only risk
    compounding it with a third candidate).
    """
    for attempt in range(1, max_log_poll_attempts + 1):
        lines = fetch_log_lines(
            job_name=job_name,
            resource_group=resource_group,
            workspace_id=workspace_id,
            execution_name=execution_name,
            az_command=az_command,
            lookback_minutes=lookback_minutes,
        )
        unique = _correlated_documents(lines, execution_name=execution_name)
        if len(unique) > 1:
            raise CompatibilityCheckError(
                f"ambiguous: {len(unique)} distinct migration-status documents correlated to "
                f"execution {execution_name!r} -- refusing to guess which is authoritative."
            )
        if unique:
            return unique[0]
        if attempt < max_log_poll_attempts:
            time.sleep(log_poll_interval_seconds)
    raise CompatibilityCheckError(
        f"no migration-status output correlated to execution {execution_name!r} was found "
        f"in Log Analytics after {max_log_poll_attempts} attempts -- refusing to proceed "
        "without a confirmed compatibility result (this may indicate unusually long Log "
        "Analytics ingestion delay; investigate before retrying)."
    )


def validate_status_document(status: dict) -> None:
    """Structurally validates every required field and type, then
    enforces the actual compatibility bar: no pending, no drift, no
    unexpected-applied (rollback-incompatible) versions, and `up_to_date`
    itself must be exactly `True`.
    """
    missing = [key for key in _REQUIRED_KEYS if key not in status]
    if missing:
        raise CompatibilityCheckError(
            f"migration-status document is missing required key(s): {missing}"
        )
    if not isinstance(status["up_to_date"], bool):
        raise CompatibilityCheckError("'up_to_date' must be a boolean.")
    for key in _LIST_KEYS:
        if not isinstance(status[key], list):
            raise CompatibilityCheckError(f"{key!r} must be a list.")

    if status["pending_versions"]:
        raise CompatibilityCheckError(
            f"pending, unapplied migrations reported: {status['pending_versions']}"
        )
    if status["drifted_versions"]:
        raise CompatibilityCheckError(
            f"drifted (checksum-mismatched) migrations reported: {status['drifted_versions']}"
        )
    if status["unexpected_applied_versions"]:
        raise CompatibilityCheckError(
            "the database has applied migration version(s) this candidate does not package "
            f"-- a rollback-incompatible schema: {status['unexpected_applied_versions']}"
        )
    if status["up_to_date"] is not True:
        raise CompatibilityCheckError("'up_to_date' is not true.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--execution-name", required=True)
    parser.add_argument("--log-analytics-workspace-id", required=True)
    parser.add_argument("--poll-interval-seconds", type=float, default=10)
    parser.add_argument("--max-attempts", type=int, default=30)
    parser.add_argument("--lookback-minutes", type=int, default=10)
    parser.add_argument(
        "--log-poll-interval-seconds",
        type=float,
        default=10,
        help="Seconds to wait between Log Analytics correlation retries (ingestion delay).",
    )
    parser.add_argument(
        "--max-log-poll-attempts",
        type=int,
        default=12,
        help="Maximum Log Analytics correlation retries before failing closed.",
    )
    args = parser.parse_args(argv)

    try:
        final_status = poll_execution_status(
            job_name=args.job_name,
            resource_group=args.resource_group,
            execution_name=args.execution_name,
            poll_interval_seconds=args.poll_interval_seconds,
            max_attempts=args.max_attempts,
        )
        if final_status != "Succeeded":
            raise CompatibilityCheckError(
                f"execution {args.execution_name!r} did not succeed (status: {final_status})."
            )
        status = poll_for_correlated_status(
            job_name=args.job_name,
            resource_group=args.resource_group,
            workspace_id=args.log_analytics_workspace_id,
            execution_name=args.execution_name,
            lookback_minutes=args.lookback_minutes,
            log_poll_interval_seconds=args.log_poll_interval_seconds,
            max_log_poll_attempts=args.max_log_poll_attempts,
        )
        validate_status_document(status)
    except CompatibilityCheckError as exc:
        print(f"migration compatibility check failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"migration compatibility confirmed for execution {args.execution_name!r}: "
        "up_to_date=true, no pending/drifted/unexpected-applied migrations."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
