"""Shared fixtures for the Phase 4G-A Azure-adapter test suite.

**Local/offline only** -- every fixture here talks to a real, local
PostgreSQL instance or a real, local Azurite (Azure Storage emulator)
instance, never a real Azure subscription. See this repository's Phase
4G-A report for exactly how these were started for this pass (plain
`docker run`, no orchestration file committed).

**Skip vs. fail-loud, by design (task 12: "must not silently skip
merely because a developer lacks a local database")**: outside the
dedicated Azure-adapters CI job, these fixtures skip cleanly when the
required service is unreachable (a developer running the general
`pytest` suite locally should not be forced to have Postgres/Azurite
running). Inside that dedicated job, `COG_AZURE_TESTS_REQUIRED=1` is set
in the environment, and an unreachable service becomes a hard failure
instead -- see `test_azure_tests_are_not_silently_skipped_in_ci` below,
which itself fails loudly if that job's own environment variables are
ever missing.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import psycopg
import pytest
from azure.storage.blob import BlobServiceClient

from cloudops_guard.ingestion_azure.migration_runner import run_migrations

_REQUIRED_ENV_VAR = "COG_AZURE_TESTS_REQUIRED"

DEFAULT_TEST_POSTGRES_CONNINFO = "postgresql://postgres:testpass@localhost:15432/ingestion_test"
DEFAULT_TEST_AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:15010/devstoreaccount1;"
)


def _tests_are_required() -> bool:
    return os.environ.get(_REQUIRED_ENV_VAR) == "1"


@pytest.fixture(scope="session")
def postgres_admin_conninfo() -> str:
    """The base connection string, pointed at a real Postgres server --
    used only to create/drop a fresh, isolated per-test-session database
    (`_new_test_database`) so tests never share mutable schema state with
    each other or with the manual smoke-testing performed during this
    pass's own implementation.
    """
    conninfo = os.environ.get("COG_TEST_POSTGRES_CONNINFO", DEFAULT_TEST_POSTGRES_CONNINFO)
    try:
        with psycopg.connect(conninfo, connect_timeout=3):
            pass
    except psycopg.OperationalError:
        if _tests_are_required():
            pytest.fail(
                f"{_REQUIRED_ENV_VAR}=1 but PostgreSQL at {conninfo!r} (from "
                "COG_TEST_POSTGRES_CONNINFO) is unreachable -- the dedicated Azure-adapters "
                "CI job must never silently skip these tests."
            )
        pytest.skip("no local PostgreSQL reachable -- set COG_TEST_POSTGRES_CONNINFO to run these.")
    return conninfo


@pytest.fixture()
def postgres_conninfo(postgres_admin_conninfo: str) -> str:
    """A fresh, uniquely-named, migrated database for exactly one test --
    tests never share mutable rows with each other, and each test starts
    from a known-clean schema (migrations applied, no data).
    """
    db_name = f"cog_test_{uuid.uuid4().hex[:16]}"
    with psycopg.connect(postgres_admin_conninfo, autocommit=True) as admin_conn:
        admin_conn.execute(f'CREATE DATABASE "{db_name}"')

    base = postgres_admin_conninfo.rsplit("/", 1)[0]
    test_conninfo = f"{base}/{db_name}"
    run_migrations(test_conninfo)

    yield test_conninfo

    with psycopg.connect(postgres_admin_conninfo, autocommit=True) as admin_conn:
        admin_conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (db_name,),
        )
        admin_conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')


@pytest.fixture()
def azurite_container_client():
    """A fresh, uniquely-named blob container against a real, local
    Azurite instance -- deleted after the test.
    """
    connection_string = os.environ.get(
        "COG_TEST_AZURITE_CONNECTION_STRING", DEFAULT_TEST_AZURITE_CONNECTION_STRING
    )
    try:
        client = BlobServiceClient.from_connection_string(connection_string)
        client.get_account_information()
    except Exception:
        if _tests_are_required():
            pytest.fail(
                f"{_REQUIRED_ENV_VAR}=1 but Azurite (from COG_TEST_AZURITE_CONNECTION_STRING) "
                "is unreachable -- the dedicated Azure-adapters CI job must never silently "
                "skip these tests."
            )
        pytest.skip(
            "no local Azurite reachable -- set COG_TEST_AZURITE_CONNECTION_STRING to run these."
        )

    container_name = f"cog-test-{uuid.uuid4().hex[:16]}"
    container = client.create_container(container_name)
    yield client, container_name
    container.delete_container()


def test_azure_tests_are_not_silently_skipped_in_ci() -> None:
    """A canary, not a fixture-consuming test: if the dedicated
    Azure-adapters CI job is running (`COG_AZURE_TESTS_REQUIRED=1`) but
    forgot to also set the connection-string environment variables, every
    `postgres`/`azurite`-marked test would otherwise `pytest.skip` (via
    the fixtures above using their own hidden defaults) rather than fail
    loudly -- defeating task 12's "must not silently skip" requirement in
    exactly the case that matters most. This test fails immediately, with
    a specific message, if that misconfiguration exists.
    """
    if not _tests_are_required():
        return
    missing = [
        name
        for name in ("COG_TEST_POSTGRES_CONNINFO", "COG_TEST_AZURITE_CONNECTION_STRING")
        if not os.environ.get(name)
    ]
    assert not missing, (
        f"{_REQUIRED_ENV_VAR}=1 but the following required environment variable(s) are "
        f"unset, which would let postgres/azurite-marked tests silently skip instead of "
        f"running for real: {missing}"
    )


def test_bicep_cli_is_required_and_present_in_ci() -> None:
    """Correction-pass item 5: the same canary pattern as
    `test_azure_tests_are_not_silently_skipped_in_ci` above, for the
    Bicep CLI. `test_bicep_infrastructure.py` applies its own
    module-level `pytestmark = pytest.mark.skipif(bicep not found)` so a
    developer without Bicep installed isn't blocked from running the
    rest of the suite -- but that same module-level marker would also
    have skipped a canary defined *inside* that file, which is exactly
    why this one lives here in `conftest.py` instead (no module-level
    skip marker applies to this file, so this test is never itself
    skipped by the condition it exists to check).

    The original defect this guards against was the *opposite* of a
    missing-binary problem: the deployment workflow's `verify` job
    installed Bicep *after* running pytest, so `test_bicep_
    infrastructure.py`'s entire compiled-template test suite silently
    skipped on every single CI run without anyone noticing -- reproduced
    directly by re-reading the job's own step order before this fix.
    Inside that job (`COG_AZURE_TESTS_REQUIRED=1`), a missing Bicep CLI
    must now be a hard, loud failure instead of a silent skip.
    """
    if not _tests_are_required():
        return
    bicep = shutil.which("bicep") or (
        str(Path.home() / ".local" / "bin" / "bicep")
        if (Path.home() / ".local" / "bin" / "bicep").is_file()
        else None
    )
    assert bicep is not None, (
        f"{_REQUIRED_ENV_VAR}=1 but no Bicep CLI was found -- the dedicated verify job must "
        "install Bicep BEFORE running pytest, never after (which silently skips every "
        "compiled-template test in test_bicep_infrastructure.py)."
    )
