"""Tests for GitLab JSON/HTML report-file rendering (v0.2.0 Phase 2D-B,
`write_gitlab_json_report`/`write_gitlab_html_report`/`generate_gitlab_reports`
in `src/cloudops_guard/reports/generator.py`).

Only synthetic `GitLabAuditReport`/`GitLabFinding` objects are used -- no
`GitLabClient`/`GitLabCollector`/evaluator is instantiated, and no network
access occurs. The golden-fixture tests near the end of this file exist
specifically to catch any accidental change to the GitLab report output
once it becomes a contract, mirroring
`tests/test_kubernetes_contract_regression.py`'s existing pattern for the
Kubernetes report. `tests/fixtures/golden_gitlab_report.{json,html}` were
generated once from this file's `_golden_report()` and reviewed before being
treated as the new GitLab report contract -- a failing byte-for-byte
comparison means the output changed and must be explained, never "fixed" by
regenerating the fixture to match new output.
"""

from __future__ import annotations

import datetime as dt
import inspect
import json
from pathlib import Path

import pytest

from cloudops_guard.models import (
    AuditSummary,
    GitLabAuditReport,
    GitLabFinding,
    GitLabResourceKind,
    Severity,
)
from cloudops_guard.reports.generator import generate_gitlab_reports

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_JSON = FIXTURES_DIR / "golden_gitlab_report.json"
GOLDEN_HTML = FIXTURES_DIR / "golden_gitlab_report.html"

NOW = dt.datetime(2026, 3, 10, 14, 0, 0, tzinfo=dt.UTC)
PROJECT_PATH = "group/project"


def make_finding(**overrides: object) -> GitLabFinding:
    defaults: dict[str, object] = {
        "check_id": "GL-MR-001",
        "title": "Successful pipelines are not required before merge",
        "severity": Severity.MEDIUM,
        "project_path": PROJECT_PATH,
        "resource_kind": GitLabResourceKind.PROJECT,
        "resource_name": PROJECT_PATH,
        "job_name": None,
        "evidence": "The 'Pipelines must succeed' setting is disabled.",
        "impact": "Merge requests can be merged even when a pipeline fails.",
        "recommendation": "Enable 'Pipelines must succeed'.",
        "auto_remediable": False,
        "audited_at": NOW,
    }
    defaults.update(overrides)
    return GitLabFinding(**defaults)


def make_report(
    findings: list[GitLabFinding] | None = None, **overrides: object
) -> GitLabAuditReport:
    findings = findings if findings is not None else [make_finding()]
    summary = AuditSummary()
    for f in findings:
        setattr(summary, f.severity.value, getattr(summary, f.severity.value) + 1)
    defaults: dict[str, object] = {
        "gitlab_url": "https://gitlab.example.com",
        "project_id": 42,
        "project_path": PROJECT_PATH,
        "default_branch": "main",
        "generated_at": NOW,
        "findings": findings,
        "summary": summary,
    }
    defaults.update(overrides)
    return GitLabAuditReport(**defaults)


# --- 1 & 2: files written, output directory created --------------------------------


def test_generate_gitlab_reports_writes_both_files(tmp_path: Path) -> None:
    report = make_report()
    json_path, html_path = generate_gitlab_reports(report, tmp_path)
    assert json_path == tmp_path / "report.json"
    assert html_path == tmp_path / "report.html"
    assert json_path.is_file()
    assert html_path.is_file()


def test_generate_gitlab_reports_creates_output_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir"
    generate_gitlab_reports(make_report(), nested)
    assert (nested / "report.json").is_file()
    assert (nested / "report.html").is_file()


# --- 3: JSON validates back into GitLabAuditReport ----------------------------------


def test_json_report_round_trips_through_pydantic(tmp_path: Path) -> None:
    report = make_report(
        [
            make_finding(check_id="GL-MR-001", severity=Severity.MEDIUM),
            make_finding(check_id="GL-CI-001", severity=Severity.HIGH, job_name="build"),
        ]
    )
    json_path, _ = generate_gitlab_reports(report, tmp_path)

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    reloaded = GitLabAuditReport.model_validate(raw)

    assert reloaded == report


# --- 7: JSON preserves combined evaluator finding order -----------------------------


def test_json_report_preserves_finding_order_exactly() -> None:
    findings = [
        make_finding(check_id="GL-BR-002", severity=Severity.HIGH),
        make_finding(check_id="GL-MR-001", severity=Severity.MEDIUM),
        make_finding(check_id="GL-REL-001", severity=Severity.MEDIUM),
        make_finding(check_id="GL-CI-001", severity=Severity.HIGH, job_name="first"),
        make_finding(check_id="GL-CI-001", severity=Severity.HIGH, job_name="second"),
    ]
    report = make_report(findings)
    dumped = json.loads(report.model_dump_json())
    assert [f["check_id"] for f in dumped["findings"]] == [
        "GL-BR-002",
        "GL-MR-001",
        "GL-REL-001",
        "GL-CI-001",
        "GL-CI-001",
    ]
    assert [f.get("job_name") for f in dumped["findings"]] == [
        None,
        None,
        None,
        "first",
        "second",
    ]


# --- 8 & 9: HTML groups by severity, preserving relative order within a group -----


def test_html_report_groups_findings_critical_to_low_preserving_relative_order(
    tmp_path: Path,
) -> None:
    findings = [
        make_finding(check_id="GL-CI-001-A", severity=Severity.HIGH, job_name="a"),
        make_finding(check_id="GL-COST-001", severity=Severity.LOW),
        make_finding(check_id="GL-CI-001-B", severity=Severity.HIGH, job_name="b"),
        make_finding(check_id="GL-MR-001", severity=Severity.MEDIUM),
    ]
    report = make_report(findings)
    _, html_path = generate_gitlab_reports(report, tmp_path)
    html = html_path.read_text(encoding="utf-8")

    positions = {
        check_id: html.index(check_id)
        for check_id in ("GL-CI-001-A", "GL-CI-001-B", "GL-MR-001", "GL-COST-001")
    }
    # Severity group order: high, then medium, then low (no critical here).
    assert positions["GL-CI-001-A"] < positions["GL-MR-001"] < positions["GL-COST-001"]
    # Relative order preserved within the "high" group.
    assert positions["GL-CI-001-A"] < positions["GL-CI-001-B"]


# --- 10: identity fields appear correctly in HTML -----------------------------------


def test_html_report_shows_project_identity_and_generated_time(tmp_path: Path) -> None:
    report = make_report(
        [],
        gitlab_url="https://gitlab.example.com",
        project_id=4821,
        project_path="engineering/checkout-service",
        default_branch="release/2026.03",
    )
    _, html_path = generate_gitlab_reports(report, tmp_path)
    html = html_path.read_text(encoding="utf-8")

    assert "engineering/checkout-service" in html
    assert "https://gitlab.example.com" in html
    assert "4821" in html
    assert "release/2026.03" in html
    assert "2026-03-10T14:00:00" in html


def test_html_report_never_turns_the_gitlab_url_into_a_hyperlink(tmp_path: Path) -> None:
    report = make_report([], gitlab_url="https://gitlab.example.com")
    _, html_path = generate_gitlab_reports(report, tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert '<a href="https://gitlab.example.com"' not in html
    assert "<a href=" not in html


# --- 11: Project, ProtectedBranch, CIJob, CIService findings render correctly ------


@pytest.mark.parametrize(
    "resource_kind",
    [
        GitLabResourceKind.PROJECT,
        GitLabResourceKind.PROTECTED_BRANCH,
        GitLabResourceKind.CI_JOB,
        GitLabResourceKind.CI_SERVICE,
    ],
)
def test_html_report_renders_every_resource_kind_accurately(
    resource_kind: GitLabResourceKind, tmp_path: Path
) -> None:
    finding = make_finding(resource_kind=resource_kind, resource_name="some-resource")
    _, html_path = generate_gitlab_reports(make_report([finding]), tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert resource_kind.value in html


def test_html_report_does_not_mention_kubernetes_only_concepts(tmp_path: Path) -> None:
    _, html_path = generate_gitlab_reports(make_report(), tmp_path)
    html_lower = html_path.read_text(encoding="utf-8").lower()
    for forbidden in (
        "cluster_context",
        "cluster context",
        "namespace",
        "deployment",
        "container_name",
    ):
        assert forbidden not in html_lower


# --- 12: job_name appears only when non-null ----------------------------------------


def test_html_report_shows_job_name_when_present(tmp_path: Path) -> None:
    finding = make_finding(
        resource_kind=GitLabResourceKind.CI_JOB, resource_name="alpine:latest", job_name="build"
    )
    _, html_path = generate_gitlab_reports(make_report([finding]), tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert "job <strong>build</strong>" in html


def test_html_report_omits_job_label_when_job_name_is_none(tmp_path: Path) -> None:
    finding = make_finding(
        resource_kind=GitLabResourceKind.PROTECTED_BRANCH, resource_name="main", job_name=None
    )
    _, html_path = generate_gitlab_reports(make_report([finding]), tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert "&middot; job " not in html
    assert ">job<" not in html


# --- 13: evidence/impact/recommendation/auto-remediable render correctly ----------


def test_html_report_shows_evidence_impact_recommendation_and_auto_remediable(
    tmp_path: Path,
) -> None:
    finding = make_finding(
        evidence="Synthetic evidence text.",
        impact="Synthetic impact text.",
        recommendation="Synthetic recommendation text.",
        auto_remediable=False,
    )
    _, html_path = generate_gitlab_reports(make_report([finding]), tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert "Synthetic evidence text." in html
    assert "Synthetic impact text." in html
    assert "Synthetic recommendation text." in html
    assert "<dd>No</dd>" in html


def test_html_report_shows_yes_for_auto_remediable_true(tmp_path: Path) -> None:
    # No current GitLab check sets auto_remediable=True, but the template
    # must still render it correctly if a future check does.
    finding = make_finding(auto_remediable=True)
    _, html_path = generate_gitlab_reports(make_report([finding]), tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert "<dd>Yes</dd>" in html


# --- 14 & 15: empty findings -----------------------------------------------------


def test_html_report_handles_empty_findings(tmp_path: Path) -> None:
    _, html_path = generate_gitlab_reports(make_report([]), tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert "No findings" in html


def test_json_report_handles_empty_findings_and_zero_summary(tmp_path: Path) -> None:
    json_path, _ = generate_gitlab_reports(make_report([]), tmp_path)
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    assert raw["findings"] == []
    assert raw["summary"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}


# --- 16: unicode round-trips ---------------------------------------------------------


def test_reports_handle_unicode_content(tmp_path: Path) -> None:
    finding = make_finding(resource_name="wébapp-中文-\U0001f680")
    report = make_report([finding], project_path="gröup/wébapp-中文")
    json_path, html_path = generate_gitlab_reports(report, tmp_path)

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    assert raw["findings"][0]["resource_name"] == "wébapp-中文-\U0001f680"
    assert raw["project_path"] == "gröup/wébapp-中文"

    html = html_path.read_text(encoding="utf-8")
    assert "wébapp-中文-\U0001f680" in html
    assert "gröup/wébapp-中文" in html


# --- 17: no script/external resources ------------------------------------------------


def test_html_report_contains_no_javascript_or_external_resources(tmp_path: Path) -> None:
    _, html_path = generate_gitlab_reports(make_report(), tmp_path)
    html = html_path.read_text(encoding="utf-8")
    html_lower = html.lower()
    assert "<script" not in html_lower
    assert "<!doctype html>" in html_lower
    for forbidden in (
        "http://",
        "https://fonts.",
        "cdn.",
        "<link",
        "<iframe",
        "fetch(",
        "xmlhttprequest",
    ):
        assert forbidden not in html_lower


# --- 18: malicious HTML in every untrusted field is escaped -------------------------


MALICIOUS = "<script>alert(1)</script>"


def test_html_report_escapes_malicious_gitlab_url(tmp_path: Path) -> None:
    report = make_report([], gitlab_url=f"https://example.com/{MALICIOUS}")
    _, html_path = generate_gitlab_reports(report, tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert MALICIOUS not in html
    assert "&lt;script&gt;" in html


def test_html_report_escapes_malicious_project_path(tmp_path: Path) -> None:
    report = make_report([], project_path=f"group/{MALICIOUS}")
    _, html_path = generate_gitlab_reports(report, tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert MALICIOUS not in html
    assert "&lt;script&gt;" in html


def test_html_report_escapes_malicious_default_branch(tmp_path: Path) -> None:
    report = make_report([], default_branch=MALICIOUS)
    _, html_path = generate_gitlab_reports(report, tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert MALICIOUS not in html
    assert "&lt;script&gt;" in html


def test_html_report_escapes_malicious_resource_name(tmp_path: Path) -> None:
    finding = make_finding(resource_name=MALICIOUS)
    _, html_path = generate_gitlab_reports(make_report([finding]), tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert MALICIOUS not in html
    assert "&lt;script&gt;" in html


def test_html_report_escapes_malicious_job_name(tmp_path: Path) -> None:
    finding = make_finding(
        resource_kind=GitLabResourceKind.CI_JOB, resource_name="alpine:3.19", job_name=MALICIOUS
    )
    _, html_path = generate_gitlab_reports(make_report([finding]), tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert MALICIOUS not in html
    assert "&lt;script&gt;" in html


def test_html_report_escapes_malicious_evidence(tmp_path: Path) -> None:
    finding = make_finding(evidence=MALICIOUS)
    _, html_path = generate_gitlab_reports(make_report([finding]), tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert MALICIOUS not in html
    assert "&lt;script&gt;" in html


# --- 19: existing report files are replaced -----------------------------------------


def test_generate_gitlab_reports_replaces_existing_files(tmp_path: Path) -> None:
    generate_gitlab_reports(make_report([make_finding(check_id="GL-MR-001")]), tmp_path)
    first_json = (tmp_path / "report.json").read_text()

    generate_gitlab_reports(make_report([make_finding(check_id="GL-CI-001")]), tmp_path)
    second_json = (tmp_path / "report.json").read_text()

    assert first_json != second_json
    assert "GL-CI-001" in second_json
    assert "GL-MR-001" not in second_json


# --- 20: atomic-write failure leaves no truncated target and no leftover temp files


def test_write_failure_does_not_leave_a_truncated_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generate_gitlab_reports(make_report([make_finding(check_id="GL-MR-001")]), tmp_path)
    original_json = (tmp_path / "report.json").read_text()
    original_html = (tmp_path / "report.html").read_text()

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr("cloudops_guard.reports.generator.os.replace", boom)

    with pytest.raises(OSError):
        generate_gitlab_reports(make_report([make_finding(check_id="GL-CI-001")]), tmp_path)

    assert (tmp_path / "report.json").read_text() == original_json
    assert (tmp_path / "report.html").read_text() == original_html
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".report")]
    assert leftovers == []


# --- 21: template resolvable through importlib.resources -----------------------------


def test_gitlab_report_template_is_packaged() -> None:
    from importlib.resources import files

    template_path = files("cloudops_guard.reports") / "templates" / "gitlab_report.html.j2"
    assert template_path.is_file()


# --- 24: structural signature guard --------------------------------------------------


def test_generate_gitlab_reports_accepts_only_report_and_output_dir() -> None:
    signature = inspect.signature(generate_gitlab_reports)
    assert list(signature.parameters) == ["report", "output_dir"]
    forbidden_names = {"client", "collector", "token", "url", "gitlab_url", "evaluator"}
    assert not (set(signature.parameters) & forbidden_names)


# --- 25: no HTTP/environment/collector/evaluator access ------------------------------


def test_generate_gitlab_reports_performs_no_network_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib3

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("generate_gitlab_reports must not perform network access")

    monkeypatch.setattr(urllib3.PoolManager, "request", _forbidden)
    monkeypatch.setattr(urllib3.PoolManager, "urlopen", _forbidden)

    generate_gitlab_reports(make_report([make_finding()]), tmp_path)


# --- 26 & 27: no mutation, deterministic ----------------------------------------------


def test_generate_gitlab_reports_does_not_mutate_the_report(tmp_path: Path) -> None:
    report = make_report([make_finding(), make_finding(check_id="GL-CI-001")])
    original = report.model_copy(deep=True)
    generate_gitlab_reports(report, tmp_path)
    assert report == original


def test_repeated_rendering_of_the_same_report_is_deterministic(tmp_path: Path) -> None:
    report = make_report([make_finding(), make_finding(check_id="GL-CI-001")])
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    generate_gitlab_reports(report, first_dir)
    generate_gitlab_reports(report, second_dir)
    assert (first_dir / "report.json").read_bytes() == (second_dir / "report.json").read_bytes()
    assert (first_dir / "report.html").read_bytes() == (second_dir / "report.html").read_bytes()


# --- 28: output contains no fields outside the GitLabAuditReport/GitLabFinding contract


def test_json_output_contains_no_fields_outside_the_declared_contract(tmp_path: Path) -> None:
    report = make_report([make_finding(check_id="GL-CI-001", job_name="build")])
    json_path, _ = generate_gitlab_reports(report, tmp_path)
    raw = json.loads(json_path.read_text(encoding="utf-8"))

    assert set(raw.keys()) == set(GitLabAuditReport.model_fields)
    for finding in raw["findings"]:
        assert set(finding.keys()) == set(GitLabFinding.model_fields)


# ============================================================================
# Golden fixture: byte-for-byte GitLab report contract
# ============================================================================

GENERATED_AT = dt.datetime(2026, 3, 10, 14, 0, 0, tzinfo=dt.UTC)
GOLDEN_PROJECT_PATH = "engineering/checkout-service"


def _golden_report() -> GitLabAuditReport:
    """Build the exact deterministic report the golden fixtures were generated from."""
    findings = [
        GitLabFinding(
            check_id="GL-BR-001",
            title="Default branch is not protected",
            severity=Severity.HIGH,
            project_path=GOLDEN_PROJECT_PATH,
            resource_kind=GitLabResourceKind.PROTECTED_BRANCH,
            resource_name="main",
            job_name=None,
            evidence=(
                f"Project path: '{GOLDEN_PROJECT_PATH}'. Default branch: 'main'. No exact, "
                "wildcard, or inherited protected-branch rule matched the default branch."
            ),
            impact=(
                "An unprotected default branch permits force-push, deletion, and "
                "unreviewed direct pushes by anyone with push access. The default branch "
                "is typically the deployment/release source of truth."
            ),
            recommendation=(
                "Create a protected-branch rule whose name matches the default branch, "
                "with push and merge access restricted appropriately."
            ),
            auto_remediable=False,
            audited_at=GENERATED_AT,
        ),
        GitLabFinding(
            check_id="GL-MR-001",
            title="Successful pipelines are not required before merge",
            severity=Severity.MEDIUM,
            project_path=GOLDEN_PROJECT_PATH,
            resource_kind=GitLabResourceKind.PROJECT,
            resource_name=GOLDEN_PROJECT_PATH,
            job_name=None,
            evidence=(
                "The 'Pipelines must succeed' setting "
                "(only_allow_merge_if_pipeline_succeeds) is disabled."
            ),
            impact=(
                "Merge requests can be merged even when a pipeline fails, so any "
                "configured tests, builds, or security scans cannot reliably gate merges "
                "while this setting is disabled."
            ),
            recommendation=(
                "Enable 'Pipelines must succeed' in the project's merge request settings."
            ),
            auto_remediable=False,
            audited_at=GENERATED_AT,
        ),
        GitLabFinding(
            check_id="GL-COST-001",
            title="Redundant pipelines are not automatically cancelled",
            severity=Severity.LOW,
            project_path=GOLDEN_PROJECT_PATH,
            resource_kind=GitLabResourceKind.PROJECT,
            resource_name=GOLDEN_PROJECT_PATH,
            job_name=None,
            evidence=(
                "Automatic cancellation of redundant pending pipelines "
                "(auto_cancel_pending_pipelines) is 'disabled'."
            ),
            impact=(
                "With automatic cancellation disabled, superseded pending pipelines may "
                "continue consuming runner capacity rather than being cancelled in favor "
                "of a newer run. Interruptible running pipelines may also continue "
                "consuming runner capacity and compute time instead of being stopped when "
                "superseded."
            ),
            recommendation=(
                "Enable automatic cancellation of redundant, pending pipelines, unless the "
                "project intentionally requires every pipeline to run to completion for "
                "compliance or audit-history purposes."
            ),
            auto_remediable=False,
            audited_at=GENERATED_AT,
        ),
        GitLabFinding(
            check_id="GL-REL-001",
            title="Project job timeout exceeds a configurable threshold",
            severity=Severity.MEDIUM,
            project_path=GOLDEN_PROJECT_PATH,
            resource_kind=GitLabResourceKind.PROJECT,
            resource_name=GOLDEN_PROJECT_PATH,
            job_name=None,
            evidence=(
                "Configured project job timeout: 7200 seconds (120 minutes). Configured "
                "audit threshold: 3600 seconds (60 minutes)."
            ),
            impact=(
                "An excessively long job timeout lets a hung job occupy a runner slot for "
                "longer, delaying feedback and potentially increasing compute cost."
            ),
            recommendation=(
                "Lower the project's default job timeout to a value appropriate for "
                "normal jobs, and use job-level timeout overrides only for legitimately "
                "long-running work. Projects with intentionally long builds may choose a "
                "higher threshold."
            ),
            auto_remediable=False,
            audited_at=GENERATED_AT,
        ),
        GitLabFinding(
            check_id="GL-CI-001",
            title="CI job or service container image uses a mutable tag or no tag",
            severity=Severity.HIGH,
            project_path=GOLDEN_PROJECT_PATH,
            resource_kind=GitLabResourceKind.CI_JOB,
            resource_name="registry.example.com/checkout/build:latest",
            job_name="build",
            evidence=(
                "CI job 'build' uses image 'registry.example.com/checkout/build:latest' "
                "(tag: latest)."
            ),
            impact=(
                "A mutable tag such as 'latest', or no tag at all, means the exact image "
                "content used by this CI job may differ from what ran previously or what "
                "will run next time, undermining reproducibility, rollback safety, and "
                "supply-chain traceability. CI images also run with pipeline (and "
                "potentially CI job token) permissions, so an unverifiable image is a "
                "sharper risk here than an equivalent finding on an idle workload. A "
                "specific version tag is a meaningful improvement over 'latest', but a "
                "tag can still be overwritten and re-pushed in the registry -- it is not "
                "itself a guarantee of immutability."
            ),
            recommendation=(
                "Pin the image to a specific version tag at minimum. For a "
                "content-addressed, truly immutable reference, pin to a digest instead "
                "or in addition (e.g. app@sha256:...)."
            ),
            auto_remediable=False,
            audited_at=GENERATED_AT,
        ),
        GitLabFinding(
            check_id="GL-CI-001",
            title="CI job or service container image uses a mutable tag or no tag",
            severity=Severity.HIGH,
            project_path=GOLDEN_PROJECT_PATH,
            resource_kind=GitLabResourceKind.CI_SERVICE,
            resource_name="dynamic image reference",
            job_name="build",
            evidence="The CI image reference is dynamic and could not be statically verified.",
            impact=(
                "A dynamic, runtime-resolved image reference cannot be evaluated from the "
                "CI configuration alone, so its reproducibility, rollback safety, and "
                "supply-chain traceability cannot be verified. This does not mean the "
                "effective image is necessarily unpinned -- only that CloudOps Guard "
                "cannot determine that statically."
            ),
            recommendation=(
                "Replace or constrain the CI/CD variable expression so the effective "
                "image reference can be statically verified, rather than relying on a "
                "value resolved only at runtime."
            ),
            auto_remediable=False,
            audited_at=GENERATED_AT,
        ),
    ]
    summary = AuditSummary(critical=0, high=3, medium=2, low=1)
    return GitLabAuditReport(
        gitlab_url="https://gitlab.example.com",
        project_id=4821,
        project_path=GOLDEN_PROJECT_PATH,
        default_branch="main",
        generated_at=GENERATED_AT,
        findings=findings,
        summary=summary,
    )


# --- 4: JSON field order and complete output match the golden JSON byte-for-byte --


def test_golden_json_report_matches_fixture_byte_for_byte(tmp_path: Path) -> None:
    json_path, _ = generate_gitlab_reports(_golden_report(), tmp_path)
    actual = json_path.read_bytes()
    expected = GOLDEN_JSON.read_bytes()
    assert actual == expected


# --- 5: HTML matches the golden HTML byte-for-byte -----------------------------------


def test_golden_html_report_matches_fixture_byte_for_byte(tmp_path: Path) -> None:
    _, html_path = generate_gitlab_reports(_golden_report(), tmp_path)
    actual = html_path.read_bytes()
    expected = GOLDEN_HTML.read_bytes()
    assert actual == expected


def test_golden_json_fixture_validates_back_into_gitlab_audit_report() -> None:
    raw = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    reloaded = GitLabAuditReport.model_validate(raw)
    assert reloaded == _golden_report()


def test_golden_html_fixture_is_javascript_free() -> None:
    html = GOLDEN_HTML.read_text(encoding="utf-8")
    assert "<script" not in html.lower()
    assert "<!doctype html>" in html.lower()
