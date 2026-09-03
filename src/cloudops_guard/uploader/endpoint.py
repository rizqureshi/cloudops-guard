"""Local, network-free `--endpoint`/`CLOUDOPS_GUARD_INGESTION_URL`
validation for `cloudops-guard upload`.

Every check here is pure string parsing over the URL text
(`urllib.parse.urlsplit`) -- nothing in this module resolves a hostname,
opens a connection, or performs any I/O. `str.hostname`'s "loopback"
recognition (`_is_loopback_literal`) uses `ipaddress.ip_address`, which
only parses an already-literal IP address string (raising `ValueError`
for an ordinary hostname like `example.com`) -- never a DNS lookup.

**Correction pass, item 3.** The original implementation returned `raw`
unchanged and never validated a port, so it silently accepted several
malformed/dangerous inputs, each independently reproduced before this
fix: `https://:443/api/v1/reports` (empty host -- `parsed.hostname` was
never checked outside the `http://` branch, so an https URL with no
host at all passed); `https://[::1/api/v1/reports` (a malformed IPv6
literal -- `urlsplit()` itself raises an uncaught `ValueError` here, so
this crashed the CLI instead of failing with `EndpointValidationError`);
`https://example.com:bad/api/v1/reports` (an invalid port --
`SplitResult.port` was never accessed at all, so a non-numeric port was
silently ignored); and a trailing `"\n"` (accepted and returned
byte-for-byte unchanged -- `raw`, not a canonicalized value -- which
both relies on `urlsplit`'s own silent whitespace-stripping behavior
matching whatever `urllib3` does with the same string later, a parser
differential this module must never depend on, and hands a string
containing a literal control character to the summary/confirmation
prompt/transport layers as if it were clean).

Every check below now: (1) rejects any control character, ASCII
whitespace, or backslash anywhere in the *raw* string before any parsing
is attempted, rather than relying on `urlsplit`'s own undocumented
stripping behavior to make the rest of this module's parsing safe; (2)
wraps `urlsplit()` and every property access that can raise (`.hostname`,
`.port`) in `try`/`except ValueError`, translating every failure into
`EndpointValidationError`, never letting a raw parser exception escape;
(3) requires a non-empty hostname, matched against a strict fixed
allowed-character set (an ordinary ASCII/punycode hostname, or a literal
IPv4/IPv6 address) -- never a hostname containing a `%` (closing off
percent-encoding ambiguities the raw-level check above cannot see, since
`urlsplit` does not percent-decode a hostname); (4) validates an explicit
port is in the real, connectable `1-65535` range (`0` is rejected too --
not a port anything can actually connect to); (5) reconstructs and
returns **one** canonical URL built only from the validated scheme,
lower-cased hostname, and optional port, plus the one fixed required
path -- never the caller's original, possibly differently-formatted raw
string -- so `summary.py`/`confirmation.py`/the transport layer, which
`service.py` feeds this same single validated value into, can never
observe a value this module has not itself fully vetted, and so this
module's own `urlsplit`-based validation and `urllib3`'s later parsing
of that exact string can never structurally disagree about what it
means.

**Second correction pass, item 4.** `_validate_hostname` called
`ipaddress.ip_address()` *before* the percent-rejecting hostname regex,
but Python's `ipaddress` module accepts an IPv6 zone/scope identifier
(`fe80::1%eth0`, `fe80::1%25eth0` -- the RFC 4007-style "scope id" real
network stacks use to disambiguate a link-local address across multiple
interfaces): a scoped IPv6 host therefore reached `ipaddress.ip_address`
and returned successfully *before* the `%`-excluding regex was ever
consulted, completely bypassing this module's own documented "never a
hostname containing a `%`" guarantee -- independently reproduced for
both `%`- and `%25`-encoded scope ids, on both `https://` and
`http://[::1%...]`. A scope identifier is meaningless for a fixed,
named ingestion endpoint (there is no ambiguous multi-interface
link-local target to disambiguate here) and would otherwise reintroduce
exactly the `%`/`%25` parser-differential ambiguity the rest of this
module already refuses -- so `%` is now rejected **unconditionally, as
the very first check**, before any `ipaddress.ip_address()` call is
even attempted.

Separately, the plain `^[A-Za-z0-9.-]+$` hostname regex accepted
several syntactically-charset-valid but semantically-malformed DNS
names (independently reproduced): `.`, `..`, `-bad.example`,
`bad-.example`, and `a..b`. `_validate_hostname` now performs real
DNS-hostname syntax validation once the input is confirmed not to be a
literal IP address: no leading/trailing dot, no empty label (rejecting
`..`), each label 1-63 characters starting and ending with an
alphanumeric character (hyphens only interior to a label, never
leading/trailing), and the complete hostname at most 253 characters.
A hostname built *only* from digits and dots (e.g. `999.1.1.1`,
`1.2.3.4.5`, `010.0.0.1`) is rejected outright at this stage rather
than falling through to per-label syntax validation: `ipaddress.
ip_address` is itself the canonical, already-strict IPv4 parser
(confirmed to reject leading-zero/octal-looking, decimal, and
short forms), so anything digits-and-dots-only that it already refused
is not a real DNS name -- letting the label regex accept `999` as a
"valid label" would silently readmit exactly the alternative-numeric-
address-notation ambiguity `ipaddress.ip_address` itself exists to
reject.

**Correction, hexadecimal IPv4 notation.** The digits-and-dots-only
check above does not catch a hostname containing a `0x`/`0X` hex
prefix (e.g. `0x7f.0.0.1`, `0x7f000001`), since those strings contain
letters and therefore are not "digits and dots only" -- they were
independently confirmed accepted as ordinary DNS hostnames by this
module before this fix. Independently confirmed against the real
platform networking stack: `socket.inet_aton("0x7f.0.0.1")`,
`socket.inet_aton("0x7f000001")`, and `socket.inet_aton("0x7f.1")` all
resolve to `127.0.0.1`, and `socket.getaddrinfo("0x7f.0.0.1", 443)`
resolves the same way -- a genuine parser differential between this
module's own validation and the downstream networking stack the
uploader's actual connection goes through (`urllib3`, backed by the
OS resolver), directly contradicting the canonical-endpoint guarantee
this module exists to provide. `_looks_like_legacy_numeric_ipv4` now
additionally rejects any hostname whose 1-4 dot-separated components
are each shaped like a decimal or `0x`/`0X`-hex `inet_aton`-style
numeric component -- covering hexadecimal whole-address notation,
hexadecimal dotted components, and mixed hexadecimal/decimal
components. `socket.inet_aton`/`socket.getaddrinfo` are used **only**
in this module's own tests, to independently demonstrate the real
parser differential being closed -- never in this production
validation path, which performs no DNS resolution, socket call, or
network access of any kind.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import SplitResult, urlsplit

from .errors import EndpointValidationError

#: The only path this uploader is ever authorized to POST to -- no
#: subpath, no trailing slash, no query string or fragment. No production
#: default is provided anywhere in this module: no production ingestion
#: service has been deployed (Phase 4D/4E), so `--endpoint`/
#: `CLOUDOPS_GUARD_INGESTION_URL` is always required, explicitly, from
#: the caller.
REQUIRED_PATH = "/api/v1/reports"

INGESTION_URL_ENV_VAR = "CLOUDOPS_GUARD_INGESTION_URL"

#: Any ASCII control character (0x00-0x1F, 0x7F), ASCII space, or
#: backslash, anywhere in the raw URL text -- checked before any parsing
#: is attempted at all, rather than trusting `urlsplit`'s own silent
#: whitespace-stripping behaviour to keep the rest of this module's
#: parsing safe (see module docstring). A backslash is rejected outright
#: because some URL consumers (notably browsers, per the WHATWG URL
#: spec) treat it as equivalent to a forward slash inside the authority
#: component of an http(s) URL -- a classic authority-confusion trick
#: this module closes off by refusing it unconditionally, rather than
#: trying to reason about how any particular downstream parser would
#: treat it.
_FORBIDDEN_RAW_CHAR_RE = re.compile(r"[\x00-\x20\x7f\\]")

#: One DNS label's own syntax (RFC 1035 §2.3.1, the "preferred name
#: syntax"): starts and ends with an alphanumeric character, with
#: hyphens permitted only strictly between them -- so a bare `-`, or a
#: label beginning/ending with `-`, never matches. Unanchored,
#: deliberately -- checked with `.fullmatch()` against one label at a
#: time (never `.match()`; see `response.py`'s own item-1 discipline
#: for why an anchor-based `$` is never trusted here either).
_DNS_LABEL_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?")

#: A hostname built *only* from digits and dots -- checked before label
#: validation so a malformed/alternative-notation numeric address (see
#: module docstring) is rejected outright rather than being accepted as
#: a sequence of all-digit "labels."
_DIGITS_AND_DOTS_RE = re.compile(r"[0-9.]+")

#: One `inet_aton`-style legacy numeric IPv4 *component*: either a run
#: of plain decimal digits (this shape alone also covers an
#: octal-looking leading-zero component such as `0177` -- the character
#: class deliberately does not care about radix, only about whether the
#: component *looks* like one of the numeric forms a real OS resolver
#: still accepts) or a `0x`/`0X`-prefixed run of hex digits. Used only
#: by `_looks_like_legacy_numeric_ipv4` below -- unanchored, checked
#: with `.fullmatch()` against one component at a time.
_LEGACY_NUMERIC_COMPONENT_RE = re.compile(r"[0-9]+|0[xX][0-9a-fA-F]+")

#: RFC 1035 §3.1's own limits: 63 octets per label, 253 characters for
#: the complete dotted name.
_MAX_DNS_LABEL_LENGTH = 63
_MAX_DNS_HOSTNAME_LENGTH = 253

_MIN_PORT = 1
_MAX_PORT = 65535


def _is_loopback_literal(hostname: str) -> bool:
    """True only for a hostname that is *syntactically* loopback --
    `localhost` (case-insensitive) or a literal IP address (IPv4 or
    IPv6) whose `ipaddress.ip_address(...).is_loopback` is true. Never
    resolves an arbitrary hostname to see whether it happens to resolve
    to a loopback address -- an ordinary hostname always returns `False`
    here, before confirmation, with no network access of any kind.
    """
    if hostname.lower() == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return addr.is_loopback


def _looks_like_legacy_numeric_ipv4(hostname: str) -> bool:
    """**Correction, hexadecimal IPv4 notation.** True for a hostname
    whose every dot-separated component is individually decimal- or
    hex-numeric-shaped, with 1-4 such components -- exactly the set of
    legacy `inet_aton`-style IPv4 notations (hexadecimal whole-address
    notation, hexadecimal dotted components, mixed hexadecimal/decimal
    components, and abbreviated 1-3-component shorthand) that a real
    OS resolver still accepts even though `ipaddress.ip_address` --
    the only IPv4 parser this module trusts -- rejects every one of
    them. Independently reproduced: `socket.inet_aton("0x7f.0.0.1")`,
    `socket.inet_aton("0x7f000001")`, and `socket.inet_aton("0x7f.1")`
    all resolve to `127.0.0.1`, and `socket.getaddrinfo("0x7f.0.0.1",
    443)` resolves the same way -- so a hostname this function flags,
    if it were ever accepted here as an ordinary DNS name, would be a
    real parser differential: this module's own validation would treat
    it as a hostname while the downstream networking stack
    (`urllib3`/the OS resolver `_finalize_response`'s actual connection
    goes through) would treat it as a numeric IPv4 address, silently
    contradicting the canonical-endpoint guarantee `validate_endpoint`
    exists to provide.

    Deliberately conservative and purely local: never computes whether
    any individual component is actually in a valid range (an
    oversized component like `0xFFFFFFFF` still matches, and is still
    rejected) -- the point is to reject the entire *shape* outright,
    never to second-guess which numeric values a real resolver would
    ultimately accept. Performs no DNS resolution, socket call, or
    network access of any kind -- pure string-shape detection via
    `_LEGACY_NUMERIC_COMPONENT_RE`, mirroring `_DIGITS_AND_DOTS_RE`'s
    own purely-local design immediately below.
    """
    labels = hostname.split(".")
    if not (1 <= len(labels) <= 4):
        return False
    return all(_LEGACY_NUMERIC_COMPONENT_RE.fullmatch(label) for label in labels)


def _validate_dns_hostname(hostname: str) -> None:
    """Real DNS-hostname syntax validation (RFC 1035 §§2.3.1/3.1) for a
    hostname already confirmed **not** to be a literal IP address.
    Raises `EndpointValidationError` for: a leading/trailing dot; an
    empty label (`..`); a label starting or ending with `-`; a label
    longer than 63 characters; a complete hostname longer than 253
    characters; a hostname built entirely from digits and dots (an
    alternative/malformed numeric address notation `ipaddress.
    ip_address` itself already rejected -- see module docstring -- never
    a real DNS name, regardless of what a naive per-label charset check
    would otherwise accept); or a hostname shaped like a legacy
    hexadecimal/mixed/abbreviated `inet_aton`-style numeric IPv4 address
    (see `_looks_like_legacy_numeric_ipv4` -- a hostname such as
    `0x7f.0.0.1` is not digits-and-dots-only, so it is not already
    caught by the check immediately above it, but a real OS resolver
    still treats it as `127.0.0.1`).
    """
    if len(hostname) > _MAX_DNS_HOSTNAME_LENGTH:
        raise EndpointValidationError("endpoint host is too long.")
    if hostname.startswith(".") or hostname.endswith("."):
        raise EndpointValidationError("endpoint host must not start or end with a dot.")
    if _DIGITS_AND_DOTS_RE.fullmatch(hostname):
        raise EndpointValidationError("endpoint host is not a valid hostname or IP address.")
    if _looks_like_legacy_numeric_ipv4(hostname):
        raise EndpointValidationError("endpoint host is not a valid hostname or IP address.")

    labels = hostname.split(".")
    for label in labels:
        if not label:
            raise EndpointValidationError("endpoint host must not contain an empty label.")
        if len(label) > _MAX_DNS_LABEL_LENGTH:
            raise EndpointValidationError("endpoint host contains a label that is too long.")
        if not _DNS_LABEL_RE.fullmatch(label):
            raise EndpointValidationError(
                "endpoint host contains a label with an invalid character or hyphen placement."
            )


def _validate_hostname(hostname: str) -> tuple[str, bool]:
    """Returns `(hostname, is_ipv6)`, both needed to reconstruct the
    canonical authority (an IPv6 literal must be re-wrapped in `[]`).
    Raises `EndpointValidationError` for anything that is neither a
    literal IPv4/IPv6 address (with no zone/scope identifier -- see
    module docstring) nor a syntactically valid DNS hostname.
    """
    if "%" in hostname:
        # Checked first, unconditionally, before any `ipaddress.
        # ip_address()` call -- see module docstring: that call itself
        # accepts an IPv6 zone/scope identifier, which would otherwise
        # bypass this exact check entirely for a scoped IPv6 literal.
        raise EndpointValidationError(
            "endpoint host must not include a zone/scope identifier or a percent sign."
        )

    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return str(addr), addr.version == 6

    _validate_dns_hostname(hostname)
    return hostname, False


def validate_endpoint(raw: str) -> str:
    """Validates `raw` as the ingestion API upload URL and returns **one
    canonical, reconstructed URL string** -- never `raw` itself -- once
    every check below has passed. That same returned string is what
    `service.py` feeds into the summary, the confirmation prompt, and the
    actual transport request: nothing downstream ever sees `raw` again.

    Raises `EndpointValidationError` for: a control character, ASCII
    whitespace, or backslash anywhere in `raw`; anything `urlsplit`
    itself cannot parse (e.g. a malformed IPv6 literal); a missing scheme
    or authority; a scheme other than `http`/`https`; an embedded
    username or password (`user:pass@host`); any query parameters; a
    fragment; a path other than exactly `/api/v1/reports`; a missing,
    malformed, or out-of-range (`1-65535`) explicit port; a hostname
    containing a character outside the strict allowed set (never a
    literal IP address's own valid characters, and never `%`); or
    `http://` used against a non-loopback host (HTTPS is required
    everywhere except an explicit, syntactically-recognized loopback
    address, for local tests and development).
    """
    if _FORBIDDEN_RAW_CHAR_RE.search(raw):
        raise EndpointValidationError(
            "endpoint must not contain a control character, whitespace, or backslash."
        )

    try:
        parsed: SplitResult = urlsplit(raw)
    except ValueError:
        raise EndpointValidationError("endpoint could not be parsed as a URL.") from None

    if parsed.scheme not in ("http", "https"):
        raise EndpointValidationError(
            "endpoint must use the http or https scheme, e.g. https://ingest.example.com/api/v1/reports."
        )
    if not parsed.netloc:
        raise EndpointValidationError("endpoint is missing its authority (host[:port]).")
    if parsed.username is not None or parsed.password is not None:
        raise EndpointValidationError("endpoint must not embed a username or password.")
    if parsed.query:
        raise EndpointValidationError("endpoint must not include query parameters.")
    if parsed.fragment:
        raise EndpointValidationError("endpoint must not include a fragment.")
    if parsed.path != REQUIRED_PATH:
        raise EndpointValidationError(
            f"endpoint path must be exactly {REQUIRED_PATH!r} (no trailing slash, no subpath)."
        )

    try:
        raw_hostname = parsed.hostname
    except ValueError:
        raise EndpointValidationError("endpoint host could not be parsed.") from None
    if not raw_hostname:
        raise EndpointValidationError("endpoint is missing a host.")
    hostname, is_ipv6 = _validate_hostname(raw_hostname)

    try:
        port = parsed.port
    except ValueError:
        raise EndpointValidationError("endpoint port is invalid.") from None
    if port is not None and not (_MIN_PORT <= port <= _MAX_PORT):
        raise EndpointValidationError(f"endpoint port must be between {_MIN_PORT} and {_MAX_PORT}.")

    if parsed.scheme == "http" and not _is_loopback_literal(hostname):
        raise EndpointValidationError(
            "endpoint must use https:// -- http:// is only accepted for an explicit "
            "loopback address (localhost, 127.0.0.0/8, or ::1), for local tests and development."
        )

    host_for_url = f"[{hostname}]" if is_ipv6 else hostname
    authority = host_for_url if port is None else f"{host_for_url}:{port}"
    return f"{parsed.scheme}://{authority}{REQUIRED_PATH}"
