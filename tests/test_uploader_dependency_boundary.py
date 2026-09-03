"""Dependency-boundary proof (§10 of the Phase 4E task): importing the
CLI/uploader must never require installing the `api` optional-dependency
group. Runs a real, fresh subprocess (never the current, already-`api`-
extra-installed test process, whose `sys.modules` cache would hide a
real problem) that imports `cloudops_guard.cli` and inspects
`sys.modules` afterward -- proving `starlette`/`uvicorn`/`httpx`/`anyio`
were never imported as a side effect.
"""

from __future__ import annotations

import subprocess
import sys

_FORBIDDEN_MODULE_PREFIXES = ("starlette", "uvicorn", "httpx", "anyio")

_PROBE_SCRIPT = """
import sys
import json

import cloudops_guard.cli  # noqa: F401
import cloudops_guard.uploader.service  # noqa: F401
import cloudops_guard.uploader.local_report  # noqa: F401
import cloudops_guard.uploader.transport  # noqa: F401
import cloudops_guard.uploader.endpoint  # noqa: F401
import cloudops_guard.uploader.credentials  # noqa: F401
import cloudops_guard.uploader.confirmation  # noqa: F401
import cloudops_guard.uploader.response  # noqa: F401
import cloudops_guard.uploader.envelope  # noqa: F401

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
    import json

    return set(json.loads(result.stdout.strip().splitlines()[-1]))


class TestUploaderNeverImportsApiExtraDependencies:
    def test_importing_cli_and_every_uploader_module_pulls_in_none_of_the_api_extra(
        self,
    ) -> None:
        module_names = _run_probe_and_get_module_names()
        violations = {
            name
            for name in module_names
            if any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in _FORBIDDEN_MODULE_PREFIXES
            )
        }
        assert violations == set(), (
            f"importing the CLI/uploader pulled in api-extra-only modules: {violations}"
        )

    def test_the_probe_itself_did_import_rfc8785_and_pydantic(self) -> None:
        # A non-vacuous check on the probe above: proves it actually
        # exercised the fingerprint/report-validation code paths (which
        # legitimately do import rfc8785/pydantic, both base
        # dependencies) rather than trivially passing because nothing
        # real was imported at all.
        module_names = _run_probe_and_get_module_names()
        assert "rfc8785" in module_names
        assert "pydantic" in module_names
