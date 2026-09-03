"""Strict JSON decoding (`docs/milestones/v0.4.0-ingestion-api.md` §E.0):
rejects everything a lossy or ambiguous JSON decode would otherwise let
through silently, before any caller reasons about the decoded value at
all.

**Phase 4E relocation**: this is the authoritative implementation,
shared, unduplicated, between the ingestion API (`cloudops_guard.
ingestion_api.strict_json`, now a thin compatibility shim over this
module) and the CLI uploader (`cloudops_guard.uploader`) -- moved here,
into the dependency-free `cloudops_guard.ingestion` package, specifically
so the base CLI can use it without requiring the `api` optional-dependency
group. Raises `errors.StrictJsonRejected` (a plain `IngestionStorageError`,
never an HTTP-flavored exception) uniformly; the ingestion API's own
compatibility shim translates that into its `ApiError(INVALID_REQUEST)`
HTTP-boundary contract, preserving that package's existing observable
behavior unchanged.

RFC 8785 (JCS) is built on I-JSON (RFC 7493), which restricts numbers to
a domain every implementation can represent identically -- finite values
only, and integers within the IEEE-754 double "safe integer" range
(`+-(2**53 - 1)`, `Number.isSafeInteger`'s own definition). A JSON number
can be syntactically valid yet violate this domain in two distinct ways
Python's own `json` module does not surface as a decode error: a literal
integer token like `9007199254740992` (`2**53`, one past the safe
boundary) decodes to a perfectly valid, unlimited-precision Python `int`,
and an exponential literal like `1e400` decodes to Python's `float('inf')`
(silently, since IEEE-754 double overflow to infinity is not a Python
exception) -- neither looks anomalous to Python, but both are outside the
domain the RFC 8785 canonicalizer (and every *other* implementation
computing the same fingerprint) can agree on. `_validate_decoded_document`
walks the fully decoded document (through every nested object and array
-- not the top level alone) and rejects both cases, **before** this input
ever reaches `fingerprint.py`'s own RFC 8785 call -- which still
independently, defensively guards against the same domain violation (see
that module), since this function's own domain rules are deliberately
kept identical to, but not privileged over, `rfc8785`'s actual
enforcement.

A syntactically valid document with roughly 1,000 nested arrays/objects
could otherwise let a bare `RecursionError` escape this module entirely,
and, independently, is a plausible way to exhaust the *process's* C stack
(not just Python's, if nesting were deep enough) inside
`rfc8785.dumps`/`json.dumps`/pydantic validation downstream, none of
which this module protects against on its own. Two independent defenses
apply: (1) `_validate_decoded_document`, the combined lone-surrogate/
unsafe-number/depth walk below, is **iterative** (an explicit stack,
never Python call recursion), so it cannot itself be the thing that
exhausts the call stack; (2) it enforces `_MAX_NESTING_DEPTH` as its very
first check per node, before either of the other two per-node checks, so
a document deeper than that conservative ceiling is rejected *before*
fingerprinting, compact serialization, or Pydantic validation ever see it
-- no legitimate CloudOps Guard report (Kubernetes or GitLab, a handful
of fixed levels deep) comes anywhere close to this ceiling. `json.loads`
itself is also wrapped to convert a `RecursionError` it might
independently raise (observed, empirically, to require much deeper
nesting than `_MAX_NESTING_DEPTH` to trigger, but never assumed safe by
construction) into the same rejection, rather than ever leaking a bare
`RecursionError` to a caller.
"""

from __future__ import annotations

import json
import math
from typing import Any

from .errors import StrictJsonRejected

#: `Number.isSafeInteger`'s own bound (ECMA-262) -- the largest magnitude
#: integer every IEEE-754 double can represent exactly, and therefore the
#: largest magnitude integer RFC 8785 JCS can canonicalize identically
#: across independent implementations.
_MAX_SAFE_INTEGER = 2**53 - 1

#: A conservative, documented ceiling on container nesting depth -- counts
#: one level per object/array boundary crossed while walking the decoded
#: document, starting at 0 for the top-level value itself. Every real
#: CloudOps Guard report (Kubernetes or GitLab) is only a handful of
#: levels deep (envelope -> report -> findings[] -> one finding -> at most
#: one or two more levels of nested resource/config detail); 64 leaves
#: generous headroom above any legitimate report while sitting far below
#: where this module's own iterative walk, `json.loads`, Pydantic
#: validation, `json.dumps`, or `rfc8785.dumps` have been observed to
#: risk stack exhaustion (empirically, all four tolerate nesting at least
#: an order of magnitude deeper than this before any difficulty).
_MAX_NESTING_DEPTH = 64


def _reject_constant(constant: str) -> Any:
    # Invoked by `json.loads` for a bare `NaN`/`Infinity`/`-Infinity`
    # token, which Python's `json` module otherwise accepts as a
    # non-standard extension RFC 8259 does not define.
    raise StrictJsonRejected(f"non-standard JSON constant {constant!r} is not accepted.")


def _object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # Invoked bottom-up for every object in the document (the top-level
    # value and every nested object inside it alike), so a duplicate key
    # at any nesting level is caught here -- never the "last key wins"
    # behavior a plain `dict(pairs)` would silently apply.
    seen: set[str] = set()
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise StrictJsonRejected(f"duplicate object key {key!r}.")
        seen.add(key)
        result[key] = value
    return result


def _reject_lone_surrogate_string(value: str) -> None:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise StrictJsonRejected("string value contains an unpaired surrogate.") from exc


def _reject_unsafe_number(value: bool | int | float) -> None:
    # `bool` is an `int` subclass in Python -- `True`/`False` are never
    # numbers in the JSON sense and must never reach the `int` branch
    # below (which would otherwise "validate" them as safe integers 0/1).
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StrictJsonRejected(f"non-finite number {value!r} is not accepted.")
    elif abs(value) > _MAX_SAFE_INTEGER:
        raise StrictJsonRejected(
            f"integer {value!r} exceeds the safe integer domain (+-2**53 - 1)."
        )


def _validate_decoded_document(root: Any) -> None:
    """A single **iterative** walk (an explicit stack, never Python call
    recursion) of the fully decoded document. Enforces, per node, in this
    order: (1) `_MAX_NESTING_DEPTH`, checked *before* doing any other
    work for that node, so a too-deep document is rejected the moment
    the ceiling is crossed rather than after additional work; (2) the
    lone-surrogate check for a string; (3) the unsafe-number check for a
    `bool`/`int`/`float`. Container values (`dict`/`list`) push their
    children for later processing instead of recursing.
    """
    stack: list[tuple[Any, int]] = [(root, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > _MAX_NESTING_DEPTH:
            raise StrictJsonRejected(
                f"document nesting exceeds the maximum allowed depth ({_MAX_NESTING_DEPTH})."
            )
        if isinstance(value, str):
            _reject_lone_surrogate_string(value)
        elif isinstance(value, dict):
            for key, inner in value.items():
                stack.append((key, depth + 1))
                stack.append((inner, depth + 1))
        elif isinstance(value, list):
            for item in value:
                stack.append((item, depth + 1))
        elif isinstance(value, (bool, int, float)):
            _reject_unsafe_number(value)


def strict_decode_json(raw_body: bytes) -> Any:
    """Decodes `raw_body` under every §E.0 strict-decode rule. Raises
    `errors.StrictJsonRejected` -- never a raw `json.JSONDecodeError`,
    `UnicodeDecodeError`, `RecursionError`, or any other exception type
    -- for: invalid UTF-8 byte sequences, a duplicate object-member name
    at any nesting level, a bare `NaN`/`Infinity`/`-Infinity` literal, a
    string containing an unpaired/lone surrogate (e.g. a `\\uD800` escape
    with no matching low surrogate -- Python's `json` module happily
    decodes that into a `str` holding the raw surrogate code point, since
    Python `str` can hold arbitrary code points; this function is what
    rejects it instead), a number -- anywhere in the document, at any
    nesting level, including inside an object/array this contract does
    not otherwise interpret -- outside RFC 8785/I-JSON's representable
    domain, or a document nested deeper than this module's own
    conservative ceiling.
    """
    try:
        text = raw_body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise StrictJsonRejected("invalid UTF-8 byte sequence.") from exc

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_object_pairs_hook,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise StrictJsonRejected(str(exc)) from exc
    except RecursionError as exc:
        # `json.loads` itself, given `object_pairs_hook`, recurses in
        # pure Python for nested objects -- empirically tolerant of far
        # deeper nesting than `_MAX_NESTING_DEPTH` before any difficulty,
        # but never assumed safe by construction.
        raise StrictJsonRejected("document nesting exhausted the JSON parser.") from exc
    # A StrictJsonRejected raised from inside _object_pairs_hook/
    # _reject_constant (duplicate key, bare NaN/Infinity) is already the
    # correct, final exception type -- propagates unchanged, never
    # re-wrapped.

    _validate_decoded_document(decoded)

    return decoded
