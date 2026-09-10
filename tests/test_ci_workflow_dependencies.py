"""CI-restoration pass: `.github/workflows/ci.yml`'s normal CI run
previously installed only the `dev` and `api` extras
(`uv sync --locked --extra dev --extra api`), but
`tests/ingestion_azure/conftest.py` imports `psycopg` (part of the
`azure-production` extra) unconditionally at collection time -- Phase 4G-A
added that test package and its dependency without ever updating this
workflow's own install step. Reproduced directly: a clean
`uv sync --locked --extra dev --extra api` virtual environment left
`psycopg` uninstalled, and collecting `tests/ingestion_azure` failed with
`ModuleNotFoundError: No module named 'psycopg'` -- the exact failure
GitHub Actions run 34521414314 hit on a real push to `main`.

This file parses the real, shipped YAML (via PyYAML, never a hand-retyped
paraphrase) and proves the fix -- never re-implementing or guessing at the
command's own logic.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_REQUIRED_EXTRAS = {"dev", "api", "azure-production"}


def _load_ci_workflow() -> dict:
    with open(CI_WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def _find_step(job_name: str, step_name: str) -> dict:
    workflow = _load_ci_workflow()
    job = workflow["jobs"][job_name]
    for step in job["steps"]:
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"step {step_name!r} not found in job {job_name!r}")


def _extras_in_command(command_tokens: list[str]) -> set[str]:
    extras: set[str] = set()
    for i, token in enumerate(command_tokens):
        if token == "--extra":
            extras.add(command_tokens[i + 1])
    return extras


class TestLockedInstallStepIncludesEveryRequiredExtra:
    """Task requirement: prove the locked dependency-install step includes
    all three currently test-required extras (`dev`, `api`,
    `azure-production`), still uses `--locked`, and never becomes an
    unlocked install.
    """

    def test_install_step_uses_uv_sync_with_locked(self) -> None:
        step = _find_step("test", "Install locked dependencies")
        tokens = shlex.split(step["run"])
        assert tokens[:2] == ["uv", "sync"], (
            f"expected the install step to run 'uv sync ...', got: {step['run']!r}"
        )
        assert "--locked" in tokens, (
            "the install step must never become an unlocked install "
            "('--locked' is required, per the task's own explicit boundary)"
        )

    def test_install_step_includes_exactly_the_required_extras(self) -> None:
        step = _find_step("test", "Install locked dependencies")
        tokens = shlex.split(step["run"])
        extras = _extras_in_command(tokens)
        assert extras == _REQUIRED_EXTRAS, (
            f"expected exactly {_REQUIRED_EXTRAS!r}, found {extras!r} in "
            f"the real install command: {step['run']!r}"
        )

    def test_azure_production_extra_specifically_is_present(self) -> None:
        """The single, minimal fix this pass makes -- isolated as its own
        assertion so a future regression naming exactly this extra is
        unambiguous about what broke.
        """
        step = _find_step("test", "Install locked dependencies")
        tokens = shlex.split(step["run"])
        assert "azure-production" in _extras_in_command(tokens), (
            "tests/ingestion_azure/conftest.py imports psycopg (the "
            "azure-production extra) unconditionally at collection time -- "
            "normal CI must install it or every test in that package fails "
            "to even collect"
        )


class TestRunTestsStepStillRunsTheFullSuiteUnmodified:
    """Task requirement: prove the test step remains present, still runs
    the full pytest suite, and that no test-selection, skip, deselection,
    or ignored-failure option was introduced by this narrow fix.
    """

    def test_run_tests_step_exists_in_the_same_job(self) -> None:
        # Raises AssertionError (failing this test) if the step is missing.
        _find_step("test", "Run tests")

    def test_run_tests_step_runs_the_full_suite_with_no_selection_or_skip_flags(
        self,
    ) -> None:
        step = _find_step("test", "Run tests")
        tokens = shlex.split(step["run"])
        assert tokens == ["uv", "run", "pytest"], (
            "the test step must run the complete suite with no extra "
            f"arguments -- got: {step['run']!r}"
        )
        forbidden_substrings = [
            "-k ",
            "-m ",
            "--deselect",
            "--ignore",
            "-x",
            "--maxfail",
            "-p no:",
            "skip",
        ]
        lowered = step["run"].lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in lowered, (
                f"the test step's command must never introduce a "
                f"test-selection/skip/deselection flag -- found {forbidden!r} "
                f"in: {step['run']!r}"
            )

    def test_run_tests_step_never_ignores_its_own_failure(self) -> None:
        step = _find_step("test", "Run tests")
        assert step.get("continue-on-error") is not True, (
            "the test step must never be allowed to fail silently"
        )
