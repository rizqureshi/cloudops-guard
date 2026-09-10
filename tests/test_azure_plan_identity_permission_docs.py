"""Correction pass, item 5: `AZURE_PLAN_CLIENT_ID` (the identity backing
`plan`'s own `az deployment group what-if` calls) was documented as
needing only `Microsoft.Resources/deployments/whatIf/action` plus a
wildcard `*/read` -- "provably broader than Reader," but still an
understatement. Microsoft's own documentation
(https://learn.microsoft.com/en-us/azure/azure-resource-manager/
templates/deploy-what-if#required-permissions) states plainly: "The
what-if operation has the same permission requirements" as an actual
deployment -- write access on every resource type being evaluated, not
merely a narrow `whatIf/action` grant. This file structurally verifies
the corrected documentation and workflow comments never re-describe this
identity as "Reader-scoped" or read-only, do state the actual permission
model (including the sensitive `Microsoft.Authorization/
roleAssignments/write` permission `foundation.bicep`'s own role
assignments require), and do require `ingestion-azure-plan` to be a
genuinely protected GitHub Environment -- never merely asserted in a
comment, but checked with real regex/substring searches against the real
files.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "deployment" / "azure-ingestion-production.md"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-ingestion-azure.yml"


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _every_occurrence_is_negated(text: str, phrase: str, *, window: int = 80) -> bool:
    """A doc/comment is allowed to *mention* a wrong claim while
    explicitly disclaiming it ("must never be described as X") -- what
    it must never do is *assert* the claim unqualified. Returns True
    only if every occurrence of `phrase` has an explicit negation word
    ("never"/"not"/"don't") within `window` characters before it.
    """
    negations = ("never", "not ", "don't", "must never")
    start = 0
    found_any = False
    while True:
        index = text.find(phrase, start)
        if index == -1:
            break
        found_any = True
        preceding = text[max(0, index - window) : index].lower()
        if not any(neg in preceding for neg in negations):
            return False
        start = index + len(phrase)
    return found_any or True  # vacuously true if the phrase never appears at all


class TestNeverDescribedAsReaderOrReadOnly:
    def test_documentation_never_asserts_reader_scoped_unqualified(self) -> None:
        assert _every_occurrence_is_negated(_doc_text(), "Reader-scoped")

    def test_workflow_never_asserts_reader_scoped_unqualified(self) -> None:
        assert _every_occurrence_is_negated(_workflow_text(), "Reader-scoped")

    def test_documentation_never_calls_the_plan_identity_read_only(self) -> None:
        text = _doc_text()
        # A narrow, targeted check: the specific phrase this correction
        # pass removed ("it's just a preview"/"read-only browsing"
        # framing for AZURE_PLAN_CLIENT_ID specifically) must be gone --
        # never a blanket ban on the substring "read-only" anywhere in a
        # large document that legitimately uses it elsewhere (e.g. for
        # the genuinely read-only GitLab/Kubernetes audit invariants).
        assert "read-only browsing" not in text
        assert "no required reviewers (What-If" not in text


class TestActualPermissionModelIsDocumented:
    def test_documentation_quotes_the_same_permission_requirements_finding(self) -> None:
        text = _doc_text()
        assert "same permission requirements" in text

    def test_documentation_names_the_sensitive_role_assignment_write_permission(self) -> None:
        """The custom role must not be understated by omitting the one
        genuinely sensitive permission it requires: `foundation.bicep`
        creates role assignments, so the plan identity's own custom role
        must include `Microsoft.Authorization/roleAssignments/write` --
        an access-granting permission in its own right, not merely
        "can create infrastructure."
        """
        assert "Microsoft.Authorization/roleAssignments/" in _doc_text()

    def test_documentation_lists_deployments_wildcard_not_only_whatif_action(self) -> None:
        assert "Microsoft.Resources/deployments/*" in _doc_text()

    def test_workflow_comment_cites_the_real_microsoft_learn_url(self) -> None:
        text = _workflow_text()
        assert "deploy-what-if#required-permissions" in text


class TestPlanEnvironmentMustBeProtected:
    def test_documentation_requires_ingestion_azure_plan_to_be_protected(self) -> None:
        text = _doc_text()
        assert "protected GitHub Environment" in text
        assert "required reviewers" in text

    def test_workflow_comment_requires_a_protected_stage_mirroring_production(self) -> None:
        text = _workflow_text()
        assert "protected" in text.lower()
        assert "required reviewers" in text


class TestNeverTestThePlanIdentityWithARealDeployment:
    """Correction pass, item 5: an earlier version of this same
    documentation and workflow comment instructed Phase 4G-B to
    "confirm a real `az deployment group create` attempted under this
    same identity" as a way of *testing* the plan identity -- a garbled,
    actively wrong instruction (What-If's own non-mutating nature is the
    entire point of using it; deliberately running a real mutating
    deployment under a stage that is supposed to remain non-mutating
    defeats that). The corrected procedure is entirely read-only: a real
    `az role assignment list` inspection plus a real (still non-mutating)
    `az deployment group what-if`.
    """

    #: The exact, directly-reproduced shape of the garbled instruction
    #: this test guards against -- "deployment group create" and
    #: "attempted under ... identity" within the same short span, in
    #: either order. A fixed-size textual proximity window (checked only
    #: *before* the match) previously missed this precisely because the
    #: real regression's own "this same identity" phrasing came *after*
    #: "deployment group create" in the sentence, farther back than any
    #: reasonably-sized preceding window reached the bullet's own
    #: `AZURE_PLAN_CLIENT_ID` heading -- confirmed by mutation-testing
    #: this exact fixture text and observing the original version of
    #: this check fail to catch it.
    _DANGEROUS_INSTRUCTION_PATTERN = re.compile(
        r"deployment group create[^.]{0,150}"
        r"attempted under (?:this|the) (?:same )?(?:plan )?identity"
        r"|attempted under (?:this|the) (?:same )?(?:plan )?identity"
        r"[^.]{0,150}deployment group create",
        re.IGNORECASE | re.DOTALL,
    )

    def _occurrences_near_plan_identity_are_negated(self, text: str) -> bool:
        # Markdown/YAML comments wrap prose across lines with indentation,
        # so a phrase spanning a line break has a newline (plus leading
        # whitespace) where a literal single space would appear in the
        # unwrapped sentence -- collapse all whitespace runs to one space
        # before matching, or a wrapped occurrence of the exact dangerous
        # phrase would silently evade this check (confirmed directly: the
        # real regression text wraps exactly between "same" and
        # "identity", and the unnormalized pattern missed it).
        normalized = re.sub(r"\s+", " ", text)
        return not self._DANGEROUS_INSTRUCTION_PATTERN.search(normalized)

    def test_documentation_never_instructs_testing_the_plan_identity_with_a_real_deployment(
        self,
    ) -> None:
        assert self._occurrences_near_plan_identity_are_negated(_doc_text())

    def test_workflow_comment_never_instructs_testing_the_plan_identity_with_a_real_deployment(
        self,
    ) -> None:
        assert self._occurrences_near_plan_identity_are_negated(_workflow_text())

    def test_documentation_states_the_real_read_only_validation_procedure(self) -> None:
        text = _doc_text()
        assert "role assignment list" in text
        assert "read-only means" in text or "entirely read-only" in text

    def test_workflow_comment_states_the_real_read_only_validation_procedure(self) -> None:
        text = _workflow_text()
        assert "role assignment list" in text
        assert "read-only" in text


class TestBlockerListReflectsTheCorrection:
    def test_known_blockers_section_no_longer_understates_the_permission_gap(self) -> None:
        text = _doc_text()
        match = re.search(
            r"AZURE_PLAN_CLIENT_ID.{0,400}",
            text[text.index("## 11. Known Phase 4G-B blockers") :],
            re.DOTALL,
        )
        assert match is not None, "expected an AZURE_PLAN_CLIENT_ID blocker entry in §11"
        blocker_text = match.group(0)
        assert "same write permissions as an" in blocker_text
