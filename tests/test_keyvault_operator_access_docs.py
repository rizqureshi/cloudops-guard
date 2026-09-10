"""Correction pass, item 4: `infra/azure/modules/keyvault.bicep` sets
`publicNetworkAccess: 'Disabled'`, reachable only through its own
private endpoint inside the VNet -- but the Phase 4G-B provisioning
procedure instructed an operator to run `az keyvault secret set`
without ever naming any approved, network-reachable place to run it
from. Reproduced directly by reading the pre-fix `docs/deployment/
azure-ingestion-production.md` §9 step 5, which described populating
secrets with no mention of network reachability at all -- an operator
following it literally from their own laptop or an ordinary GitHub-
hosted Actions runner would have every command silently fail (or,
worse, an undocumented workaround could tempt someone to flip
`publicNetworkAccess` to enabled, defeating the private-only design
`docs/deployment/azure-ingestion-production.md` §2/§4 already approved).

This file structurally verifies: (1) the documentation now explicitly
names this as an unresolved Phase 4G-B precondition requiring a human
choice among compared options; (2) it never claims ordinary local or
GitHub-hosted execution can reach the private vault; (3) it never
suggests temporarily enabling public Key Vault access as a workaround;
(4) the authorization checklist carries this as an unchecked
precondition; (5) the pilot runbook cross-references the same
unresolved gap rather than silently assuming it away.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT_DOC = REPO_ROOT / "docs" / "deployment" / "azure-ingestion-production.md"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "deployment" / "ingestion-production.md"
CHECKLIST_DOC = REPO_ROOT / "docs" / "pilots" / "phase-4g-authorization-checklist.md"
RUNBOOK_DOC = REPO_ROOT / "docs" / "pilots" / "ingestion-pilot-runbook.md"
KEYVAULT_BICEP = REPO_ROOT / "infra" / "azure" / "modules" / "keyvault.bicep"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    """Collapses whitespace runs to a single space -- markdown/YAML prose
    wraps across lines, so a phrase spanning a line break has a newline
    (plus leading indentation) where a literal single space would appear
    in the unwrapped sentence. A regex/substring check against the raw
    text can silently miss an occurrence that happens to wrap mid-phrase.
    """
    return re.sub(r"\s+", " ", _text(path))


class TestKeyVaultIsActuallyPrivateOnly:
    """Confirms the premise this whole correction item depends on --
    if the vault's own Bicep ever stopped being private-only, every
    other test in this file would be checking a now-moot concern.
    """

    def test_key_vault_bicep_disables_public_network_access(self) -> None:
        text = _text(KEYVAULT_BICEP)
        assert "publicNetworkAccess: 'Disabled'" in text


class TestDocumentationNamesTheUnresolvedPrecondition:
    def test_deployment_doc_names_this_an_unresolved_precondition(self) -> None:
        text = _text(DEPLOYMENT_DOC)
        assert "Unresolved Phase 4G-B precondition" in text or "unresolved" in text.lower()
        assert "operator-access mechanism" in text or "operator-access" in text

    def test_deployment_doc_compares_at_least_three_options(self) -> None:
        text = _text(DEPLOYMENT_DOC)
        assert "ephemeral in-VNet operator VM" in text
        assert "self-hosted" in text.lower() and "runner" in text.lower()
        assert "private-connectivity path" in text or "VPN" in text or "Bastion" in text

    def test_deployment_doc_records_rbac_logging_teardown_and_cost_for_options(self) -> None:
        """Task item 4's own explicit requirement: "Record RBAC,
        logging, teardown, secret-handling, and additional monthly/
        temporary cost implications."
        """
        text = _normalized(DEPLOYMENT_DOC)
        match = re.search(r"operator-?\s*access mechanism must exist", text)
        assert match is not None
        section = text[match.start() : match.start() + 6000]
        for required_term in ("RBAC", "Logging", "Teardown", "Secret handling", "cost"):
            assert required_term.lower() in section.lower(), (
                f"expected {required_term!r} to be discussed in the operator-access "
                "comparison section"
            )

    def test_deployment_doc_requires_an_explicit_human_choice(self) -> None:
        text = _text(DEPLOYMENT_DOC)
        assert "human must explicitly choose" in text or "explicit, separate human choice" in text

    def test_deployment_doc_blocker_list_includes_this_item(self) -> None:
        text = _text(DEPLOYMENT_DOC)
        blockers_section = text[text.index("## 11. Known Phase 4G-B blockers") :]
        assert "operator-access mechanism is unresolved" in blockers_section

    def test_cost_section_flags_the_pending_operator_access_cost(self) -> None:
        text = _text(DEPLOYMENT_DOC)
        cost_section = text[
            text.index("## 5. Cost estimate") : text.index("## 6.")
            if "## 6." in text
            else len(text)
        ]
        assert "operator-access mechanism" in cost_section


class TestNeverClaimsOrdinaryExecutionCanReachThePrivateVault:
    """The specific failure mode this pass closes: a runbook or doc that
    describes populating a secret without ever addressing network
    reachability reads, to a literal operator, as an implicit claim that
    ordinary execution (their own laptop, a GitHub-hosted runner) will
    work. Checked as an explicit *disclaimer* requirement, not merely
    "the phrase never appears" (both docs must legitimately discuss
    GitHub-hosted runners elsewhere, e.g. for the workflow itself).
    """

    def test_deployment_doc_explicitly_disclaims_github_hosted_and_local_reachability(
        self,
    ) -> None:
        text = _normalized(DEPLOYMENT_DOC)
        assert re.search(
            r"no ordinary local (?:machine )?or github-hosted (?:execution|runner)",
            text,
            re.IGNORECASE,
        ), (
            "expected an explicit disclaimer that ordinary local/GitHub-hosted "
            "execution cannot reach the vault"
        )

    def test_runbook_cross_references_the_same_unresolved_gap(self) -> None:
        text = _normalized(RUNBOOK_DOC).lower()
        assert (
            "private network access" in text
            or "private-endpoint" in text
            or "private endpoint" in text
        )
        assert "unresolved" in text

    def test_never_suggests_temporarily_enabling_public_key_vault_access(self) -> None:
        # A targeted structural check: the specific dangerous instruction
        # shape ("enable public...access" as something to *do*, not
        # something to *avoid*) must never appear unnegated -- markdown's
        # own `**not**` bold-emphasis markers mean "not" is not always
        # followed by a literal space, so the negation check itself must
        # tolerate that rather than requiring "not " with a trailing space.
        negation_pattern = re.compile(r"never|not\b|don't|must never", re.IGNORECASE)
        for path in (DEPLOYMENT_DOC, ARCHITECTURE_DOC, RUNBOOK_DOC, CHECKLIST_DOC):
            text = _normalized(path)
            for match in re.finditer(
                r"enable[^.]{0,60}public[^.]{0,30}access", text, re.IGNORECASE
            ):
                window = text[max(0, match.start() - 80) : match.start()]
                assert negation_pattern.search(window), (
                    f"found an unqualified instruction to enable public access in {path.name}: "
                    f"{match.group(0)!r}"
                )


class TestAuthorizationChecklistCarriesTheUncheckedPrecondition:
    def test_checklist_has_an_unchecked_operator_access_precondition(self) -> None:
        text = _text(CHECKLIST_DOC)
        match = re.search(
            r"- \[( |x)\] \*\*Private Key Vault operator-access mechanism decision\*\*",
            text,
        )
        assert match is not None, "expected an explicit operator-access precondition checkbox"
        assert match.group(1) == " ", "the operator-access precondition must be unchecked"

    def test_checklist_preconditions_intro_mentions_three_items_not_two(self) -> None:
        text = _text(CHECKLIST_DOC)
        intro_section = text[: text.index("## Phase 4G execution checklist")]
        assert "three items" in intro_section
        assert "two items" not in intro_section
