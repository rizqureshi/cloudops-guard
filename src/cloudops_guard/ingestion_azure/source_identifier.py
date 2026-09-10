"""Resolves Phase 4F's trusted-proxy blocker for a production deployment
behind **Azure Container Apps' own external HTTP ingress** specifically
(Phase 4G-A, task 6). `ingestion_api.app._peer_source_identifier`
(unchanged by this module) derives Layer 2/2.5's abuse-protection source
key from the raw ASGI `scope["client"]` peer address -- which, behind any
reverse proxy including Azure Container Apps' own ingress, is always the
proxy's own internal address, never the real client. This module is the
production-only replacement source-identifier function a Phase 4G
entrypoint wires in instead.

**Authoritative source, quoted verbatim** (Microsoft Learn, "Ingress in
Azure Container Apps", HTTP headers table, fetched 2026-09-09,
<https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview>):

    X-Forwarded-For | The IP addresses of the client and/or intermediate
    proxies that sent the request. | IP addresses of the senders. If
    specified in initial request, it is appended to. Only the rightmost
    IP is provided by Azure Container Apps. Any other values must be
    validated by the user to prevent IP spoofing.

**The rule this module implements, and why it is safe**: Azure Container
Apps' external HTTP ingress is the *only* network path that can reach a
container app configured with external ingress -- the platform does not
expose any other route to the container's listening port (`docs/
deployment/azure-ingestion-production.md` §"Networking"). Every request
the application process ever sees has therefore already passed through
exactly one trusted hop: the platform's own ingress, which the quoted
documentation states *appends* the address it itself observed as the
direct TCP peer to the right-hand end of `X-Forwarded-For` (creating the
header if the client didn't send one, or appending to whatever the client
did send). The **rightmost** entry is therefore always the platform's own
observation, never a value the client fully controls -- every other
entry (including the leftmost, "the client's own claimed IP") originated
from the caller (or an untrusted upstream hop) and **must never be used
for an authorization or abuse-protection decision** (this is precisely
what the documentation's own "any other values must be validated by the
user to prevent IP spoofing" sentence warns against, and precisely what
this module refuses to do: it never even looks at any entry but the
rightmost).

**Fails closed** (task 6: "or fails production readiness until an
explicitly trusted proxy configuration is supplied") if
`X-Forwarded-For` is absent or empty: since Container Apps ingress is
documented to always populate it for a request that genuinely arrived
through that ingress, its absence means either a request that somehow
did not pass through the documented ingress path (a platform anomaly
this code must never silently trust), or a locally-run instance not
actually running behind Container Apps at all (e.g. a misconfigured
production entrypoint) -- either way, this module raises rather than
falling back to an untrusted value or a constant placeholder that would
silently collapse every caller into one shared abuse-protection bucket.
"""

from __future__ import annotations

import ipaddress

from starlette.requests import Request

from .errors import SourceIdentificationError

_HEADER_NAME = b"x-forwarded-for"


def _raw_header_values(request: Request, name: bytes) -> list[bytes]:
    return [value for key, value in request.scope["headers"] if key == name]


def resolve_azure_container_apps_client_address(request: Request) -> str:
    """Returns the trusted client address for a request received behind
    Azure Container Apps' external HTTP ingress -- the rightmost,
    syntactically valid IP address in the (single, required)
    `X-Forwarded-For` header. Raises `SourceIdentificationError` if the
    header is missing, empty, repeated, or its rightmost entry does not
    parse as a valid IPv4/IPv6 address (a malformed value at the position
    the platform itself is documented to write is treated as an anomaly,
    never coerced into a best-effort guess).

    Deliberately does **not** attempt to validate, trust, or make any use
    of any *other* entry in the header -- every entry to the left of the
    rightmost one is caller-supplied and must never influence an
    authorization or abuse-protection decision (see this module's own
    docstring for the full reasoning, quoting Azure's authoritative
    documentation).
    """
    values = _raw_header_values(request, _HEADER_NAME)
    if len(values) == 0:
        raise SourceIdentificationError(
            "X-Forwarded-For is absent -- Azure Container Apps' external HTTP "
            "ingress is documented to always populate this header; its absence "
            "means this request did not arrive through that ingress path, or a "
            "platform anomaly occurred. Refusing to derive a source identifier "
            "from an untrusted or absent value."
        )
    if len(values) > 1:
        # A conformant, single-hop ASGI server presents every header
        # occurrence separately, never pre-joined -- multiple raw
        # occurrences of this header (as opposed to multiple
        # comma-separated *values* within one occurrence, handled below)
        # is itself an anomaly this module refuses to guess about.
        raise SourceIdentificationError(
            "X-Forwarded-For was received as more than one distinct header "
            "occurrence -- refusing to guess which one Azure Container Apps "
            "ingress itself wrote."
        )

    try:
        raw_value = values[0].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceIdentificationError("X-Forwarded-For is not valid UTF-8.") from exc

    entries = [entry.strip() for entry in raw_value.split(",")]
    entries = [entry for entry in entries if entry != ""]
    if not entries:
        raise SourceIdentificationError("X-Forwarded-For is present but contains no entries.")

    rightmost = entries[-1]
    try:
        # `ipaddress.ip_address` accepts a bare IPv6 zone id (e.g.
        # "fe80::1%eth0") on some platforms -- Phase 4F's own uploader-
        # side endpoint validator (`uploader.endpoint`) already
        # discovered and closed this exact gap for a different purpose.
        # Reject a `%` outright here too, before ever calling
        # ip_address, for the same reason: a zone id is never a
        # meaningful abuse-protection scope-key component.
        if "%" in rightmost:
            raise ValueError("zone/scope id is not accepted")
        ipaddress.ip_address(rightmost)
    except ValueError as exc:
        raise SourceIdentificationError(
            "the rightmost X-Forwarded-For entry is not a syntactically valid IP address."
        ) from exc

    return rightmost
