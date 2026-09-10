"""Correction pass, item 6: prevents a checked cost/budget authorization
precondition whenever the documented credible cost upper bound exceeds
the recorded budget ceiling. This is a durable regression test, not a
one-time fix verification -- it re-derives both numbers from the real,
live documents/Bicep default on every run, so it will fail again in the
future if either figure changes without the other being reconciled
(e.g. a human raises the budget ceiling without updating this checklist,
or a future cost recalculation raises the upper bound without anyone
re-checking the authorization checklist's own precondition box).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AZURE_DEPLOYMENT_DOC = REPO_ROOT / "docs" / "deployment" / "azure-ingestion-production.md"
PHASE_4G_CHECKLIST = REPO_ROOT / "docs" / "pilots" / "phase-4g-authorization-checklist.md"
BUDGET_BICEP = REPO_ROOT / "infra" / "azure" / "modules" / "budget.bicep"


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file does not exist: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def azure_deployment_doc_text() -> str:
    return _read(AZURE_DEPLOYMENT_DOC)


@pytest.fixture(scope="module")
def phase_4g_checklist_text() -> str:
    return _read(PHASE_4G_CHECKLIST)


def _configured_budget_ceiling() -> int:
    text = _read(BUDGET_BICEP)
    match = re.search(r"param monthlyAmount int = (\d+)", text)
    assert match is not None, "could not find budget.bicep's monthlyAmount default"
    return int(match.group(1))


def _documented_cost_upper_bound(azure_deployment_doc_text: str) -> int:
    """Extracts the upper bound of the `~$X–Y/month` total figure from
    §5's own cost table (e.g. `**~$58–124/month**` -> `124`). Fails
    loudly if the document's own total-cost figure can't be found or
    parsed, rather than silently assuming any particular value.
    """
    match = re.search(r"~\$(\d+)–(\d+)/month", azure_deployment_doc_text)
    assert match is not None, (
        "could not find the '~$LOW–HIGH/month' total cost figure in "
        "docs/deployment/azure-ingestion-production.md -- has its format changed?"
    )
    return int(match.group(2))


def _cost_budget_precondition_is_checked(phase_4g_checklist_text: str) -> bool:
    match = re.search(
        r"- \[([ x])\] \*\*Provider-specific cost estimate and budget approval\*\*",
        phase_4g_checklist_text,
    )
    assert match is not None, (
        "could not find the 'Provider-specific cost estimate and budget approval' "
        "precondition checkbox in the Phase 4G authorization checklist"
    )
    return match.group(1) == "x"


class TestCostBudgetPreconditionReflectsRealFigures:
    def test_documented_upper_bound_and_configured_ceiling_are_both_parseable(
        self, azure_deployment_doc_text: str
    ) -> None:
        """Sanity check that the extraction logic itself works against
        the real, current documents -- a prerequisite for the real test
        below to mean anything.
        """
        assert _configured_budget_ceiling() > 0
        assert _documented_cost_upper_bound(azure_deployment_doc_text) > 0

    def test_precondition_is_unchecked_whenever_upper_bound_exceeds_ceiling(
        self, azure_deployment_doc_text: str, phase_4g_checklist_text: str
    ) -> None:
        """The exact guarantee correction-pass item 6 requires: this
        project's own authorization checklist must never claim the
        cost/budget precondition is satisfied while its own documented
        worst-case cost genuinely exceeds the approved ceiling.
        """
        ceiling = _configured_budget_ceiling()
        upper_bound = _documented_cost_upper_bound(azure_deployment_doc_text)
        checked = _cost_budget_precondition_is_checked(phase_4g_checklist_text)

        if upper_bound > ceiling:
            assert not checked, (
                f"the documented cost upper bound (${upper_bound}/month) exceeds the "
                f"configured budget ceiling (${ceiling}/month), but the authorization "
                "checklist's cost/budget precondition is still checked -- it must be "
                "unchecked until a human re-approves either a higher ceiling or a "
                "lower-cost architecture."
            )

    def test_current_real_state_is_the_known_unresolved_case(
        self, azure_deployment_doc_text: str, phase_4g_checklist_text: str
    ) -> None:
        """Pins today's actual, known state (upper bound $124 > ceiling
        $100, precondition unchecked) so a *silent* edit to either
        document that accidentally makes them "coincidentally consistent
        again" without a real human re-decision is still caught --
        this test fails the moment either number changes, forcing an
        explicit, reviewed update to this test alongside the real
        decision, never a silent drift.
        """
        assert _configured_budget_ceiling() == 100
        assert _documented_cost_upper_bound(azure_deployment_doc_text) == 124
        assert _cost_budget_precondition_is_checked(phase_4g_checklist_text) is False
