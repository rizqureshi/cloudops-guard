"""One-time generator for tests/fixtures/ingestion_fingerprint_fixtures_v1.json
-- run manually, NOT part of the test suite. Computes each fixture's
expected_fingerprint ONCE, offline, via the real implementation, and
writes it as a hard-coded literal into the fixture file; the test suite
that later CONSUMES this file never recomputes these values at runtime
using the same implementation under test (it only compares against them).
"""

import copy
import json
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from cloudops_guard.ingestion_api.fingerprint import compute_report_fingerprint
from cloudops_guard.ingestion_api.report_validation import validate_report

golden_k8s = json.load(open("tests/fixtures/golden_kubernetes_report.json"))
golden_gitlab = json.load(open("tests/fixtures/golden_gitlab_report.json"))

cases = []


def add_case(name, platform, report_schema_version, report, notes):
    validate_report(platform, report_schema_version, report)  # must genuinely validate
    fp = compute_report_fingerprint(platform, report_schema_version, report)
    cases.append(
        {
            "name": name,
            "platform": platform,
            "report_schema_version": report_schema_version,
            "notes": notes,
            "report": report,
            "expected_fingerprint": fp,
        }
    )


# 1. Representative, real, multi-finding reports (the project's own
# golden fixtures, already used elsewhere as genuine released-contract
# examples).
add_case(
    "kubernetes_representative_multi_finding",
    "kubernetes",
    1,
    copy.deepcopy(golden_k8s),
    (
        "The project's own golden Kubernetes report (4 findings, "
        "multiple severities) -- the deepest structure the real "
        "Finding/AuditReport contract has (a list of several findings; "
        "Finding itself is flat)."
    ),
)
add_case(
    "gitlab_representative_multi_finding",
    "gitlab",
    1,
    copy.deepcopy(golden_gitlab),
    "The project's own golden GitLab report (6 findings, multiple severities and resource kinds).",
)

# 2. Key-order equivalence pair: two Python dicts built with DIFFERENTLY
# ORDERED key insertion, for the exact same logical report -- both must
# fingerprint identically. Kept small and hand-built for clarity.
finding_a = {
    "check_id": "K8S-IMG-001",
    "title": "Container uses the 'latest' tag",
    "severity": "high",
    "cluster_context": "prod",
    "namespace": "default",
    "resource_kind": "Pod",
    "resource_name": "web-abc123",
    "container_name": "web",
    "evidence": "image: nginx:latest",
    "impact": "Non-reproducible deployments.",
    "recommendation": "Pin to a specific tag or digest.",
    "auto_remediable": False,
    "audited_at": "2026-01-01T00:00:00Z",
}
report_order_a = {
    "cluster_context": "prod",
    "namespace_filter": None,
    "generated_at": "2026-01-01T00:00:00Z",
    "findings": [finding_a],
    "summary": {"critical": 0, "high": 1, "medium": 0, "low": 0},
}
# Same logical content, deliberately reinserted in reverse-ish key order
# at every level (top-level report AND the nested finding).
finding_b = {}
for k in [
    "audited_at",
    "auto_remediable",
    "recommendation",
    "impact",
    "evidence",
    "container_name",
    "resource_name",
    "resource_kind",
    "namespace",
    "cluster_context",
    "severity",
    "title",
    "check_id",
]:
    finding_b[k] = finding_a[k]
report_order_b = {}
for k in ["summary", "findings", "generated_at", "namespace_filter", "cluster_context"]:
    if k == "findings":
        report_order_b[k] = [finding_b]
    else:
        report_order_b[k] = report_order_a[k]

add_case(
    "kubernetes_key_order_variant_a",
    "kubernetes",
    1,
    report_order_a,
    "Key-order equivalence pair (a): top-level and nested finding keys in 'natural' order.",
)
add_case(
    "kubernetes_key_order_variant_b",
    "kubernetes",
    1,
    report_order_b,
    (
        "Key-order equivalence pair (b): SAME logical report as "
        "variant_a, every object's keys inserted in a deliberately "
        "different order -- must fingerprint identically to variant_a."
    ),
)
assert cases[-1]["expected_fingerprint"] == cases[-2]["expected_fingerprint"], (
    "key-order variants must match"
)

# 3. Unicode, combining characters, and RTL text in allowed (free-form)
# fields.
k8s_unicode_finding = {
    "check_id": "K8S-IMG-001",
    "title": "容器使用了 'latest' 标签",
    "severity": "medium",
    "cluster_context": "生产-cluster",
    "namespace": "مجال-namespace",  # Arabic "field" + namespace
    "resource_kind": "Pod",
    "resource_name": "café-pod",  # "café" via combining acute accent (U+0301)
    "container_name": "web",
    "evidence": "בדיקה: image uses :latest",  # Hebrew RTL prefix
    "impact": "Non-reproducible deployments. النشر غير قابل للتكرار.",  # trailing RTL
    "recommendation": "Pin to a specific tag or digest. 🔒",  # trailing emoji (surrogate pair)
    "auto_remediable": False,
    "audited_at": "2026-01-01T00:00:00Z",
}
k8s_unicode_report = {
    "cluster_context": "生产-cluster",
    "namespace_filter": None,
    "generated_at": "2026-01-01T00:00:00Z",
    "findings": [k8s_unicode_finding],
    "summary": {"critical": 0, "high": 0, "medium": 1, "low": 0},
}
add_case(
    "kubernetes_unicode_rtl_combining",
    "kubernetes",
    1,
    k8s_unicode_report,
    (
        "Unicode (CJK), RTL (Arabic/Hebrew), a combining-character "
        "sequence (cafe + U+0301), and an emoji outside the BMP "
        "(surrogate pair) in allowed free-form fields."
    ),
)

gitlab_unicode_finding = {
    "check_id": "GL-BR-001",
    "title": "默认分支未受保护",
    "severity": "critical",
    "project_path": "مجموعة/مشروع",  # Arabic group/project
    "resource_kind": "ProtectedBranch",
    "resource_name": "main",
    "job_name": None,
    "evidence": "café עברית RTL evidence text",
    "impact": "Anyone with push access can force-push.",
    "recommendation": "Add a protected branch rule. 🔒",
    "auto_remediable": False,
    "audited_at": "2026-01-01T00:00:00Z",
}
gitlab_unicode_report = {
    "platform": "gitlab",
    "gitlab_url": "https://gitlab.example.com",
    "project_id": 1,
    "project_path": "مجموعة/مشروع",
    "default_branch": "main",
    "generated_at": "2026-01-01T00:00:00Z",
    "findings": [gitlab_unicode_finding],
    "summary": {"critical": 1, "high": 0, "medium": 0, "low": 0},
}
add_case(
    "gitlab_unicode_rtl_combining",
    "gitlab",
    1,
    gitlab_unicode_report,
    (
        "Unicode (CJK), RTL (Arabic/Hebrew), a combining-character "
        "sequence, and a surrogate-pair emoji in allowed free-form "
        "GitLab fields."
    ),
)

# 4. Valid numeric canonicalization: summary counts expressed as
# integer-valued FLOATS (1.0) rather than ints (1) in the raw JSON --
# must still validate (matches the recomputed int count via numeric
# equality) and fingerprint per RFC 8785's canonical integer form
# (1.0 -> 1), never as a distinct value from the int form.
k8s_float_summary_report = {
    "cluster_context": "prod",
    "namespace_filter": None,
    "generated_at": "2026-01-01T00:00:00Z",
    "findings": [finding_a],
    "summary": {"critical": 0.0, "high": 1.0, "medium": 0.0, "low": 0.0},
}
add_case(
    "kubernetes_float_summary_counts",
    "kubernetes",
    1,
    k8s_float_summary_report,
    (
        "summary counts given as integer-valued floats (1.0, 0.0) "
        "rather than ints -- must validate (numeric equality against "
        "the recomputed int counts) and fingerprint identically to the "
        "equivalent int-typed report."
    ),
)
int_summary_equivalent = {
    "cluster_context": "prod",
    "namespace_filter": None,
    "generated_at": "2026-01-01T00:00:00Z",
    "findings": [finding_a],
    "summary": {"critical": 0, "high": 1, "medium": 0, "low": 0},
}
fp_float = compute_report_fingerprint("kubernetes", 1, k8s_float_summary_report)
fp_int = compute_report_fingerprint("kubernetes", 1, int_summary_equivalent)
assert fp_float == fp_int, "float vs int summary counts must fingerprint identically"

fixture_doc = {
    "fixture_set_version": 1,
    "purpose": (
        "Shared, versioned RFC 8785 fingerprint conformance fixtures for "
        "Phase 4D (server) and Phase 4E (uploader CLI) -- both sides must "
        "independently compute the exact expected_fingerprint for each "
        "case's (platform, report_schema_version, report) tuple, with no "
        "coordination required between them. Consumable unchanged by "
        "Phase 4E: a plain JSON file, no test-framework coupling."
    ),
    "algorithm": (
        'RFC 8785 JCS canonicalization of the JSON object {"platform": '
        'platform, "report_schema_version": report_schema_version, '
        '"report": report}, then SHA-256 of the canonical UTF-8 bytes, '
        'formatted as the lowercase-hex string "sha256:<hex>".'
    ),
    "generated_by": (
        "tests/fixtures/generate_ingestion_fingerprint_fixtures.py (run "
        "once, offline; expected_fingerprint values are NOT recomputed "
        "at test runtime by the implementation under test)."
    ),
    "invariant": (
        "Every case's report is a genuinely schema-valid, fully-accepted "
        "report for its platform (validated at generation time via "
        "report_validation.validate_report) -- this file intentionally "
        "contains no invalid/rejected input; strict-decode and "
        "numeric-domain REJECTION fixtures are covered separately (see "
        "test_ingestion_api_strict_json.py, "
        "test_ingestion_api_reports_post.py::TestNumericDomainRejection)."
    ),
    "key_order_equivalence_pairs": [
        ["kubernetes_key_order_variant_a", "kubernetes_key_order_variant_b"]
    ],
    # kubernetes_key_order_variant_a's summary is int-typed and logically
    # identical to kubernetes_float_summary_counts's float-typed one --
    # their matching expected_fingerprint values are this pair's proof.
    "numeric_canonicalization_equivalence_pairs": [
        ["kubernetes_float_summary_counts", "kubernetes_key_order_variant_a"]
    ],
    "cases": cases,
}

with open("tests/fixtures/ingestion_fingerprint_fixtures_v1.json", "w", encoding="utf-8") as f:
    json.dump(fixture_doc, f, indent=2, ensure_ascii=False, sort_keys=False)
    f.write("\n")

print(f"Wrote {len(cases)} cases.")
for c in cases:
    print(" -", c["name"], "->", c["expected_fingerprint"])
