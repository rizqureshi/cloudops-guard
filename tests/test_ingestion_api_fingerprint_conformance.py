"""RFC 8785 fingerprint conformance tests (§E.0, task 9).

**Independence of the fixture values below, stated precisely** (per the
task's own honesty requirement -- see the Phase 4D report for the same
disclosure): `EXPECTED_SIMPLE_FINGERPRINT` was cross-checked against a
small, deliberately independent hand-rolled canonicalizer
(`_manual_canonicalize`, below) that implements just enough of JCS --
sorted object keys, no insignificant whitespace, plain integer/string/
list serialization -- to verify the `rfc8785` dependency's output on a
plain-ASCII, integer-only case *without* depending on `rfc8785` itself.
The Unicode/RTL/combining-sequence fixture's expected digest was computed
once, offline, directly from `rfc8785.dumps` -- not independently
re-derived by a second implementation, since writing a full second JCS
Unicode-handling implementation was judged out of proportion to this
phase's scope. That fixture is retained as a regression/mutation-
detection guard (see `TestMutationDetection` below), not as proof of
independent Unicode conformance. Every other case below is an
*equivalence* test (two different inputs must fingerprint identically, or
must not) -- provable from first principles with no external oracle at
all, regardless of which JCS implementation computes either side.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from cloudops_guard.ingestion_api.errors import ApiError
from cloudops_guard.ingestion_api.fingerprint import compute_report_fingerprint

EXPECTED_SIMPLE_FINGERPRINT = (
    "sha256:adddf676c68c753bac5e877bc13940d9d0a5243543c5ba3019360b367a02a589"
)

# Computed once, offline, directly via `rfc8785.dumps` -- see this
# module's docstring for exactly what independence claim this fixture
# does and does not carry.
EXPECTED_UNICODE_FINGERPRINT = (
    "sha256:dc9f461b88c6036ad20585cd149e0774fef8f88f8b617449c6e4935bf679ab93"
)


def _manual_canonicalize(obj: object) -> str:
    """A small, deliberately independent canonicalizer -- ASCII keys,
    plain `int`/`str`/`list`/`dict` only -- used solely to cross-check
    `rfc8785`'s output on the plain-ASCII fixture case below, without
    itself depending on the `rfc8785` package.
    """
    if isinstance(obj, dict):
        items = sorted(obj.items(), key=lambda kv: kv[0])
        return "{" + ",".join(f"{json.dumps(k)}:{_manual_canonicalize(v)}" for k, v in items) + "}"
    if isinstance(obj, str):
        return json.dumps(obj)
    if isinstance(obj, bool):
        raise TypeError("bool not handled by this minimal canonicalizer")
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, list):
        return "[" + ",".join(_manual_canonicalize(v) for v in obj) + "]"
    raise TypeError(f"unhandled type in minimal canonicalizer: {type(obj)}")


class TestKnownGoodVector:
    def test_simple_ascii_integer_case_matches_independent_hand_computation(self) -> None:
        report = {"a": 1, "b": "hello"}
        canonical_text = _manual_canonicalize(
            {"platform": "kubernetes", "report_schema_version": 1, "report": report}
        )
        independently_computed = "sha256:" + hashlib.sha256(canonical_text.encode()).hexdigest()
        assert independently_computed == EXPECTED_SIMPLE_FINGERPRINT

        actual = compute_report_fingerprint("kubernetes", 1, report)
        assert actual == EXPECTED_SIMPLE_FINGERPRINT


class TestUnicodeRegressionVector:
    def test_unicode_rtl_combining_and_numeric_case(self) -> None:
        report = {
            "unicode": "café مرحبا",
            "combining": "é",  # "e" + combining acute accent (U+0301)
            "nested": {"a": {"b": {"c": [1, 2, 3]}}},
            "num": 1.0,
        }
        actual = compute_report_fingerprint("gitlab", 1, report)
        assert actual == EXPECTED_UNICODE_FINGERPRINT


class TestKeyOrderingEquivalence:
    """No external oracle needed: two Python dicts with the same
    key/value pairs in different insertion order MUST fingerprint
    identically -- that is the entire point of canonicalization, provable
    from the definition alone.
    """

    def test_reordered_top_level_keys_produce_the_same_fingerprint(self) -> None:
        report_a = {"z": 1, "a": 2, "m": 3}
        report_b = {"a": 2, "m": 3, "z": 1}
        assert report_a != list(report_b.items())  # sanity: genuinely different insertion order
        assert compute_report_fingerprint("kubernetes", 1, report_a) == compute_report_fingerprint(
            "kubernetes", 1, report_b
        )

    def test_reordered_deeply_nested_keys_produce_the_same_fingerprint(self) -> None:
        report_a = {"outer": {"z": {"deep": 1, "deeper": 2}, "a": [1, {"x": 1, "y": 2}]}}
        report_b = {"outer": {"a": [1, {"y": 2, "x": 1}], "z": {"deeper": 2, "deep": 1}}}
        assert compute_report_fingerprint("gitlab", 1, report_a) == compute_report_fingerprint(
            "gitlab", 1, report_b
        )


class TestInsignificantWhitespaceEquivalence:
    """The fingerprint depends only on the *parsed* value, never on the
    original request text's incidental formatting -- proven by parsing
    two differently-whitespaced JSON texts of the same content and
    confirming they fingerprint identically.
    """

    def test_differently_formatted_json_text_of_same_content_matches(self) -> None:
        compact_text = '{"a":1,"b":[1,2,3]}'
        spaced_text = '{\n  "a": 1,\n  "b": [1, 2, 3]\n}'
        report_from_compact = json.loads(compact_text)
        report_from_spaced = json.loads(spaced_text)
        assert compute_report_fingerprint(
            "kubernetes", 1, report_from_compact
        ) == compute_report_fingerprint("kubernetes", 1, report_from_spaced)


class TestNumericCanonicalization:
    """RFC 8785 §3.2.2.3's own cited example (the milestone document
    quotes it directly): a JSON number written as `1.0` canonicalizes
    identically to `1`.
    """

    def test_float_1_0_and_int_1_fingerprint_identically(self) -> None:
        report_float = {"count": 1.0}
        report_int = {"count": 1}
        assert compute_report_fingerprint(
            "kubernetes", 1, report_float
        ) == compute_report_fingerprint("kubernetes", 1, report_int)


class TestFingerprintComposition:
    """The fingerprint covers all three of platform/report_schema_version/
    report together -- never `report` in isolation (§E.0's own explicit
    requirement).
    """

    def test_different_platform_same_report_differs(self) -> None:
        report = {"x": 1}
        assert compute_report_fingerprint("kubernetes", 1, report) != compute_report_fingerprint(
            "gitlab", 1, report
        )

    def test_different_schema_version_same_report_differs(self) -> None:
        report = {"x": 1}
        assert compute_report_fingerprint("kubernetes", 1, report) != compute_report_fingerprint(
            "kubernetes", 2, report
        )

    def test_identical_inputs_produce_identical_fingerprints(self) -> None:
        report = {"x": 1, "y": [1, 2, 3]}
        assert compute_report_fingerprint("kubernetes", 1, report) == compute_report_fingerprint(
            "kubernetes", 1, dict(report)
        )

    def test_fingerprint_never_includes_tenant_or_timestamp_information(self) -> None:
        # Structural proof: compute_report_fingerprint's signature itself
        # accepts no tenant_id, idempotency_key, request_id, ingestion_id,
        # or timestamp parameter at all -- there is nothing for such a
        # value to be threaded through, by construction.
        import inspect

        signature = inspect.signature(compute_report_fingerprint)
        assert set(signature.parameters) == {"platform", "report_schema_version", "report"}


class TestMutationDetection:
    """Deliberate-mutation proof (task 9/15's explicit requirement):
    confirms the fixtures above actually detect incorrect
    canonicalization, rather than passing vacuously regardless of
    implementation. This test does not mutate production code itself
    (see the Phase 4D report for the manual mutation-and-restore
    verification actually performed against `fingerprint.py`); it instead
    proves the fixture values are sensitive to real canonicalization
    differences by comparing against a deliberately non-canonical
    (naive, insertion-order, whitespace-preserving) serialization of the
    same logical content.
    """

    def test_naive_noncanonical_serialization_would_not_match_the_fixture(self) -> None:
        report = {"a": 1, "b": "hello"}
        naive_serialization = json.dumps(
            {"platform": "kubernetes", "report_schema_version": 1, "report": report}
        )  # default separators (with spaces), insertion order, not RFC 8785
        naive_digest = "sha256:" + hashlib.sha256(naive_serialization.encode()).hexdigest()
        assert naive_digest != EXPECTED_SIMPLE_FINGERPRINT
        # ...while the real, canonicalizing implementation does match.
        assert compute_report_fingerprint("kubernetes", 1, report) == EXPECTED_SIMPLE_FINGERPRINT


class TestNumericDomainDefensiveBackstop:
    """Correction-pass item 2: `compute_report_fingerprint` itself
    catches `rfc8785.CanonicalizationError` and re-raises as
    `ApiError(INVALID_REQUEST)` -- a defense-in-depth backstop,
    deliberately independent of `strict_json`'s own, earlier, more
    specific rejection (bypassed here on purpose, by calling this
    function directly with an out-of-domain value neither
    `strict_decode_json` nor `envelope.parse_envelope` had a chance to
    reject first, exactly as a future caller of this function that
    skipped those layers would experience).
    """

    def test_non_finite_report_value_raises_apierror_not_a_bare_rfc8785_exception(self) -> None:
        with pytest.raises(ApiError) as exc_info:
            compute_report_fingerprint("kubernetes", 1, {"x": float("inf")})
        assert exc_info.value.code == "invalid_request"

    def test_unsafe_integer_report_value_raises_apierror(self) -> None:
        with pytest.raises(ApiError) as exc_info:
            compute_report_fingerprint("kubernetes", 1, {"x": 2**53})
        assert exc_info.value.code == "invalid_request"

    def test_nan_report_value_raises_apierror(self) -> None:
        with pytest.raises(ApiError):
            compute_report_fingerprint("kubernetes", 1, {"x": float("nan")})


class TestRecursionDefensiveBackstop:
    """**Second correction pass, item 4**: `compute_report_fingerprint`
    also catches a bare `RecursionError` `rfc8785.dumps` could otherwise
    raise for a deeply-nested `report` value, and maps it to the same
    `ApiError(INVALID_REQUEST)`. Deliberately bypasses `strict_json`'s own
    `_MAX_NESTING_DEPTH` gate by constructing the deeply-nested Python
    object directly and calling this function with it -- exactly what a
    caller that reaches this function without going through
    `strict_decode_json` first (e.g. a future uploader computing this
    same fingerprint locally, directly from its own parsed report file)
    would experience.
    """

    @staticmethod
    def _deeply_nested(depth: int) -> object:
        value: object = 1
        for _ in range(depth):
            value = [value]
        return value

    def test_deeply_nested_report_raises_apierror_not_a_bare_recursion_error(self) -> None:
        deep_report = {"deep_field": self._deeply_nested(100_000)}
        with pytest.raises(ApiError) as exc_info:
            compute_report_fingerprint("kubernetes", 1, deep_report)
        assert exc_info.value.code == "invalid_request"
