"""Consumes `tests/fixtures/ingestion_fingerprint_fixtures_v1.json`
(correction-pass item 6): the shared, versioned RFC 8785 fingerprint
conformance fixture set required for Phase 4E's future uploader
conformance testing. Every `expected_fingerprint` value in that file was
computed once, offline, by
`tests/fixtures/generate_ingestion_fingerprint_fixtures.py` -- **never**
recomputed here at test-collection or test-run time using the same
`compute_report_fingerprint` implementation under test; every assertion
below only *compares against* the already-hard-coded value.

Each fixture is exercised through two independent paths: the pure
`compute_report_fingerprint` function, and the real
`POST /api/v1/reports` endpoint end to end -- proving both that the
fixture reports are genuinely accepted (not merely well-formed) and that
the live ingestion path produces the identical fingerprint the static
fixture file already commits to.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from cloudops_guard.ingestion_api.fingerprint import compute_report_fingerprint
from cloudops_guard.ingestion_api.report_validation import validate_report
from tests.ingestion_api_support import IngestionApiTestHarness, with_client

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ingestion_fingerprint_fixtures_v1.json"


def _load_fixture_doc() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


FIXTURE_DOC = _load_fixture_doc()
CASES = FIXTURE_DOC["cases"]
CASES_BY_NAME = {case["name"]: case for case in CASES}


class TestFixtureFileItself:
    def test_fixture_set_version_is_1(self) -> None:
        assert FIXTURE_DOC["fixture_set_version"] == 1

    def test_at_least_one_case_per_platform(self) -> None:
        platforms = {case["platform"] for case in CASES}
        assert platforms == {"kubernetes", "gitlab"}

    def test_every_case_has_the_required_fields(self) -> None:
        for case in CASES:
            assert set(case.keys()) == {
                "name",
                "platform",
                "report_schema_version",
                "notes",
                "report",
                "expected_fingerprint",
            }
            assert case["expected_fingerprint"].startswith("sha256:")
            assert len(case["expected_fingerprint"]) == len("sha256:") + 64

    def test_no_invalid_or_rejection_fixtures_are_mixed_in(self) -> None:
        # This file is exclusively genuinely-accepted reports -- rejection
        # fixtures live in their own, separate test modules (see this
        # module's own docstring). Every case here must independently
        # validate without raising.
        for case in CASES:
            validate_report(case["platform"], case["report_schema_version"], case["report"])


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
class TestEachFixtureCase:
    def test_report_is_genuinely_accepted(self, case: dict) -> None:
        # Raises if not -- the assertion is simply that this does not
        # raise.
        validate_report(case["platform"], case["report_schema_version"], case["report"])

    def test_pure_function_matches_the_hard_coded_expected_fingerprint(self, case: dict) -> None:
        actual = compute_report_fingerprint(
            case["platform"], case["report_schema_version"], case["report"]
        )
        assert actual == case["expected_fingerprint"]

    def test_real_post_endpoint_produces_the_same_fingerprint(self, case: dict) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        envelope = {
            "platform": case["platform"],
            "report_schema_version": case["report_schema_version"],
            "report": case["report"],
        }

        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                "/api/v1/reports",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(envelope),
            )

        resp = with_client(harness, _do)
        assert resp.status_code == 201
        assert resp.json()["report_fingerprint"] == case["expected_fingerprint"]


class TestKeyOrderEquivalencePairs:
    def test_every_declared_pair_shares_the_same_expected_fingerprint(self) -> None:
        pairs = FIXTURE_DOC["key_order_equivalence_pairs"]
        assert len(pairs) >= 1
        for name_a, name_b in pairs:
            assert (
                CASES_BY_NAME[name_a]["expected_fingerprint"]
                == (CASES_BY_NAME[name_b]["expected_fingerprint"])
            )

    def test_the_underlying_report_values_actually_differ_in_raw_key_order(self) -> None:
        # A non-vacuous check: the two variants must not simply be the
        # identical Python dict twice -- their SERIALIZED key order (via
        # a naive, non-canonicalizing dump) must genuinely differ, or this
        # pair would prove nothing about order-independence.
        pairs = FIXTURE_DOC["key_order_equivalence_pairs"]
        for name_a, name_b in pairs:
            report_a = CASES_BY_NAME[name_a]["report"]
            report_b = CASES_BY_NAME[name_b]["report"]
            assert list(report_a.keys()) != list(report_b.keys()) or list(
                report_a["findings"][0].keys()
            ) != list(report_b["findings"][0].keys())


class TestNumericCanonicalizationEquivalencePairs:
    def test_every_declared_pair_shares_the_same_expected_fingerprint(self) -> None:
        pairs = FIXTURE_DOC["numeric_canonicalization_equivalence_pairs"]
        assert len(pairs) >= 1
        for name_a, name_b in pairs:
            assert (
                CASES_BY_NAME[name_a]["expected_fingerprint"]
                == (CASES_BY_NAME[name_b]["expected_fingerprint"])
            )

    def test_the_pair_genuinely_differs_in_int_vs_float_typing(self) -> None:
        pairs = FIXTURE_DOC["numeric_canonicalization_equivalence_pairs"]
        for name_a, name_b in pairs:
            summary_a = CASES_BY_NAME[name_a]["report"]["summary"]
            summary_b = CASES_BY_NAME[name_b]["report"]["summary"]
            types_a = {type(v) for v in summary_a.values()}
            types_b = {type(v) for v in summary_b.values()}
            assert types_a != types_b, "the pair must genuinely differ in int vs float typing"


class TestUnicodeRtlCombiningCoverage:
    def test_at_least_one_case_per_platform_covers_unicode_rtl_and_combining_text(self) -> None:
        for platform in ("kubernetes", "gitlab"):
            matching = [
                c
                for c in CASES
                if c["platform"] == platform and "unicode" in c["name"] and "rtl" in c["name"]
            ]
            assert len(matching) >= 1, f"no unicode/RTL fixture case found for {platform}"
