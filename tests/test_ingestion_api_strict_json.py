"""Unit tests for `strict_json.strict_decode_json`'s numeric-domain
validation (correction-pass item 2): RFC 8785/I-JSON restricts numbers to
finite values and integers within the IEEE-754 double safe-integer bound
(`+-(2**53 - 1)`) -- a JSON number can be syntactically valid yet violate
this domain in a way Python's own `json` module does not surface as a
decode error (a literal integer one past the safe bound decodes to a
perfectly normal, unlimited-precision Python `int`; an exponential
literal like `1e400` silently overflows to `float('inf')`). These tests
exercise `strict_decode_json` directly, independent of the HTTP layer,
for precise coverage of every boundary case; `test_ingestion_api_reports_post.py`
covers the same guarantee end-to-end through the real POST endpoint.
"""

from __future__ import annotations

import json

import pytest

from cloudops_guard.ingestion_api.errors import ApiError
from cloudops_guard.ingestion_api.strict_json import strict_decode_json

_MAX_SAFE_INTEGER = 2**53 - 1


def _decode(obj: object) -> object:
    return strict_decode_json(json.dumps(obj).encode())


class TestSafeIntegerBoundary:
    def test_max_safe_integer_is_accepted(self) -> None:
        assert _decode({"x": _MAX_SAFE_INTEGER}) == {"x": _MAX_SAFE_INTEGER}

    def test_min_safe_integer_is_accepted(self) -> None:
        assert _decode({"x": -_MAX_SAFE_INTEGER}) == {"x": -_MAX_SAFE_INTEGER}

    def test_one_past_max_safe_integer_is_rejected(self) -> None:
        with pytest.raises(ApiError) as exc_info:
            _decode({"x": _MAX_SAFE_INTEGER + 1})
        assert exc_info.value.code == "invalid_request"

    def test_one_past_min_safe_integer_is_rejected(self) -> None:
        with pytest.raises(ApiError) as exc_info:
            _decode({"x": -_MAX_SAFE_INTEGER - 1})
        assert exc_info.value.code == "invalid_request"

    def test_2_pow_53_literal_is_rejected(self) -> None:
        # The task's own named example: 2**53 = 9007199254740992.
        with pytest.raises(ApiError):
            _decode({"x": 9007199254740992})

    def test_negative_2_pow_53_literal_is_rejected(self) -> None:
        with pytest.raises(ApiError):
            _decode({"x": -9007199254740992})

    def test_ordinary_small_integers_are_unaffected(self) -> None:
        assert _decode({"a": 0, "b": -1, "c": 1, "d": 42}) == {"a": 0, "b": -1, "c": 1, "d": 42}

    def test_bool_is_never_treated_as_an_unsafe_integer(self) -> None:
        # `bool` is an `int` subclass in Python -- must never be
        # mistakenly evaluated against the safe-integer bound.
        assert _decode({"x": True, "y": False}) == {"x": True, "y": False}


class TestNonFiniteNumbers:
    def test_exponential_overflow_to_infinity_is_rejected(self) -> None:
        # The task's own named example: Python's json module decodes
        # 1e400 to float('inf') without raising -- this must still be
        # rejected here.
        raw = b'{"x": 1e400}'
        with pytest.raises(ApiError) as exc_info:
            strict_decode_json(raw)
        assert exc_info.value.code == "invalid_request"

    def test_negative_exponential_overflow_is_rejected(self) -> None:
        raw = b'{"x": -1e400}'
        with pytest.raises(ApiError):
            strict_decode_json(raw)

    def test_ordinary_large_but_finite_float_is_accepted(self) -> None:
        # 1e300 is finite (within a double's representable range) --
        # must not be confused with the overflow case above.
        raw = b'{"x": 1e300}'
        decoded = strict_decode_json(raw)
        assert decoded["x"] == 1e300

    def test_ordinary_small_float_is_unaffected(self) -> None:
        assert _decode({"x": 1.5}) == {"x": 1.5}

    def test_bare_nan_and_infinity_literals_still_rejected_by_the_existing_check(self) -> None:
        # Unrelated to this correction (already covered by
        # _reject_constant), included here only to confirm the two
        # mechanisms do not conflict.
        with pytest.raises(ApiError):
            strict_decode_json(b'{"x": NaN}')
        with pytest.raises(ApiError):
            strict_decode_json(b'{"x": Infinity}')
        with pytest.raises(ApiError):
            strict_decode_json(b'{"x": -Infinity}')


class TestRecursiveValidation:
    def test_unsafe_integer_nested_inside_an_object_is_rejected(self) -> None:
        with pytest.raises(ApiError):
            _decode({"outer": {"inner": {"deep": 2**53}}})

    def test_unsafe_integer_nested_inside_an_array_is_rejected(self) -> None:
        with pytest.raises(ApiError):
            _decode({"items": [1, 2, {"x": 2**53}]})

    def test_unsafe_integer_inside_an_array_of_arrays_is_rejected(self) -> None:
        with pytest.raises(ApiError):
            _decode({"matrix": [[1, 2], [3, 2**53]]})

    def test_non_finite_number_nested_deeply_is_rejected(self) -> None:
        raw = json.dumps({"a": {"b": {"c": [1, 2, "REPLACE"]}}}).replace('"REPLACE"', "1e400")
        with pytest.raises(ApiError):
            strict_decode_json(raw.encode())

    def test_only_the_unsafe_value_matters_siblings_are_irrelevant(self) -> None:
        with pytest.raises(ApiError):
            _decode(
                {
                    "safe_sibling_1": 1,
                    "safe_sibling_2": "hello",
                    "nested": {"safe": True, "unsafe": 2**53},
                }
            )

    def test_document_with_no_unsafe_numbers_anywhere_is_accepted(self) -> None:
        payload = {
            "a": 1,
            "b": [1, 2, 3, {"c": -5, "d": 1.5}],
            "e": {"f": {"g": [_MAX_SAFE_INTEGER, -_MAX_SAFE_INTEGER]}},
        }
        assert _decode(payload) == payload


class TestErrorEnvelopeShape:
    def test_no_unsupported_numeric_input_ever_raises_a_non_apierror(self) -> None:
        cases = [
            b'{"x": 1e400}',
            b'{"x": -1e400}',
            json.dumps({"x": 2**53}).encode(),
            json.dumps({"x": -(2**53)}).encode(),
            json.dumps({"a": {"b": [2**53]}}).encode(),
        ]
        for raw in cases:
            with pytest.raises(ApiError) as exc_info:
                strict_decode_json(raw)
            assert exc_info.value.code == "invalid_request"
            assert exc_info.value.http_status == 400


def _nested_arrays(depth: int) -> bytes:
    return (b"[" * depth) + b"1" + (b"]" * depth)


def _nested_objects(depth: int) -> bytes:
    return (b'{"a":' * depth) + b"1" + (b"}" * depth)


class TestExcessiveNestingDepth:
    """**Second correction pass, item 4**: a syntactically valid document
    that nests far enough must never let a bare `RecursionError` escape
    `strict_decode_json` -- it must be rejected as an ordinary, sanitized
    `400 invalid_request`, the same as every other strict-decode
    violation, well before fingerprinting, compact serialization, or
    Pydantic validation ever see it.
    """

    def test_array_nesting_exactly_at_the_boundary_is_accepted(self) -> None:
        from cloudops_guard.ingestion_api.strict_json import _MAX_NESTING_DEPTH

        # Depth counts from 0 at the top-level value itself, so a document
        # with exactly `_MAX_NESTING_DEPTH` levels of array nesting is the
        # deepest one this ceiling still accepts.
        raw = _nested_arrays(_MAX_NESTING_DEPTH)
        result = strict_decode_json(raw)
        depth = 0
        node = result
        while isinstance(node, list):
            depth += 1
            node = node[0]
        assert depth == _MAX_NESTING_DEPTH

    def test_array_nesting_one_past_the_boundary_is_rejected(self) -> None:
        from cloudops_guard.ingestion_api.strict_json import _MAX_NESTING_DEPTH

        raw = _nested_arrays(_MAX_NESTING_DEPTH + 1)
        with pytest.raises(ApiError) as exc_info:
            strict_decode_json(raw)
        assert exc_info.value.code == "invalid_request"
        assert exc_info.value.http_status == 400

    def test_object_nesting_exactly_at_the_boundary_is_accepted(self) -> None:
        from cloudops_guard.ingestion_api.strict_json import _MAX_NESTING_DEPTH

        raw = _nested_objects(_MAX_NESTING_DEPTH)
        result = strict_decode_json(raw)
        depth = 0
        node = result
        while isinstance(node, dict):
            depth += 1
            node = node["a"]
        assert depth == _MAX_NESTING_DEPTH

    def test_object_nesting_one_past_the_boundary_is_rejected(self) -> None:
        from cloudops_guard.ingestion_api.strict_json import _MAX_NESTING_DEPTH

        raw = _nested_objects(_MAX_NESTING_DEPTH + 1)
        with pytest.raises(ApiError) as exc_info:
            strict_decode_json(raw)
        assert exc_info.value.code == "invalid_request"
        assert exc_info.value.http_status == 400

    def test_roughly_1000_nested_arrays_is_rejected_as_400_not_a_recursion_error(
        self,
    ) -> None:
        # The task's own exact reproduction: this used to let a bare
        # RecursionError escape strict_decode_json entirely.
        raw = _nested_arrays(1000)
        with pytest.raises(ApiError) as exc_info:
            strict_decode_json(raw)
        assert exc_info.value.code == "invalid_request"
        assert exc_info.value.http_status == 400

    def test_roughly_1000_nested_objects_is_rejected_as_400_not_a_recursion_error(
        self,
    ) -> None:
        raw = _nested_objects(1000)
        with pytest.raises(ApiError) as exc_info:
            strict_decode_json(raw)
        assert exc_info.value.code == "invalid_request"
        assert exc_info.value.http_status == 400

    def test_deep_nesting_inside_an_otherwise_normal_document_is_also_rejected(self) -> None:
        # Deep content need not be the entire document -- a shallow
        # envelope carrying one deeply-nested field must be rejected the
        # same way.
        deep = json.loads(_nested_arrays(1000))
        raw = json.dumps({"platform": "kubernetes", "report": {"deep_field": deep}}).encode()
        with pytest.raises(ApiError) as exc_info:
            strict_decode_json(raw)
        assert exc_info.value.code == "invalid_request"
        assert exc_info.value.http_status == 400
