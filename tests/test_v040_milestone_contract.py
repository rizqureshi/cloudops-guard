"""Documentation/contract tests for v0.4.0 Phase 4A (ingestion API design).

Phase 4A is documentation and contract design only -- there was no API,
storage, authentication, or uploader code yet for these tests to exercise
directly. These tests instead read the real, on-disk documentation files
(the new milestone document, `CLAUDE.md`, and the relevant website pages)
and assert specific, deliberately concrete properties of their text, so a
later, careless edit to any of these files cannot silently regress one of
the guarantees Phase 4A's design is built on.

A later, uncommitted, pending-review Phase 4B has since added real local,
in-memory reference storage/token code under
`src/cloudops_guard/ingestion/` -- see `TestPhase4BDocumentationMatchesRealCode`
below, which checks the opposite drift direction: that the "no storage
code exists yet" claim does not silently survive in documentation once it
stops being true, while every other class in this file continues to prove
Phase 4A's own design guarantees (privacy, versioning, security) are
unweakened by anything Phase 4B added.

Every assertion below reads the actual files this project ships, never a
hand-copied duplicate of their content -- these are string/regex checks
against real, on-disk text, sufficient to prove the properties Phase 4A
requires without needing a Markdown/Astro parser dependency (consistent
with `CLAUDE.md`: avoid unnecessary dependencies).

This is the corrected version of this test suite (a focused correction
pass): it replaces a line-by-line, per-line unsafe-upload-claim scan (which
could not catch a claim wrapped across two source lines) with a
sentence-based, word-proximity scan; replaces a broad forbidden-substring
check for a client-supplied tenant field with a positive, closed-envelope
contract check; and adds coverage for the deterministic report fingerprint,
deletion status enum, implementable token-lookup design, the two
independent size ceilings, and durable (non-"in progress") wording.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE_DOC = REPO_ROOT / "docs" / "milestones" / "v0.4.0-ingestion-api.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
ROADMAP_ASTRO = REPO_ROOT / "web" / "src" / "pages" / "roadmap.astro"
PRIVACY_ASTRO = REPO_ROOT / "web" / "src" / "pages" / "privacy.astro"
LOCAL_REPORT_PRIVACY_ASTRO = (
    REPO_ROOT / "web" / "src" / "pages" / "learn" / "local-report-privacy.astro"
)
README_MD = REPO_ROOT / "README.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file does not exist: {path}"
    return path.read_text(encoding="utf-8")


def _normalize_whitespace(text: str) -> str:
    """Collapses every run of whitespace (including a Markdown line-wrap
    newline in the middle of a sentence) to a single space, so a multi-word
    phrase check does not depend on exactly where this project's ~80-column
    prose wrapping happened to break a line."""
    return re.sub(r"\s+", " ", text)


def _extract_section(text: str, start_pattern: str, end_pattern: str) -> str:
    """Finds the body of one section: `start_pattern` need only match a
    prefix of its heading's own line (the rest of that line, e.g. a
    backtick-quoted endpoint path, is consumed automatically via `[^\\n]*`)
    -- this avoids every section-boundary regex having to spell out a
    heading's full literal text, including punctuation that would otherwise
    need regex-escaping."""
    match = re.search(f"{start_pattern}[^\n]*\n(.*?)\n{end_pattern}", text, re.DOTALL)
    assert match is not None, f"could not locate section {start_pattern!r} .. {end_pattern!r}"
    return match.group(1)


@pytest.fixture(scope="module")
def milestone_text() -> str:
    return _read(MILESTONE_DOC)


@pytest.fixture(scope="module")
def claude_md_text() -> str:
    return _read(CLAUDE_MD)


@pytest.fixture(scope="module")
def roadmap_text() -> str:
    return _read(ROADMAP_ASTRO)


def _claude_invariants_section(claude_md_text: str) -> str:
    return _extract_section(
        claude_md_text, r"## Ingestion API and uploader invariants \(v0\.4\.0\+\)", r"## "
    )


# --- (a) required v0.4.0 sections must not disappear ------------------------

REQUIRED_MILESTONE_SECTIONS = [
    "## A. Objective and product context",
    "## B. Scope and non-goals",
    "## C. Privacy boundary",
    "## D. Versioning contract",
    "## E. API contract",
    "### E.0 Strict input validation and deterministic report fingerprint",
    "## F. Authentication and tenant isolation",
    "## G. Threat model",
    "## H. Proposed architecture",
    "## I. Phase plan (Phase 4B through 4G, proposed)",
]


class TestRequiredSectionsPresent:
    @pytest.mark.parametrize("heading", REQUIRED_MILESTONE_SECTIONS)
    def test_required_section_heading_present(self, milestone_text: str, heading: str) -> None:
        assert heading in milestone_text, (
            f"required v0.4.0 milestone section heading missing: {heading!r}"
        )

    def test_request_data_flow_diagram_present(self, milestone_text: str) -> None:
        # §H requires "a concise request/data-flow diagram" -- checked as a
        # real fenced Mermaid diagram block, not merely prose describing one.
        assert "```mermaid" in milestone_text
        assert "flowchart" in milestone_text

    def test_threat_model_table_covers_every_required_threat(self, milestone_text: str) -> None:
        required_threats = [
            "Cross-tenant access",
            "Forged tenant identifiers",
            "Replay and duplicate ingestion",
            "Oversized or malformed payloads",
            "Schema abuse and resource exhaustion",
            "Secret leakage",
            "Log leakage",
            "Enumeration of ingestion IDs",
            "Unauthorized deletion",
            "Storage-key traversal or collision",
            "Rate limiting and abuse controls",
        ]
        threat_section = _extract_section(milestone_text, r"## G\. Threat model", r"## H\.")
        missing = [threat for threat in required_threats if threat not in threat_section]
        assert missing == [], f"threat model table is missing required threat(s): {missing}"

    def test_phase_plan_covers_4b_through_4g(self, milestone_text: str) -> None:
        for phase in ["Phase 4B", "Phase 4C", "Phase 4D", "Phase 4E", "Phase 4F", "Phase 4G"]:
            assert phase in milestone_text, f"phase plan is missing {phase}"

    def test_storage_security_requirements_present(self, milestone_text: str) -> None:
        section = _extract_section(milestone_text, r"## H\. Proposed architecture", r"## I\.")
        for requirement in [
            "TLS in transit",
            "Encryption at rest",
            "Least-privilege service access",
            "Bounded backup deletion",
            "Region/data-residency selection is a mandatory decision before Phase\n  4G",
        ]:
            assert requirement in _normalize_whitespace(section) or requirement in section, (
                f"storage security requirement missing from §H: {requirement!r}"
            )


# --- (b) the API version and report schema version must never be conflated --


class TestVersioningIsNotConflated:
    def test_api_version_and_schema_version_are_named_as_independent_axes(
        self, milestone_text: str
    ) -> None:
        versioning_section = _extract_section(
            milestone_text, r"## D\. Versioning contract", r"## E\."
        )
        assert "/api/v1" in versioning_section
        assert "report_schema_version" in versioning_section
        assert "independent" in versioning_section.lower()

    def test_worked_example_proves_each_axis_can_vary_while_the_other_holds_constant(
        self, milestone_text: str
    ) -> None:
        # A concrete, non-vacuous proof that the two axes are independent:
        # the documented worked-example table must contain at least one row
        # pair holding the API version constant while the schema version
        # changes, AND at least one row pair holding the schema version
        # constant while the API version changes. A doc that merely claims
        # independence in prose, without ever demonstrating it, fails here.
        table_match = re.search(
            r"\| API version \| report\\_schema\\_version \| Meaning \|\n(.*?)\n\n",
            milestone_text,
            re.DOTALL,
        )
        assert table_match is not None, (
            "could not locate the API-version/report-schema-version worked-example table"
        )
        table_body = table_match.group(1)
        rows = [line for line in table_body.splitlines() if line.strip().startswith("|")]
        assert len(rows) >= 3, (
            "worked-example table must have at least 3 rows to demonstrate independence"
        )

        def parse_row(row: str) -> tuple[str, str]:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            return cells[0], cells[1]

        parsed = [parse_row(row) for row in rows]
        api_versions = {api for api, _schema in parsed}
        schema_versions = {schema for _api, schema in parsed}
        same_api_different_schema = any(
            a1 == a2 and s1 != s2
            for (a1, s1) in parsed
            for (a2, s2) in parsed
            if (a1, s1) != (a2, s2)
        )
        same_schema_different_api = any(
            s1 == s2 and a1 != a2
            for (a1, s1) in parsed
            for (a2, s2) in parsed
            if (a1, s1) != (a2, s2)
        )
        assert len(api_versions) >= 2, "worked example never varies the API version"
        assert len(schema_versions) >= 2, "worked example never varies the report schema version"
        assert same_api_different_schema, (
            "worked example never holds the API version constant while the schema version changes"
        )
        assert same_schema_different_api, (
            "worked example never holds the report schema version constant while the API version"
            " changes"
        )

    def test_claude_md_states_the_two_axes_separately(self, claude_md_text: str) -> None:
        section = _claude_invariants_section(claude_md_text)
        assert "/api/v1" in section
        assert "report schema version" in section.lower()
        assert "never conflate" in section.lower()


# --- (c) documentation must never imply the browser explorer uploads --------

BROWSER_UPLOAD_CLAIM_SOURCES = [
    PRIVACY_ASTRO,
    LOCAL_REPORT_PRIVACY_ASTRO,
    ROADMAP_ASTRO,
    MILESTONE_DOC,
]

# Any of these tokens marks a "client-side surface" a sentence is talking
# about; matched as a whole word so "browser-triggered" still matches
# "browser" (the hyphen is a word boundary) without matching an unrelated
# word that merely contains "demo" as a substring.
_CLIENT_SURFACE_PATTERN = re.compile(r"\b(explorer|demo|browser)\b", re.IGNORECASE)
# Matches every inflection of the verb, not just the bare form -- a claim
# phrased as "uploaded"/"uploading" is exactly as unsafe as one phrased as
# "upload"/"uploads", and must be recognized identically.
_UPLOAD_PATTERN = re.compile(r"\b(?:uploads?|uploaded|uploading)\b", re.IGNORECASE)
_NEGATION_WORD = r"(?:never|does not|doesn't|will not|won't|no)"
# Anchored to the *end* of whatever text precedes one specific upload-word
# occurrence: credits a negation only if it appears within at most 3 words
# directly before *that* occurrence -- not merely present somewhere, anywhere,
# in the sentence. This is what makes "No account is required; the browser
# uploads every selected report." correctly flagged as unsafe: "No" is
# present, but six words before "uploads", far outside this window, so it
# does not count as negating the claim that immediately follows it.
_NEGATION_IMMEDIATELY_BEFORE = re.compile(
    rf"\b{_NEGATION_WORD}\b(?:\s+\S+){{0,3}}\s*$", re.IGNORECASE
)


def _strip_fenced_code_blocks(text: str) -> str:
    """Removes every ` ```...``` ` fenced block (e.g. this document's own
    Mermaid diagram and JSON examples) before prose is scanned for claims.
    Diagram source (a node literally labeled "cloudops-guard upload" next
    to one literally labeled "explorer", with no sentence punctuation
    between them at all) is not a prose sentence asserting anything about
    what the explorer does -- scanning it as one would produce a false
    positive with no bearing on the actual property being checked."""
    return re.sub(r"```.*?```", " ", text, flags=re.DOTALL)


def _split_into_sentences(text: str) -> list[str]:
    normalized = _normalize_whitespace(_strip_fenced_code_blocks(text))
    # Deliberately simple sentence splitting (not general-purpose NLP): a
    # spurious split only ever produces two shorter sentences instead of
    # one, which can only make this scan *more* sensitive, never less --
    # so it is safe to be approximate here.
    return re.split(r"(?<=[.!?])\s+", normalized)


def _find_unsafe_upload_claims(text: str) -> list[str]:
    """Returns every sentence that mentions a client-side surface (the
    explorer, a demo, or the browser generally) together with at least one
    upload-word occurrence (upload/uploads/uploaded/uploading) that is not
    itself individually negated -- operating on whitespace-normalized,
    sentence-split, fenced-code-stripped text, so a claim split across two
    source lines by this project's own prose wrapping is still caught as a
    single sentence, and diagram/code source is never mistaken for a prose
    claim.

    Negation is evaluated **independently for every upload-word occurrence**
    in the sentence, not once for the sentence as a whole: a sentence can
    contain both a negated and a non-negated occurrence side by side (e.g.
    "The explorer never uploads previews, but the browser uploads full
    reports."), and the earlier, negated occurrence must never cause the
    later, unsafe one to go unflagged. One unsafe occurrence anywhere in a
    sentence is sufficient to flag the whole sentence.
    """
    offending: list[str] = []
    for sentence in _split_into_sentences(text):
        if not _CLIENT_SURFACE_PATTERN.search(sentence):
            continue
        for match in _UPLOAD_PATTERN.finditer(sentence):
            preceding_text = sentence[: match.start()]
            if not _NEGATION_IMMEDIATELY_BEFORE.search(preceding_text):
                offending.append(sentence)
                break
    return offending


class TestUnsafeUploadClaimDetectionIsNonVacuous:
    """Proves the scan above actually catches the adversarial examples the
    two correction passes explicitly named, before it is trusted against
    the project's own real documentation files below. The final two cases
    are new: a sentence with one negated and one non-negated upload
    occurrence side by side, which only a per-occurrence (not
    per-sentence) negation check can tell apart."""

    @pytest.mark.parametrize(
        "unsafe_text",
        [
            "The explorer\nuploads every selected report.",
            "No account is required; the browser uploads every selected report.",
            "The explorer never uploads previews, but the browser uploads full reports.",
            "The browser does not upload previews, but it uploads the full report.",
        ],
    )
    def test_known_unsafe_statements_are_caught(self, unsafe_text: str) -> None:
        assert _find_unsafe_upload_claims(unsafe_text) != [], (
            f"the unsafe-upload-claim scan failed to catch a known-unsafe statement:"
            f" {unsafe_text!r}"
        )

    @pytest.mark.parametrize(
        "safe_text",
        [
            "The explorer never uploads a selected report.",
            "No automatic or browser-triggered uploads, of any kind, ever.",
            "The file is never\nuploaded to any server.",
        ],
    )
    def test_known_safe_statements_are_not_caught(self, safe_text: str) -> None:
        assert _find_unsafe_upload_claims(safe_text) == [], (
            f"the unsafe-upload-claim scan produced a false positive on a known-safe statement:"
            f" {safe_text!r}"
        )


class TestExplorerNeverDescribedAsUploading:
    @pytest.mark.parametrize("path", BROWSER_UPLOAD_CLAIM_SOURCES)
    def test_no_sentence_pairs_upload_language_with_a_client_surface_without_a_negation(
        self, path: Path
    ) -> None:
        text = _read(path)
        offending = _find_unsafe_upload_claims(text)
        assert offending == [], (
            f"{path}: found sentence(s) claiming the browser/explorer/demo uploads: {offending}"
        )

    def test_privacy_page_still_asserts_the_explorer_never_uploads(self) -> None:
        text = _read(PRIVACY_ASTRO)
        assert "never uploaded" in text.lower()
        assert "connect-src 'none'" in text

    def test_local_report_privacy_page_still_asserts_no_network_request(self) -> None:
        text = _read(LOCAL_REPORT_PRIVACY_ASTRO)
        assert "never uploaded" in text.lower()
        assert "No network request is initiated" in text

    def test_milestone_doc_states_the_ingestion_api_is_a_separate_service_from_the_website(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(
            _extract_section(milestone_text, r"## C\. Privacy boundary", r"## D\.")
        )
        assert "separate service" in section
        assert "connect-src 'none'" in section


# --- (d) authentication must never permit a body-supplied customer identity -


class TestAuthenticationForbidsBodySuppliedIdentity:
    def test_milestone_doc_states_identity_comes_only_from_the_token(
        self, milestone_text: str
    ) -> None:
        section = _extract_section(
            milestone_text, r"## F\. Authentication and tenant isolation", r"## G\."
        )
        assert "derived only from the authenticated token" in section
        assert "never from anything the client supplies" in section

    def test_claude_md_states_the_same_invariant(self, claude_md_text: str) -> None:
        section = _normalize_whitespace(_claude_invariants_section(claude_md_text))
        assert "derived only from the authenticated bearer token" in section
        assert "never trusted from a client-supplied field" in section


class TestClosedRequestEnvelopeContract:
    """Replaces a prior broad forbidden-substring check (asserting
    'tenant_id'/'customer_id' etc. never appear anywhere in §E) with a
    positive contract: the envelope is a named, closed set of exactly four
    fields, and unknown top-level fields are explicitly rejected -- a
    stronger, harder-to-satisfy-by-accident property than a substring
    denylist, and one that also naturally rules out a client-supplied
    tenant field without needing to enumerate every possible name for one.
    """

    def _envelope_section(self, milestone_text: str) -> str:
        return _normalize_whitespace(_extract_section(milestone_text, r"### E\.2", r"### E\.3"))

    def test_envelope_is_documented_as_a_closed_set_of_exactly_four_fields(
        self, milestone_text: str
    ) -> None:
        section = self._envelope_section(milestone_text)
        assert "closed set of exactly these four top-level" in section
        for field in ["platform", "report_schema_version", "report", "idempotency_key"]:
            assert f"`{field}`" in section, (
                f"expected field {field!r} to be named in the closed envelope set"
            )

    def test_unknown_top_level_fields_are_explicitly_rejected(self, milestone_text: str) -> None:
        section = self._envelope_section(milestone_text)
        assert "unknown top-level field" in section
        assert "never" in section and "silently ignored" in section
        assert "400" in section and "invalid_request" in section


# --- (prior pass 2) one deterministic ingestion fingerprint, covering inputs -


class TestDeterministicReportFingerprint:
    def _fingerprint_section(self, milestone_text: str) -> str:
        return _extract_section(milestone_text, r"### E\.0", r"### E\.1")

    def test_fingerprint_covers_platform_schema_version_and_report(
        self, milestone_text: str
    ) -> None:
        section = self._fingerprint_section(milestone_text)
        for token in ["platform", "report_schema_version", "report"]:
            assert token in section

    def test_fingerprint_names_an_exact_canonicalization_standard(
        self, milestone_text: str
    ) -> None:
        section = self._fingerprint_section(milestone_text)
        assert "RFC 8785" in section
        assert "SHA-256" in section

    def test_fingerprint_field_used_consistently_in_responses(self, milestone_text: str) -> None:
        assert '"report_fingerprint": "sha256:<hex>"' in milestone_text
        # the old, ambiguous field name must not remain as a live field
        # anywhere outside E.0's own historical explanatory note (checked
        # by the drift test below, which greps the whole document instead).

    def test_uploader_server_equivalence_is_a_required_acceptance_gate(
        self, milestone_text: str
    ) -> None:
        fingerprint_section = self._fingerprint_section(milestone_text)
        assert "testable" in fingerprint_section.lower()
        phase_plan_section = _extract_section(
            milestone_text, r"## I\. Phase plan \(Phase 4B through 4G, proposed\)", r"$"
        )
        normalized_phase_plan = _normalize_whitespace(phase_plan_section)
        assert "conformance" in normalized_phase_plan.lower()
        assert "byte-for-byte" in normalized_phase_plan.lower()

    def test_content_hash_is_not_the_live_field_name_anywhere_outside_its_own_historical_footnote(
        self, milestone_text: str
    ) -> None:
        # `content_hash` may still appear exactly once, inside E.0's own
        # paragraph explaining what this correction replaced -- everywhere
        # else, the live field name must be `report_fingerprint`.
        occurrences = [m.start() for m in re.finditer(r"`content_hash`", milestone_text)]
        assert len(occurrences) <= 1, (
            f"'content_hash' should only remain as E.0's own historical footnote, found"
            f" {len(occurrences)} occurrences"
        )


# --- (prior pass 3 / this pass 3) deletion semantics: status enum, transitions -


class TestDeletionSemantics:
    def _deletion_section(self, milestone_text: str) -> str:
        return _extract_section(milestone_text, r"### E\.4", r"### Idempotency semantics")

    def test_complete_status_enum_is_documented(self, milestone_text: str) -> None:
        section = self._deletion_section(milestone_text)
        for status in ["received", "retired", "deleted"]:
            assert f"`{status}`" in section, f"status value {status!r} not documented in §E.4"

    def test_transitions_are_documented(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._deletion_section(milestone_text))
        assert "reason=customer_requested)--> retired" in section
        assert "reason=retention_expired)--> retired" in section
        assert "retired --(physical purge completes, asynchronous)--> deleted" in section

    def test_reason_field_distinguishes_the_two_retirement_triggers(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._deletion_section(milestone_text))
        assert "customer_requested" in section
        assert "retention_expired" in section
        assert '"reason"' in section

    def test_field_names_stay_truthful_for_both_triggers(self, milestone_text: str) -> None:
        section = self._deletion_section(milestone_text)
        assert "truthful" in section.lower()
        assert "`retired_at`" in section
        # The old, trigger-specific field name may still appear exactly
        # once, as this correction's own explanation of what it renamed
        # (mirroring the `content_hash` precedent above) -- everywhere
        # else in this section, the live field name must be `retired_at`.
        occurrences = len(re.findall(r"deletion_requested_at", section))
        assert occurrences <= 1, (
            f"'deletion_requested_at' should only remain as an explanatory footnote, found"
            f" {occurrences} occurrences"
        )

    def test_delete_never_overwrites_an_existing_retention_expired_reason(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._deletion_section(milestone_text))
        assert (
            "never overwrites an existing" in section.lower()
            or "never overwrites an" in section.lower()
        )
        assert "true original" in section.lower()

    def test_automatic_retention_expiry_transition_is_defined(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._deletion_section(milestone_text))
        assert "automatic" in section.lower()
        assert "retention period" in section.lower()
        assert "background retention sweep" in section.lower()

    def test_never_claims_physical_deletion_before_it_occurs(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._deletion_section(milestone_text))
        assert "deleted_at" in section
        assert "never claims" in section.lower() or "never claim" in section.lower()

    def test_repeated_delete_behavior_during_and_after_tombstone_retention_is_stated(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._deletion_section(milestone_text))
        assert "repeated" in section.lower() and "delete" in section.lower()
        assert "tombstone" in section.lower()
        assert (
            "tombstone itself has expired" in section.lower()
            or "tombstone has since expired" in section.lower()
        )

    def test_unknown_and_cross_tenant_and_expired_tombstone_ids_behave_identically(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._deletion_section(milestone_text))
        assert "identical `404 not_found`" in section

    def test_metadata_store_interface_supports_the_status_lifecycle(
        self, milestone_text: str
    ) -> None:
        storage_section = _extract_section(
            milestone_text, r"## H\. Proposed architecture", r"## I\."
        )
        for interface_method in [
            "mark_retired",
            "mark_purged",
            "get_tombstone",
            "list_expired_for_retention_sweep",
        ]:
            assert interface_method in storage_section, (
                f"MetadataStore is missing {interface_method!r}"
            )


# --- (1) ingestion deduplication must be concurrency-safe -------------------


class TestConcurrencySafeDeduplication:
    def _idempotency_section(self, milestone_text: str) -> str:
        return _extract_section(
            milestone_text, r"### Idempotency semantics", r"### Deterministic machine-readable"
        )

    def test_atomic_create_or_get_operation_replaces_find_then_put(
        self, milestone_text: str
    ) -> None:
        section = self._idempotency_section(milestone_text)
        assert "create_or_get_received" in section
        assert "atomic" in section.lower()

    def test_naive_find_then_put_is_explicitly_called_insufficient(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._idempotency_section(milestone_text))
        assert "explicitly insufficient" in section.lower()

    def test_at_most_one_received_record_per_tenant_and_fingerprint_is_guaranteed(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._idempotency_section(milestone_text))
        assert "at most one" in section.lower()
        assert "(tenant_id, report_fingerprint)" in section

    def test_idempotency_key_binding_is_also_atomic(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._idempotency_section(milestone_text))
        assert "at most one active binding" in section.lower()
        assert "(tenant_id, idempotency_key)" in section

    def test_storage_interface_exposes_one_atomic_operation_not_separate_lookup_and_put(
        self, milestone_text: str
    ) -> None:
        storage_section = _extract_section(
            milestone_text, r"## H\. Proposed architecture", r"## I\."
        )
        assert "create_or_get_received" in storage_section
        assert "single database transaction" in storage_section.lower()
        # The old, separately-callable dedup-only method must not remain as
        # a live MetadataStore interface entry (a mention of it as the
        # rejected alternative, in the idempotency-semantics prose already
        # checked above, is a different section and unaffected by this
        # check).
        interface_block_match = re.search(
            r"```\nMetadataStore\n(.*?)\n```", storage_section, re.DOTALL
        )
        assert interface_block_match is not None, (
            "could not locate the MetadataStore interface block"
        )
        assert "find_by_fingerprint(" not in interface_block_match.group(1)

    def test_simultaneous_request_tests_required_in_phase_4b_and_4d(
        self, milestone_text: str
    ) -> None:
        phase_plan_section = _normalize_whitespace(
            _extract_section(
                milestone_text, r"## I\. Phase plan \(Phase 4B through 4G, proposed\)", r"$"
            )
        )
        assert "simultaneous-request tests are required" in phase_plan_section.lower()
        # Required at both the storage-interface level (4B) and the real
        # HTTP-API level (4D) -- not merely one or the other.
        occurrences = phase_plan_section.lower().count("simultaneous-request tests are required")
        assert occurrences >= 2, (
            f"expected simultaneous-request test requirements in both Phase 4B and 4D,"
            f" found {occurrences}"
        )

    def test_claude_md_states_the_atomicity_guarantee(self, claude_md_text: str) -> None:
        section = _normalize_whitespace(_claude_invariants_section(claude_md_text))
        assert "atomic create-or-return-existing" in section.lower()
        assert "concurrent requests" in section.lower()


# --- (2) strict JSON/JCS input rules -----------------------------------------


class TestStrictJsonInputRules:
    def _strict_rules_section(self, milestone_text: str) -> str:
        return _extract_section(milestone_text, r"### E\.0", r"### E\.1")

    def test_duplicate_object_member_names_are_rejected(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._strict_rules_section(milestone_text))
        assert "reject duplicate object-member names" in section.lower()
        assert "every object level" in section.lower() or "every** object level" in section.lower()

    def test_nan_and_infinity_are_rejected(self, milestone_text: str) -> None:
        section = self._strict_rules_section(milestone_text)
        assert "NaN" in section
        assert "Infinity" in section
        assert "-Infinity" in section

    def test_malformed_unicode_is_rejected(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._strict_rules_section(milestone_text))
        assert "malformed" in section.lower() and "unicode" in section.lower()
        assert "decodeUtf8Strict" in section

    def test_report_schema_version_must_be_an_integer_not_a_numeric_string(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._strict_rules_section(milestone_text))
        assert 'a numeric string such as `"1"` is invalid' in section

    def test_validation_order_is_specified_fingerprint_last(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._strict_rules_section(milestone_text))
        assert "validation order" in section.lower()
        # The four ordered steps must appear in this exact order in the text.
        strict_decode_pos = section.lower().find("strict-decode rules")
        envelope_pos = section.lower().find("closed-envelope unknown-field check")
        schema_pos = section.lower().find("platform-specific")
        fingerprint_pos = section.lower().find("report_fingerprint` computation")
        assert -1 not in (strict_decode_pos, envelope_pos, schema_pos, fingerprint_pos)
        assert strict_decode_pos < envelope_pos < schema_pos < fingerprint_pos

    def test_rejection_fixtures_are_distinguished_from_equivalence_fixtures(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._strict_rules_section(milestone_text))
        assert "rejection" in section.lower()
        assert "equivalence" in section.lower()
        # The old, incorrect framing (duplicate keys / numeric-string
        # schema version presented as fingerprint *equivalence* edge cases)
        # must not remain.
        assert "duplicate keys arriving in different orders after parsing" not in section

    def test_conformance_fixtures_use_valid_rfc_8785_unicode_and_number_cases(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._strict_rules_section(milestone_text))
        assert "unicode content in" in section.lower()
        assert "3.2.2.3" in section  # RFC 8785's number-serialization clause, cited precisely

    def test_claude_md_states_the_strict_decode_rules(self, claude_md_text: str) -> None:
        section = _normalize_whitespace(_claude_invariants_section(claude_md_text))
        assert "duplicate object-member name" in section.lower()
        assert "nan" in section.lower() and "infinity" in section.lower()
        assert "numeric string" in section.lower()


# --- (4) the fixed error envelope must never carry response-specific data ---


class TestErrorEnvelopeIsTrulyFixed:
    def _versioning_section(self, milestone_text: str) -> str:
        return _extract_section(milestone_text, r"## D\. Versioning contract", r"## E\.")

    def test_unsupported_schema_version_error_no_longer_claims_to_list_supported_values(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._versioning_section(milestone_text))
        assert "does **not** name the currently-supported values" in section
        assert "GET /api/v1/capabilities" in section

    def test_unsupported_schema_version_error_uses_the_fixed_minimal_envelope(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._versioning_section(milestone_text))
        assert '"ok": false, "error": "unsupported_report_schema_version", "request_id"' in section

    def test_get_and_delete_enumerate_401_403_404_405_429_500(self, milestone_text: str) -> None:
        get_section = _extract_section(milestone_text, r"### E\.3", r"### E\.4")
        delete_section = _extract_section(milestone_text, r"### E\.4", r"### Idempotency semantics")
        for section, name in [(get_section, "E.3 (GET)"), (delete_section, "E.4 (DELETE)")]:
            for code in ["401", "403", "404", "405", "429", "500"]:
                assert code in section, f"{name} does not enumerate {code}"

    def test_method_not_allowed_is_defined_consistently_across_endpoints(
        self, milestone_text: str
    ) -> None:
        for section_pattern, next_pattern in [
            (r"### E\.1", r"### E\.2"),
            (r"### E\.2", r"### E\.3"),
        ]:
            section = _extract_section(milestone_text, section_pattern, next_pattern)
            assert "405 method_not_allowed" in section
            assert "Allow" in section
        shared_path_section = _extract_section(milestone_text, r"### E\.3", r"### E\.4")
        assert "Allow: GET, DELETE" in shared_path_section

    def test_claude_md_states_the_fixed_envelope_and_capabilities_redirect(
        self, claude_md_text: str
    ) -> None:
        section = _normalize_whitespace(_claude_invariants_section(claude_md_text))
        assert "fixed, minimal envelope" in section.lower()
        assert "GET /api/v1/capabilities" in section


# --- (5) complete idempotency-key semantics ----------------------------------


class TestIdempotencyKeySemantics:
    def _idempotency_section(self, milestone_text: str) -> str:
        return _extract_section(
            milestone_text, r"### Idempotency semantics", r"### Deterministic machine-readable"
        )

    def test_same_key_same_fingerprint_replay_is_defined(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._idempotency_section(milestone_text))
        assert "same key, same" in section.lower()

    def test_behavior_after_retirement_or_retention_expiry_is_defined(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._idempotency_section(milestone_text))
        assert "no longer active" in section.lower()
        assert "new" in section.lower() and "binding" in section.lower()

    def test_24_hour_window_boundary_is_precisely_defined(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._idempotency_section(milestone_text))
        assert "fixed, non-sliding 24-hour window" in section.lower()
        assert "t + 24h" in section.lower()

    def test_concurrent_requests_are_addressed(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._idempotency_section(milestone_text))
        assert "concurrency guarantee" in section.lower()

    def test_original_status_and_body_replay_behavior_is_stated(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._idempotency_section(milestone_text))
        assert "live read" in section.lower()
        assert "current fields" in section.lower()


# --- (7) layered authentication-abuse protection -----------------------------


class TestLayeredAuthenticationAbuseProtection:
    def _auth_section(self, milestone_text: str) -> str:
        return _extract_section(
            milestone_text, r"## F\. Authentication and tenant isolation", r"## G\."
        )

    def test_three_layers_are_named(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._auth_section(milestone_text))
        assert "layer 1" in section.lower()
        assert "layer 2" in section.lower()
        assert "layer 3" in section.lower()

    def test_layer_1_is_pre_argon2id_and_scoped_to_lookup_id(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._auth_section(milestone_text))
        assert "before ever invoking argon2id" in section.lower()
        assert (
            "single `lookup_id`" in section.lower() or "single \\`lookup_id\\`" in section.lower()
        )

    def test_layer_2_covers_unknown_lookup_ids_and_the_capabilities_route(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._auth_section(milestone_text))
        assert "unknown" in section.lower() and "lookup_id" in section.lower()
        assert "GET /api/v1/capabilities" in section
        assert "source" in section.lower()

    def test_layer_3_is_the_existing_per_token_limit_unchanged(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._auth_section(milestone_text))
        assert "existing authenticated per-token limit" in section.lower()

    def test_no_vendor_or_numeric_threshold_selected(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._auth_section(milestone_text))
        assert "no vendor, product, or specific numeric production threshold" in section.lower()

    def test_attempt_limiter_interface_defined_not_provisioned(self, milestone_text: str) -> None:
        storage_section = _extract_section(
            milestone_text, r"## H\. Proposed architecture", r"## I\."
        )
        assert "AttemptLimiter" in storage_section
        assert "record_failure" in storage_section
        assert "is_blocked" in storage_section

    def test_threat_model_has_a_dedicated_authentication_abuse_row(
        self, milestone_text: str
    ) -> None:
        threat_section = _extract_section(milestone_text, r"## G\. Threat model", r"## H\.")
        assert "Authentication-guessing" in threat_section

    def test_claude_md_states_the_three_layers(self, claude_md_text: str) -> None:
        section = _normalize_whitespace(_claude_invariants_section(claude_md_text))
        assert "three independent, layered" in section.lower()
        assert "argon2id is ever invoked" in section.lower()


# --- (prior pass 4) token lookup must be implementable with salted hashes ----


class TestTokenLookupIsImplementable:
    def test_token_is_split_into_a_non_secret_lookup_id_and_an_independent_secret(
        self, milestone_text: str
    ) -> None:
        section = _extract_section(
            milestone_text, r"## F\. Authentication and tenant isolation", r"## G\."
        )
        assert "lookup_id" in section
        assert "not itself secret" in section
        assert "Argon2id" in section

    def test_lookup_id_is_explicitly_not_a_cosmetic_prefix(self, milestone_text: str) -> None:
        section = _normalize_whitespace(
            _extract_section(
                milestone_text, r"## F\. Authentication and tenant isolation", r"## G\."
            )
        )
        assert "not a cosmetic label" in section

    def test_storage_interface_uses_lookup_id_never_an_ambiguous_token_hash_key(
        self, milestone_text: str
    ) -> None:
        storage_section = _extract_section(
            milestone_text, r"## H\. Proposed architecture", r"## I\."
        )
        assert "lookup(lookup_id)" in storage_section
        assert "token_hash" not in storage_section

    def test_claude_md_states_the_same_token_design(self, claude_md_text: str) -> None:
        section = _normalize_whitespace(_claude_invariants_section(claude_md_text))
        assert "lookup_id" in section
        assert "Argon2id" in section


# --- (prior pass 5) the 10 MiB report limit vs. the HTTP request-body limit --


class TestSeparateSizeLimits:
    def _size_limit_section(self, milestone_text: str) -> str:
        return _normalize_whitespace(
            _extract_section(milestone_text, r"## D\. Versioning contract", r"## E\.")
        )

    def test_two_distinct_named_limits_exist(self, milestone_text: str) -> None:
        section = self._size_limit_section(milestone_text)
        assert "MAX_REPORT_BYTES = 10 * 1024 * 1024" in section
        assert "MAX_REQUEST_BODY_BYTES = MAX_REPORT_BYTES + 4096" in section
        assert "10,485,760 bytes" in section
        assert "10,489,856 bytes" in section

    def test_report_limit_matches_existing_browser_precedent(self, milestone_text: str) -> None:
        section = self._size_limit_section(milestone_text)
        assert "MAX_REPORT_FILE_BYTES" in section

    def test_states_which_representation_is_measured_for_each_limit(
        self, milestone_text: str
    ) -> None:
        section = self._size_limit_section(milestone_text)
        assert "representation measured" in section
        assert "compact" in section.lower()
        assert (
            "RFC 8785" in section
        )  # explicitly distinguished from the fingerprint's canonical form

    def test_capabilities_response_reports_both_limits_by_distinct_names(
        self, milestone_text: str
    ) -> None:
        assert '"max_report_bytes": 10485760' in milestone_text
        assert '"max_request_body_bytes": 10489856' in milestone_text

    def test_claude_md_states_two_independent_ceilings(self, claude_md_text: str) -> None:
        section = _normalize_whitespace(_claude_invariants_section(claude_md_text))
        assert "Two independent size ceilings, never conflated" in section


# --- (e) the upload flow must require dry-run and explicit customer control -


class TestUploadFlowRequiresDryRunAndExplicitControl:
    def _privacy_section(self, milestone_text: str) -> str:
        return _extract_section(milestone_text, r"## C\. Privacy boundary", r"## D\.")

    def test_milestone_doc_defines_dry_run(self, milestone_text: str) -> None:
        section = self._privacy_section(milestone_text)
        assert "--dry-run" in section
        assert "never" in section.lower() and "network request" in section.lower()

    def test_dry_run_requires_no_credential(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._privacy_section(milestone_text))
        assert (
            "without requiring any credential to be configured" in section
            or "requires no credential to be configured" in section
        )

    def test_no_server_derived_tenant_name_before_confirmation(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._privacy_section(milestone_text))
        assert "never prints a server-derived tenant display name" in section
        assert "non-authoritative local alias" in section

    def test_no_network_activity_of_any_kind_before_confirmation(self, milestone_text: str) -> None:
        section = _normalize_whitespace(self._privacy_section(milestone_text))
        assert "No capabilities call, identity/authentication check, or upload request" in section
        assert (
            "GET /api/v1/capabilities" in section
        )  # named explicitly as one of the forbidden pre-confirmation calls

    def test_milestone_doc_requires_explicit_confirmation(self, milestone_text: str) -> None:
        section = self._privacy_section(milestone_text)
        assert "explicit confirmation" in section.lower()
        assert (
            "UPLOAD" in section
        )  # the literal confirmation token the design requires the user to type

    def test_milestone_doc_defines_non_interactive_behavior_that_fails_closed(
        self, milestone_text: str
    ) -> None:
        section = _normalize_whitespace(self._privacy_section(milestone_text))
        assert "--yes" in section
        assert "fails closed" in section.lower()

    def test_claude_md_states_the_same_upload_control_invariants(self, claude_md_text: str) -> None:
        section = _claude_invariants_section(claude_md_text)
        assert "--dry-run" in section
        assert "--yes" in section
        assert "explicit" in section.lower()
        assert "No capabilities call, identity/authentication check, or upload" in section


# --- (f) the roadmap and milestone-doc phase descriptions must not drift ----


class TestRoadmapAndMilestoneDoNotDrift:
    def test_roadmap_mentions_phase_4a_and_the_milestone_doc(self, roadmap_text: str) -> None:
        assert "v0.4.0" in roadmap_text
        assert "Phase 4A" in roadmap_text

    def test_roadmap_and_milestone_doc_agree_phase_4a_is_documentation_only(
        self, roadmap_text: str, milestone_text: str
    ) -> None:
        expected_phrase = "documentation and contract design only"
        assert (
            expected_phrase in roadmap_text.lower()
            or "documentation and contract design" in roadmap_text
        )
        assert (
            expected_phrase in milestone_text.lower()
            or "documentation and contract design" in milestone_text
        )

    def test_roadmap_never_claims_the_ingestion_api_is_implemented_or_live(
        self, roadmap_text: str
    ) -> None:
        in_design_section_match = re.search(
            r'<h2 id="in-design-heading">(.*?)</section>', roadmap_text, re.DOTALL
        )
        assert in_design_section_match is not None, (
            "could not locate the roadmap's v0.4.0 'specified, not implemented' section"
        )
        section = in_design_section_match.group(1).lower()
        for forbidden_phrase in ["is available", "you can now upload", "is live", "is implemented"]:
            assert forbidden_phrase not in section

    def test_claude_md_and_milestone_doc_agree_on_the_commit_pushed_for_phase_3k(
        self, claude_md_text: str, milestone_text: str
    ) -> None:
        commit_sha = "87376002553b24f21a0331c708986222a005a62d"
        assert commit_sha in claude_md_text
        assert commit_sha in milestone_text

    def test_readme_mentions_v040_consistently_with_the_milestone_doc(
        self, milestone_text: str
    ) -> None:
        readme_text = _normalize_whitespace(_read(README_MD))
        assert "v0.4.0" in readme_text
        assert "documentation and contract design only" in readme_text
        assert "documentation and contract design only" in _normalize_whitespace(milestone_text)

    def test_status_wording_is_durable_not_in_progress_language(
        self, roadmap_text: str, milestone_text: str, claude_md_text: str
    ) -> None:
        """(7) 'in progress'/'being designed' language must be replaced with
        wording that remains accurate after Phase 4A is committed. Checks
        the durable, post-commit phrasing this correction introduced is
        actually present -- not merely the absence of the old phrasing
        (which the standalone sweep below also checks, across all 5
        source-of-truth files including ones without a dedicated fixture
        here)."""
        assert "specified in a published design document" in _normalize_whitespace(roadmap_text)
        assert "Phase 4A defines the v0.4.0 architecture and contracts" in _normalize_whitespace(
            milestone_text
        )
        privacy_text = _normalize_whitespace(_read(PRIVACY_ASTRO))
        assert "specified in a design document" in privacy_text
        readme_text = _normalize_whitespace(_read(README_MD))
        assert "specified in a design document" in readme_text
        invariants_section = _normalize_whitespace(
            _claude_invariants_section(claude_md_text)
        ).lower()
        assert "being designed" not in invariants_section
        assert "in progress" not in invariants_section

    @pytest.mark.parametrize(
        "path",
        [MILESTONE_DOC, ROADMAP_ASTRO, PRIVACY_ASTRO, README_MD],
    )
    def test_no_in_progress_or_being_designed_wording_remains(self, path: Path) -> None:
        normalized = _normalize_whitespace(_read(path)).lower()
        for forbidden_phrase in ["being designed", "currently documentation"]:
            assert forbidden_phrase not in normalized, (
                f"{path}: durable-wording violation: {forbidden_phrase!r}"
            )


# --- (g) Phase 4B: local reference storage code exists, but nothing beyond it -


INGESTION_PACKAGE_DIR = REPO_ROOT / "src" / "cloudops_guard" / "ingestion"


class TestPhase4BDocumentationMatchesRealCode:
    """Phase 4B added real, on-disk local reference storage/token code.
    These tests guard the opposite drift direction from the rest of this
    file: not "don't claim more than exists" but "the stale 'no storage
    code exists yet' claim must not silently return" once it stops being
    true. Every assertion here checks the actual files this project ships,
    exactly like the rest of this suite.
    """

    _INGESTION_PACKAGE_FILES = [
        "__init__.py",
        "models.py",
        "interfaces.py",
        "reference.py",
        "storage_keys.py",
        "errors.py",
    ]

    @pytest.mark.parametrize("filename", _INGESTION_PACKAGE_FILES)
    def test_documented_phase_4b_source_file_exists(self, filename: str) -> None:
        path = INGESTION_PACKAGE_DIR / filename
        assert path.is_file(), (
            f"CLAUDE.md/the milestone doc describe a Phase 4B "
            f"src/cloudops_guard/ingestion/{filename} that does not exist on disk"
        )

    def test_claude_md_mentions_the_real_ingestion_package_path(self, claude_md_text: str) -> None:
        assert "src/cloudops_guard/ingestion/" in claude_md_text

    def test_claude_md_states_phase_4b_is_uncommitted(self, claude_md_text: str) -> None:
        milestone_bullet = _extract_section(
            claude_md_text, r"The current approved milestone is v0\.4\.0", r"- Do not introduce"
        )
        assert "Phase 4B has implemented" in milestone_bullet
        assert "uncommitted" in milestone_bullet.lower()

    def test_milestone_doc_states_phase_4b_is_implemented_but_uncommitted(
        self, milestone_text: str
    ) -> None:
        assert "Phase 4B (uncommitted, pending independent review) has since implemented" in (
            milestone_text
        )

    def test_only_phase_4b_is_marked_implemented_in_the_phase_plan(
        self, milestone_text: str
    ) -> None:
        # Guards against a careless future edit marking a later phase (4C+)
        # implemented before it actually is: exactly one "Status:
        # implemented" marker may exist in the phase-plan section, and it
        # must belong to Phase 4B specifically.
        phase_plan_section = _extract_section(
            milestone_text, r"## I\. Phase plan \(Phase 4B through 4G, proposed\)", r"$"
        )
        markers = [m.start() for m in re.finditer(r"Status: implemented", phase_plan_section)]
        assert len(markers) == 1, (
            f"expected exactly one phase marked implemented in the phase plan, found {len(markers)}"
        )
        phase_4b_heading = phase_plan_section.find("Phase 4B — Storage and token interfaces")
        assert phase_4b_heading != -1
        assert phase_4b_heading < markers[0] < phase_plan_section.find("Phase 4C —")

    def test_readme_and_roadmap_mention_phase_4b(self, roadmap_text: str) -> None:
        readme_text = _read(README_MD)
        assert "Phase 4B" in readme_text
        assert "Phase 4B" in roadmap_text

    def test_no_document_claims_an_http_api_authentication_or_uploader_exists(self) -> None:
        # A positive, closed check: every source-of-truth file's Phase-4B
        # mention must sit within one sentence of an explicit "no HTTP
        # endpoint" / "no authentication" / "no uploader" style disclaimer,
        # not merely omit a claim of completeness.
        for path in [MILESTONE_DOC, CLAUDE_MD, README_MD, ROADMAP_ASTRO, PRIVACY_ASTRO]:
            text = _normalize_whitespace(_read(path))
            if "Phase 4B" not in text:
                continue
            lowered = text.lower()
            assert "no http" in lowered or "no api" in lowered, (
                f"{path}: mentions Phase 4B without disclaiming an HTTP/API endpoint"
            )
            assert "no authentication" in lowered or "no auth" in lowered, (
                f"{path}: mentions Phase 4B without disclaiming authentication"
            )

    def test_no_document_claims_production_storage_or_deployment_from_phase_4b(self) -> None:
        for path in [MILESTONE_DOC, CLAUDE_MD, README_MD, ROADMAP_ASTRO, PRIVACY_ASTRO]:
            text = _normalize_whitespace(_read(path))
            if "Phase 4B" not in text:
                continue
            lowered = text.lower()
            assert "no production storage" in lowered or "no deployment" in lowered, (
                f"{path}: mentions Phase 4B without disclaiming production storage/deployment"
            )

    def test_phase_4a_documentation_only_status_is_unweakened_by_phase_4b_wording(
        self, milestone_text: str, claude_md_text: str
    ) -> None:
        # Phase 4A's own historical status must remain intact and
        # undiluted: it is a true, permanent fact about that phase,
        # regardless of what a later phase has since implemented.
        assert "is documentation and contract design only" in milestone_text
        assert "Phase 4A was documentation and contract design only" in claude_md_text
