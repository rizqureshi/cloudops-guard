"""Safe interpretation of the ingestion API's HTTP response for
`cloudops-guard upload` -- turns a bounded `transport.TransportResponse`
into either a verified `UploadOutcome` or a raised, sanitized error.
Never echoes a raw response body, a request header, or any other
infrastructure detail the fixed API contract does not itself define as
safe to show (`docs/milestones/v0.4.0-ingestion-api.md` §E's own "never
an echoed input value... or any other infrastructure detail" rule,
applied symmetrically on the client side).

**Correction pass, item 2.** The original implementation decoded the
response with plain `json.loads` (never rejecting duplicate keys, bare
`NaN`/`Infinity`, or lone surrogates) and only checked that each expected
field was present and a non-empty string -- it never validated the
*complete* fixed envelope (no unknown-field check, no `ok` boolean check,
no identifier/timestamp/fingerprint format check, and no check that an
error response's `(status, error)` pair is even a combination the real
server contract defines). Independently reproduced as silently accepted
before this fix: a `201` missing `ok`/`received_at`; `ok: false` on a
`200`/`201`; an arbitrary `status` value; unexpected extra fields;
terminal-control characters inside `ingestion_id`/`request_id`; an error
response missing `ok`; `ok: true` on an error response; and an
`error`/status combination (`422`/`"invalid_report"`) that does not
exist anywhere in the real contract at all (§E's own `invalid_report` is
`400`, and `422` is not a status this contract defines -- see
`ingestion_api.errors.HTTP_STATUS_BY_CODE`, now imported directly here
specifically so this validation can never independently drift from the
real one).

Every response is now: (1) strict-JSON decoded, via the exact same
`cloudops_guard.ingestion.strict_json.strict_decode_json` the ingestion
API and the local-report loader both use (rejecting duplicate keys, bare
`NaN`/`Infinity`, lone surrogates, excessive nesting, and unsafe numbers
-- never a second, subtly different decoder); (2) checked for exactly
the documented field set, no more, no fewer; (3) checked field-by-field
against the exact format the real server implementation emits
(`ingestion_api.ids.generate_ingestion_id`/`generate_request_id`'s
`ing_`/`req_` + 32-lowercase-hex-character form,
`ingestion_api.fingerprint`'s `sha256:` + 64-lowercase-hex-character
form, `ingestion_api.app._format_timestamp`'s `Z`-suffixed RFC 3339
form, and the fixed literal `status`/`error` values the real
implementation can actually emit). A validation failure of any kind --
missing/unknown/wrong-typed field, malformed identifier, out-of-contract
error/status combination, or a strict-JSON rejection -- produces exactly
one fixed, generic `UploadTransportError` message, built without any
value taken from the response itself.

**Second correction pass, item 1.** Every fixed-format regex here
(`ingestion_id`/`request_id`/`report_fingerprint`) used to be compiled
`^...$` and checked with `.match()`. In Python, `$` matches immediately
*before* a trailing `\n`, not only at the true end of the string -- so
`.match()` against `^ing_[0-9a-f]{32}$` silently accepted a
syntactically valid ID immediately followed by a single newline, and
that newline then reached `UploadOutcome`/error-message text unrejected
(independently reproduced against the pre-fix implementation for both
identifiers, and for a `request_id`, whose trailing newline appeared in
the displayed error). Every regex below is now compiled **without** `^`/
`$` anchors at all and checked with `.fullmatch()`, which requires the
match to consume the *entire* string -- there is no anchor-based
leniency for `.fullmatch()` to exploit, confirmed directly:
`re.compile(r"abc").fullmatch("abc\n")` is `None`. An explicit,
independent control-character rejection (never relying on the regex
character class alone) is applied to every one of these fields before
its format regex runs, for the same defense-in-depth reason
`received_at` already had one.

**Second correction pass, item 2.** `_validate_received_at` checked only
a `Z` suffix and then called `datetime.fromisoformat`, which -- being a
general ISO-8601 parser -- accepts several forms the real
`ingestion_api.app._format_timestamp` never emits (space instead of
`T`, no separators at all, ISO week dates, a bare `HH:MM` with no
seconds, and so on; all independently confirmed accepted before this
fix). `_format_timestamp` itself is `value.astimezone(dt.UTC).isoformat()`
with a trailing `"+00:00"` replaced by `"Z"` -- `datetime.isoformat()`'s
own `timespec="auto"` default means its output is always exactly one of
two shapes: `YYYY-MM-DDTHH:MM:SS` (microsecond component omitted when
it is exactly zero) or `YYYY-MM-DDTHH:MM:SS.ffffff` (exactly six digits
when it is not) -- never three digits, never omitted-when-nonzero.
`_RECEIVED_AT_RE` now encodes exactly that shape, checked with
`.fullmatch()` first (rejecting every syntactic alternative
`fromisoformat` would otherwise tolerate); only once that syntax check
passes is `datetime.fromisoformat` called at all, purely to reject
impossible calendar/time values (`2026-02-30`, `T25:00:00`, etc. -- a
syntactically 2-digit field is not necessarily a semantically valid
one) that a fixed-width regex cannot itself express.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from cloudops_guard.ingestion.errors import StrictJsonRejected
from cloudops_guard.ingestion.strict_json import strict_decode_json
from cloudops_guard.ingestion_api.errors import HTTP_STATUS_BY_CODE

from .errors import FingerprintMismatchError, UploadTransportError
from .transport import TransportResponse

_SUCCESS_STATUSES = (200, 201)

#: Every status the real, authoritative error contract
#: (`ingestion_api.errors.HTTP_STATUS_BY_CODE`) can ever pair an error
#: code with -- derived from that single source, never a second,
#: independently-maintained list (the earlier, incorrect
#: `_DOCUMENTED_ERROR_STATUSES = (..., 422, ...)` tuple this replaces
#: was exactly that kind of drift: `422` is not a status the real
#: contract defines at all).
_DOCUMENTED_ERROR_STATUSES = frozenset(HTTP_STATUS_BY_CODE.values())

_SUCCESS_FIELDS = frozenset(
    {"ok", "ingestion_id", "request_id", "received_at", "report_fingerprint", "status"}
)
_ERROR_FIELDS = frozenset({"ok", "error", "request_id"})

#: Exactly `ingestion_api.ids.generate_ingestion_id`/`generate_request_id`'s
#: own real output shape -- `f"ing_{uuid.uuid4().hex}"` /
#: `f"req_{uuid.uuid4().hex}"`, and `uuid.UUID.hex` is always exactly 32
#: lowercase hex characters, never uppercase or dash-separated.
#: **Second correction pass, item 1**: deliberately unanchored (no `^`/
#: `$`) -- checked with `.fullmatch()`, never `.match()`, so there is no
#: `$`-before-a-trailing-newline leniency for a malicious/malformed
#: value to exploit.
_INGESTION_ID_RE = re.compile(r"ing_[0-9a-f]{32}")
_REQUEST_ID_RE = re.compile(r"req_[0-9a-f]{32}")

#: Exactly `ingestion.fingerprint.compute_report_fingerprint`'s own real
#: output shape -- `f"sha256:{digest}"`, `hashlib.sha256(...).hexdigest()`
#: always 64 lowercase hex characters. Also unanchored + `.fullmatch()`
#: -- see `_INGESTION_ID_RE` above.
_FINGERPRINT_RE = re.compile(r"sha256:[0-9a-f]{64}")

#: Exactly `ingestion_api.app._format_timestamp`'s own real output shape
#: -- `value.astimezone(dt.UTC).isoformat()` with a trailing `"+00:00"`
#: replaced by `"Z"`. `datetime.isoformat()`'s `timespec="auto"` default
#: means the fractional-seconds component is either fully omitted (when
#: the microsecond value is exactly `0`) or exactly six digits (when it
#: is not) -- never any other width. Also unanchored + `.fullmatch()`.
_RECEIVED_AT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?Z")

#: The only `status` value `POST /api/v1/reports` can ever return: the
#: real server's `_handle_reports_collection` only ever calls
#: `create_ingestion`, whose returned record is always freshly `received`
#: -- never `retired`/`deleted`, which are only reachable through the
#: separate `DELETE`/retention-sweep paths this endpoint never touches.
_SUCCESS_STATUS_VALUE = "received"

#: Generous but bounded -- real RFC 3339 timestamps (even with
#: microsecond precision) are well under this; defends the one
#: variable-length field this envelope has against an otherwise-valid-
#: looking but implausibly long string.
_MAX_RECEIVED_AT_LENGTH = 40


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    """A verified, successful upload result -- safe to print in full:
    none of these fields is a credential, a header, or report content.
    """

    created: bool
    ingestion_id: str
    request_id: str
    status: str
    report_fingerprint: str


class _EnvelopeRejected(Exception):
    """Internal-only signal for any envelope-validation failure -- never
    escapes `interpret_response`, and its own message (which may
    describe *which* check failed, for whoever reads this module's
    source) is never included in the `UploadTransportError` a caller
    actually sees; that message is always the same fixed, generic text,
    per this module's own docstring.
    """


def _require_exact_fields(body: object, expected_fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(body, dict):
        raise _EnvelopeRejected("response body is not a JSON object.")
    if set(body.keys()) != expected_fields:
        raise _EnvelopeRejected("response body does not have exactly the documented fields.")
    return body


def _require_str_field(body: dict[str, object], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value:
        raise _EnvelopeRejected(f"field {name!r} is missing or not a non-empty string.")
    return value


def _require_bool_field(body: dict[str, object], name: str, *, expected: bool) -> None:
    value = body.get(name)
    # `bool` is an `int` subclass in Python -- checked with `isinstance`,
    # never `value == expected`, which would also accept `1`/`0`/`1.0`.
    if not isinstance(value, bool) or value is not expected:
        raise _EnvelopeRejected(f"field {name!r} is not exactly {expected!r}.")


def _reject_control_characters(value: str, *, field: str) -> None:
    # Independent of every format regex below -- never relies solely on
    # a regex character class to exclude a control character, so this
    # guarantee survives even if a future edit ever loosened one of
    # those patterns.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise _EnvelopeRejected(f"{field} contains a control character.")


def _validate_ingestion_id(value: str) -> None:
    _reject_control_characters(value, field="ingestion_id")
    if not _INGESTION_ID_RE.fullmatch(value):
        raise _EnvelopeRejected("ingestion_id does not match the expected format.")


def _validate_request_id(value: str) -> None:
    _reject_control_characters(value, field="request_id")
    if not _REQUEST_ID_RE.fullmatch(value):
        raise _EnvelopeRejected("request_id does not match the expected format.")


def _validate_received_at(value: str) -> None:
    if len(value) > _MAX_RECEIVED_AT_LENGTH:
        raise _EnvelopeRejected("received_at is implausibly long.")
    _reject_control_characters(value, field="received_at")
    if not _RECEIVED_AT_RE.fullmatch(value):
        # Syntax first: rejects every ISO-8601 alternative
        # `datetime.fromisoformat` would otherwise tolerate but
        # `_format_timestamp` never emits (space instead of `T`,
        # lowercase `t`/`z`, no separators, ISO week dates, missing
        # seconds, an explicit numeric offset instead of `Z`, wrong
        # fractional-second width, or trailing whitespace/control
        # characters).
        raise _EnvelopeRejected("received_at is not in the exact format the server emits.")
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        # Semantics second: a value that already matches the exact
        # syntax above can still name an impossible calendar/time value
        # (e.g. `2026-02-30`, `T25:00:00`) -- a fixed-width regex cannot
        # itself express real calendar arithmetic, so this parse step is
        # what actually rejects those.
        raise _EnvelopeRejected("received_at is not a valid calendar timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise _EnvelopeRejected("received_at is not in UTC.")


def _validate_report_fingerprint(value: str, *, expected_fingerprint: str) -> None:
    _reject_control_characters(value, field="report_fingerprint")
    if not _FINGERPRINT_RE.fullmatch(value):
        raise _EnvelopeRejected("report_fingerprint does not match the expected format.")
    if value != expected_fingerprint:
        # Handled by the caller as FingerprintMismatchError, a distinct,
        # non-generic outcome -- never folded into the generic
        # _EnvelopeRejected path, so a legitimate mismatch is still
        # reported precisely rather than as "malformed response."
        raise FingerprintMismatchError(
            "the server's reported report_fingerprint does not match the fingerprint "
            "computed locally before this report was sent."
        )


def _parse_success_envelope(
    body: object, *, expected_fingerprint: str, created: bool
) -> UploadOutcome:
    fields = _require_exact_fields(body, _SUCCESS_FIELDS)
    _require_bool_field(fields, "ok", expected=True)
    ingestion_id = _require_str_field(fields, "ingestion_id")
    request_id = _require_str_field(fields, "request_id")
    received_at = _require_str_field(fields, "received_at")
    report_fingerprint = _require_str_field(fields, "report_fingerprint")
    status_value = _require_str_field(fields, "status")

    _validate_ingestion_id(ingestion_id)
    _validate_request_id(request_id)
    _validate_received_at(received_at)
    if status_value != _SUCCESS_STATUS_VALUE:
        raise _EnvelopeRejected("status is not the one value this endpoint can return.")
    _validate_report_fingerprint(report_fingerprint, expected_fingerprint=expected_fingerprint)

    return UploadOutcome(
        created=created,
        ingestion_id=ingestion_id,
        request_id=request_id,
        status=status_value,
        report_fingerprint=report_fingerprint,
    )


def _parse_error_envelope(body: object, *, http_status: int) -> tuple[str, str]:
    fields = _require_exact_fields(body, _ERROR_FIELDS)
    _require_bool_field(fields, "ok", expected=False)
    error_code = _require_str_field(fields, "error")
    request_id = _require_str_field(fields, "request_id")
    _validate_request_id(request_id)

    expected_status = HTTP_STATUS_BY_CODE.get(error_code)
    if expected_status is None:
        raise _EnvelopeRejected("error is not a code the real contract defines.")
    if expected_status != http_status:
        raise _EnvelopeRejected("error/HTTP-status combination does not match the real contract.")

    return error_code, request_id


def interpret_response(response: TransportResponse, *, expected_fingerprint: str) -> UploadOutcome:
    """Interprets one HTTP response to `POST /api/v1/reports`.

    On `200`/`201`: strict-JSON decodes the body, requires exactly the
    six documented success fields with `ok is True` and each field in
    its real, validated format, requires `report_fingerprint` to exactly
    equal `expected_fingerprint` (raising `FingerprintMismatchError`,
    never reporting success, if it does not), and returns a safe
    `UploadOutcome`.

    On any status the real error contract (`ingestion_api.errors.
    HTTP_STATUS_BY_CODE`) defines: strict-JSON decodes the body, requires
    exactly the three documented error fields with `ok is False`, a
    `request_id` in its real, validated format, and an `error` code whose
    own documented HTTP status exactly matches the response's actual
    status -- then raises `UploadTransportError` with a message built
    only from the HTTP status, the validated error code, and the
    validated request ID, all part of the documented, safe-to-display
    error contract.

    On anything else -- an undocumented status, a strict-JSON rejection,
    or *any* envelope-validation failure of any kind (missing/unknown/
    wrong-typed field, malformed identifier/timestamp, an out-of-contract
    error/status combination) -- raises `UploadTransportError` with one
    fixed, generic, sanitized message. Never lets a raw response value of
    any kind reach that message, and never lets a raw response value
    reach the caller in any other way.
    """
    is_known_status = response.status in _SUCCESS_STATUSES or response.status in (
        _DOCUMENTED_ERROR_STATUSES
    )
    if not is_known_status:
        # Checked before ever parsing the body -- an undocumented status
        # is rejected on the status alone, regardless of what the body
        # contains.
        raise UploadTransportError(
            f"the server responded with an unexpected HTTP status ({response.status})."
        )

    try:
        body = strict_decode_json(response.body)
    except StrictJsonRejected:
        raise UploadTransportError("the server's response was not valid.") from None

    if response.status in _SUCCESS_STATUSES:
        try:
            return _parse_success_envelope(
                body,
                expected_fingerprint=expected_fingerprint,
                created=response.status == 201,
            )
        except _EnvelopeRejected:
            raise UploadTransportError(
                "the server's response did not match the expected envelope."
            ) from None

    try:
        error_code, request_id = _parse_error_envelope(body, http_status=response.status)
    except _EnvelopeRejected:
        raise UploadTransportError(
            f"upload rejected: HTTP {response.status} (the server's error response did not "
            "match the expected envelope)."
        ) from None

    raise UploadTransportError(
        f"upload rejected: HTTP {response.status} (error={error_code}, request_id={request_id})."
    )
