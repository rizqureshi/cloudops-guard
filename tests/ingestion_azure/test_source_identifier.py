"""Adversarial tests for `resolve_azure_container_apps_client_address`
(Phase 4G-A, task 6/12 item 10): forged `X-Forwarded-For`, multiple
forwarded values, malformed IPv4/IPv6, private/link-local/scoped
addresses, duplicate forwarding headers, direct/untrusted peers, and
ambiguous proxy chains.

No network access, no real Azure Container Apps instance -- every test
constructs a minimal ASGI scope directly, mirroring
`tests/test_ingestion_api_singleton_headers.py`'s own pattern for raw
header-list testing.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from cloudops_guard.ingestion_azure.errors import SourceIdentificationError
from cloudops_guard.ingestion_azure.source_identifier import (
    resolve_azure_container_apps_client_address,
)


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/capabilities",
        "headers": headers,
        "query_string": b"",
        "client": ("10.0.0.4", 54321),  # the platform ingress's own internal peer address
    }
    return Request(scope)


class TestTheDocumentedTrustworthyCase:
    def test_single_client_no_prior_proxies(self) -> None:
        request = _request([(b"x-forwarded-for", b"203.0.113.42")])
        assert resolve_azure_container_apps_client_address(request) == "203.0.113.42"

    def test_ipv6_client(self) -> None:
        request = _request([(b"x-forwarded-for", b"2001:db8::1")])
        assert resolve_azure_container_apps_client_address(request) == "2001:db8::1"

    def test_rightmost_entry_is_used_when_client_supplied_a_chain(self) -> None:
        # A client that itself sent an X-Forwarded-For header naming
        # arbitrary upstream values -- Container Apps ingress appends its
        # own observed peer to the right. Only the rightmost entry (the
        # platform's own observation) is trusted.
        request = _request([(b"x-forwarded-for", b"198.51.100.9, 203.0.113.42")])
        assert resolve_azure_container_apps_client_address(request) == "203.0.113.42"

    def test_whitespace_around_entries_is_tolerated(self) -> None:
        request = _request([(b"x-forwarded-for", b"198.51.100.9 , 203.0.113.42")])
        assert resolve_azure_container_apps_client_address(request) == "203.0.113.42"


class TestForgedOrSpoofedLeftmostValuesNeverTrusted:
    def test_leftmost_forged_value_is_never_returned(self) -> None:
        """The core defense: even a highly plausible-looking, attacker-
        supplied leftmost value must never be what this function returns
        -- only the rightmost (platform-appended) entry.
        """
        request = _request([(b"x-forwarded-for", b"1.2.3.4, 203.0.113.42")])
        result = resolve_azure_container_apps_client_address(request)
        assert result == "203.0.113.42"
        assert result != "1.2.3.4"

    def test_long_forged_chain_still_uses_only_rightmost(self) -> None:
        forged_chain = ", ".join(f"10.0.{i}.1" for i in range(20))
        request = _request([(b"x-forwarded-for", f"{forged_chain}, 203.0.113.42".encode())])
        assert resolve_azure_container_apps_client_address(request) == "203.0.113.42"


class TestMissingOrEmptyHeaderFailsClosed:
    def test_absent_header_raises(self) -> None:
        request = _request([])
        with pytest.raises(SourceIdentificationError):
            resolve_azure_container_apps_client_address(request)

    def test_empty_value_raises(self) -> None:
        request = _request([(b"x-forwarded-for", b"")])
        with pytest.raises(SourceIdentificationError):
            resolve_azure_container_apps_client_address(request)

    def test_only_commas_raises(self) -> None:
        request = _request([(b"x-forwarded-for", b" , , ")])
        with pytest.raises(SourceIdentificationError):
            resolve_azure_container_apps_client_address(request)


class TestDuplicateHeaderOccurrenceFailsClosed:
    def test_two_separate_header_occurrences_raises(self) -> None:
        """Two distinct `X-Forwarded-For` header *occurrences* (not two
        comma-separated values within one occurrence) is itself an
        anomaly -- this must never silently pick one.
        """
        request = _request(
            [
                (b"x-forwarded-for", b"203.0.113.42"),
                (b"x-forwarded-for", b"198.51.100.9"),
            ]
        )
        with pytest.raises(SourceIdentificationError):
            resolve_azure_container_apps_client_address(request)


class TestMalformedAddressesFailClosed:
    @pytest.mark.parametrize(
        "value",
        [
            b"not-an-ip",
            b"999.999.999.999",
            b"203.0.113",
            b"203.0.113.42.99",
            b"::gibberish::",
            b"<script>alert(1)</script>",
        ],
    )
    def test_malformed_rightmost_entry_raises(self, value: bytes) -> None:
        request = _request([(b"x-forwarded-for", value)])
        with pytest.raises(SourceIdentificationError):
            resolve_azure_container_apps_client_address(request)

    def test_malformed_rightmost_with_valid_leftmost_still_raises(self) -> None:
        # A syntactically valid *leftmost* entry must never rescue a
        # malformed rightmost one -- only the rightmost is ever consulted.
        request = _request([(b"x-forwarded-for", b"203.0.113.42, not-an-ip")])
        with pytest.raises(SourceIdentificationError):
            resolve_azure_container_apps_client_address(request)


class TestPrivateLinkLocalAndScopedAddressesAreSyntacticallyAcceptedButNeverSpecialCased:
    """A private/link-local address in the rightmost (platform-trusted)
    position is syntactically valid and accepted as-is -- this module's
    job is trust placement (which entry), not IP-range policy (which
    ranges are "real" clients). A link-local/private address appearing
    here would mean Container Apps' own ingress itself is reachable only
    from such an address (a plausible internal-networking configuration,
    §8's Azure networking model) -- not a spoofing attempt, since the
    client never controls this position.
    """

    def test_private_ipv4_in_rightmost_position_is_accepted(self) -> None:
        request = _request([(b"x-forwarded-for", b"10.0.0.55")])
        assert resolve_azure_container_apps_client_address(request) == "10.0.0.55"

    def test_link_local_ipv4_in_rightmost_position_is_accepted(self) -> None:
        request = _request([(b"x-forwarded-for", b"169.254.169.254")])
        assert resolve_azure_container_apps_client_address(request) == "169.254.169.254"


class TestIpv6ZoneIdRejected:
    def test_zone_id_suffix_is_rejected(self) -> None:
        """Mirrors the exact IPv6 zone/scope-id bypass Phase 4E's own
        uploader endpoint validator discovered and closed for a different
        purpose (`fe80::1%eth0` is accepted by `ipaddress.ip_address` on
        some platforms) -- a zone id is never a meaningful abuse-
        protection scope-key component and must never be silently
        accepted here either.
        """
        request = _request([(b"x-forwarded-for", b"fe80::1%eth0")])
        with pytest.raises(SourceIdentificationError):
            resolve_azure_container_apps_client_address(request)


class TestNonUtf8HeaderFailsClosed:
    def test_invalid_utf8_bytes_raises(self) -> None:
        request = _request([(b"x-forwarded-for", b"\xff\xfe")])
        with pytest.raises(SourceIdentificationError):
            resolve_azure_container_apps_client_address(request)
