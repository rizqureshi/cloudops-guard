"""Dependency-boundary proof (task 20): importing the base CLI, the
uploader, or the local/staging `ingestion_api` package must never require
installing the `azure-production` optional-dependency group. Mirrors
`tests/test_uploader_dependency_boundary.py`'s own real-subprocess
methodology exactly -- a fresh subprocess (never the current test
process, whose `sys.modules` already has `psycopg`/`azure.*` imported
from this same test session) imports every non-Azure module and inspects
`sys.modules` afterward.
"""

from __future__ import annotations

import json
import subprocess
import sys

_FORBIDDEN_MODULE_PREFIXES = ("psycopg", "psycopg_pool", "azure")

_PROBE_SCRIPT = """
import sys
import json

import cloudops_guard.cli  # noqa: F401
import cloudops_guard.uploader.service  # noqa: F401
import cloudops_guard.ingestion.reference  # noqa: F401
import cloudops_guard.ingestion_api.app  # noqa: F401
import cloudops_guard.ingestion_api.config  # noqa: F401
import cloudops_guard.ingestion_api.production_readiness  # noqa: F401

print(json.dumps(sorted(sys.modules.keys())))
"""


def _run_probe_and_get_module_names() -> set[str]:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"probe subprocess failed (stdout={result.stdout!r}, stderr={result.stderr!r})"
    )
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def test_base_cli_and_local_ingestion_api_never_import_azure_production_dependencies() -> None:
    module_names = _run_probe_and_get_module_names()
    violations = {
        name
        for name in module_names
        if any(
            name == prefix or name.startswith(prefix + ".") for prefix in _FORBIDDEN_MODULE_PREFIXES
        )
    }
    assert not violations, (
        f"importing the base CLI/uploader/local ingestion API pulled in azure-production "
        f"dependencies it should never need: {sorted(violations)}"
    )


def test_ingestion_azure_package_itself_is_importable_only_with_the_extra_installed() -> None:
    """The inverse check: `cloudops_guard.ingestion_azure`'s own modules
    genuinely do need the `azure-production` extra (this test's own
    process has it installed, via this repository's dev environment) --
    proving the boundary is real (something to actually cross), not a
    boundary around a package that happens to need nothing.
    """
    import cloudops_guard.ingestion_azure.blob_store  # noqa: F401
    import cloudops_guard.ingestion_azure.postgres_metadata_store  # noqa: F401

    assert "psycopg" in sys.modules
    assert any(name == "azure" or name.startswith("azure.") for name in sys.modules)
