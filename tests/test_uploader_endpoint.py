"""Local, network-free endpoint validation tests for
`cloudops_guard.uploader.endpoint`.
"""

from __future__ import annotations

import pytest

from cloudops_guard.uploader.endpoint import validate_endpoint
from cloudops_guard.uploader.errors import EndpointValidationError


class TestAcceptedEndpoints:
    def test_ordinary_https_url_is_accepted(self) -> None:
        url = "https://ingest.example.com/api/v1/reports"
        assert validate_endpoint(url) == url

    def test_https_with_port_is_accepted(self) -> None:
        url = "https://ingest.example.com:8443/api/v1/reports"
        assert validate_endpoint(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/api/v1/reports",
            "http://127.0.0.1:8080/api/v1/reports",
            "http://127.5.5.5/api/v1/reports",
            "http://localhost/api/v1/reports",
            "http://[::1]/api/v1/reports",
        ],
    )
    def test_loopback_http_is_accepted(self, url: str) -> None:
        assert validate_endpoint(url) == url

    def test_hostname_is_canonicalized_to_lowercase(self) -> None:
        # Correction pass, item 3: the canonical URL is reconstructed
        # from the validated (lower-cased) hostname, never the caller's
        # original casing.
        assert validate_endpoint("http://LOCALHOST/api/v1/reports") == (
            "http://localhost/api/v1/reports"
        )

    def test_loopback_https_is_also_accepted(self) -> None:
        url = "https://127.0.0.1/api/v1/reports"
        assert validate_endpoint(url) == url


class TestRejectedEndpoints:
    def test_missing_scheme_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="scheme"):
            validate_endpoint("ingest.example.com/api/v1/reports")

    def test_unsupported_scheme_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="scheme"):
            validate_endpoint("ftp://ingest.example.com/api/v1/reports")

    def test_missing_authority_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="authority"):
            validate_endpoint("https:///api/v1/reports")

    def test_embedded_username_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="username or password"):
            validate_endpoint("https://user@ingest.example.com/api/v1/reports")

    def test_embedded_username_and_password_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="username or password"):
            validate_endpoint("https://user:pass@ingest.example.com/api/v1/reports")

    def test_query_parameters_are_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="query parameters"):
            validate_endpoint("https://ingest.example.com/api/v1/reports?debug=1")

    def test_fragment_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="fragment"):
            validate_endpoint("https://ingest.example.com/api/v1/reports#section")

    def test_wrong_path_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="path"):
            validate_endpoint("https://ingest.example.com/reports")

    def test_trailing_slash_path_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="path"):
            validate_endpoint("https://ingest.example.com/api/v1/reports/")

    def test_subpath_prefix_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="path"):
            validate_endpoint("https://ingest.example.com/mount/api/v1/reports")

    def test_capabilities_path_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="path"):
            validate_endpoint("https://ingest.example.com/api/v1/capabilities")

    def test_plain_http_against_a_real_hostname_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="https"):
            validate_endpoint("http://ingest.example.com/api/v1/reports")

    def test_http_against_a_non_loopback_ip_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="https"):
            validate_endpoint("http://203.0.113.5/api/v1/reports")

    def test_empty_string_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError):
            validate_endpoint("")


class TestCorrectionPassReproductions:
    """**Correction pass, item 3.** Each of these four inputs was
    independently reproduced as either silently accepted, silently
    returned byte-for-byte unchanged, or as crashing with an uncaught
    `ValueError` before this fix.
    """

    def test_https_with_empty_host_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="host"):
            validate_endpoint("https://:443/api/v1/reports")

    def test_malformed_ipv6_literal_never_raises_a_raw_valueerror(self) -> None:
        with pytest.raises(EndpointValidationError):
            validate_endpoint("https://[::1/api/v1/reports")

    def test_non_numeric_port_is_rejected(self) -> None:
        with pytest.raises(EndpointValidationError, match="port"):
            validate_endpoint("https://example.com:bad/api/v1/reports")

    def test_trailing_newline_is_never_silently_accepted_unchanged(self) -> None:
        with pytest.raises(EndpointValidationError):
            validate_endpoint("https://example.com/api/v1/reports\n")


class TestAdversarialEndpoints:
    """The full adversarial matrix item 3 requires: missing hosts,
    malformed IPv6, invalid ports, control characters in every URL
    component, backslashes, whitespace, percent-encoding ambiguities,
    and userinfo variations. Every case must raise
    `EndpointValidationError` -- never a raw parser exception -- and the
    exception's own message must never contain the offending raw value
    (the malformed input could itself carry a credential-shaped or
    control-character string that must never reach a terminal).
    """

    @pytest.mark.parametrize(
        "url",
        [
            "https:///api/v1/reports",  # no host at all
            "https://:443/api/v1/reports",  # empty host, explicit port
            "https://[::1/api/v1/reports",  # malformed IPv6 (missing ])
            "https://[::1]:bad/api/v1/reports",  # malformed port on IPv6
            "https://example.com:bad/api/v1/reports",
            "https://example.com:0/api/v1/reports",  # port 0
            "https://example.com:65536/api/v1/reports",  # port out of range
            "https://example.com:-1/api/v1/reports",
            "https://example.com:99999999999999999999/api/v1/reports",
            "https://example\x00.com/api/v1/reports",  # NUL in host
            "https://example.com/api\x00/v1/reports",  # NUL in path
            "https://example.com/api/v1/reports\n",
            "https://example.com/api/v1/reports\r",
            "https://example.com/api/v1/reports\t",
            "https://example.com/api/v1/reports\x1b[31m",  # ANSI escape
            "\nhttps://example.com/api/v1/reports",  # leading newline
            " https://example.com/api/v1/reports",  # leading space
            "https://example.com /api/v1/reports",  # embedded space
            "https://exa\\mple.com/api/v1/reports",  # backslash in host
            "https:\\\\example.com/api/v1/reports",  # backslash authority trick
            "https://example.com\\@evil.com/api/v1/reports",
            "https://example.com%0a/api/v1/reports",  # percent-encoded newline in host
            "https://exa%5cmple.com/api/v1/reports",  # percent-encoded backslash in host
            "https://example%2ecom/api/v1/reports",  # percent-encoded dot
            "https://user@example.com/api/v1/reports",  # userinfo, no password
            "https://user:@example.com/api/v1/reports",  # userinfo, empty password
            "https://:pass@example.com/api/v1/reports",  # userinfo, empty username
            "https://@example.com/api/v1/reports",  # bare @, empty userinfo
            "https://user:pass@example.com:8443/api/v1/reports",
        ],
    )
    def test_adversarial_input_is_rejected_without_leaking_it(self, url: str) -> None:
        with pytest.raises(EndpointValidationError) as exc_info:
            validate_endpoint(url)
        message = str(exc_info.value)
        assert url not in message
        assert url.strip() not in message

    def test_valid_ipv6_with_port_round_trips_with_brackets(self) -> None:
        url = "https://[2001:db8::1]:8443/api/v1/reports"
        assert validate_endpoint(url) == url

    def test_percent_encoded_path_is_rejected(self) -> None:
        # The required path must match exactly, literal, unencoded.
        with pytest.raises(EndpointValidationError, match="path"):
            validate_endpoint("https://example.com/api/v1/reports%2e")


class TestIpv6ScopeIdentifierBypass:
    """**Second correction pass, item 4.** `_validate_hostname` called
    `ipaddress.ip_address()` *before* the percent-rejecting hostname
    regex -- Python's `ipaddress` module accepts an RFC 4007 IPv6
    zone/scope identifier (`fe80::1%eth0`), so a scoped IPv6 literal
    reached and passed that call before the `%`-excluding check was ever
    consulted, bypassing this module's own "never a hostname containing
    a `%`" guarantee. Each case here was independently reproduced as
    accepted before this fix.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "https://[fe80::1%25eth0]/api/v1/reports",
            "https://[fe80::1%eth0]/api/v1/reports",
            "http://[::1%25lo0]/api/v1/reports",
            "http://[::1%lo0]/api/v1/reports",
            "https://[fe80::1%2]/api/v1/reports",  # numeric scope id
        ],
    )
    def test_scoped_ipv6_literal_is_rejected(self, url: str) -> None:
        with pytest.raises(EndpointValidationError) as exc_info:
            validate_endpoint(url)
        assert url not in str(exc_info.value)

    def test_unscoped_ipv6_literal_is_still_accepted(self) -> None:
        # The fix must not overreach into rejecting ordinary,
        # unscoped IPv6 literals.
        url = "https://[fe80::1]/api/v1/reports"
        assert validate_endpoint(url) == url


class TestMalformedDnsHostnameSyntax:
    """**Second correction pass, item 4.** The original `^[A-Za-z0-9.-]+$`
    hostname regex only checked the overall character set, never real
    DNS-hostname syntax -- each case below was independently reproduced
    as accepted before this fix, despite being a malformed hostname (an
    empty label, a leading/trailing dot, or a label starting/ending with
    a hyphen).
    """

    @pytest.mark.parametrize(
        "url",
        [
            "https://./api/v1/reports",
            "https://../api/v1/reports",
            "https://-bad.example/api/v1/reports",
            "https://bad-.example/api/v1/reports",
            "https://a..b/api/v1/reports",
            "https://.example.com/api/v1/reports",
            "https://example.com./api/v1/reports",
            "https://example..com/api/v1/reports",
            "https://-/api/v1/reports",
            "https://a.-b.com/api/v1/reports",
            "https://a.b-.com/api/v1/reports",
        ],
    )
    def test_malformed_dns_label_is_rejected(self, url: str) -> None:
        with pytest.raises(EndpointValidationError) as exc_info:
            validate_endpoint(url)
        assert url not in str(exc_info.value)

    @pytest.mark.parametrize(
        "url",
        [
            "https://999.1.1.1/api/v1/reports",  # octet out of range, not a real IPv4 literal
            "https://1.2.3.4.5/api/v1/reports",  # five components
            "https://010.0.0.1/api/v1/reports",  # leading-zero/octal-looking notation
            "https://2130706433/api/v1/reports",  # decimal notation for 127.0.0.1
        ],
    )
    def test_alternative_numeric_address_notation_is_rejected(self, url: str) -> None:
        # A hostname built *only* from digits and dots that `ipaddress.
        # ip_address` itself already refused is never a real DNS name --
        # rejected outright rather than falling through to per-label
        # syntax validation (see module docstring).
        with pytest.raises(EndpointValidationError) as exc_info:
            validate_endpoint(url)
        assert url not in str(exc_info.value)

    def test_long_label_is_rejected(self) -> None:
        url = f"https://{'a' * 64}.example.com/api/v1/reports"
        with pytest.raises(EndpointValidationError):
            validate_endpoint(url)

    def test_long_hostname_is_rejected(self) -> None:
        label = "a" * 50
        host = ".".join([label] * 6)  # 6*51 - 1 = 305 chars, over the 253 ceiling
        url = f"https://{host}/api/v1/reports"
        with pytest.raises(EndpointValidationError):
            validate_endpoint(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/api/v1/reports",
            "https://a-b.example.com/api/v1/reports",
            "https://a.b.c.example.com/api/v1/reports",
            "https://xn--e1aybc.xn--p1ai/api/v1/reports",
            "https://a1-b2.example.com/api/v1/reports",
            "http://localhost/api/v1/reports",
            "http://127.0.0.1/api/v1/reports",
            "https://[::1]/api/v1/reports",
            "https://[2001:db8::1]/api/v1/reports",
        ],
    )
    def test_legitimate_hostnames_are_still_accepted(self, url: str) -> None:
        assert validate_endpoint(url) == url


class TestRealParserDifferentialReproduction:
    """**Not** a test of `cloudops_guard.uploader.endpoint` -- this class
    demonstrates, independently, that the real platform networking stack
    (`socket.inet_aton`/`socket.getaddrinfo`, backing what `urllib3`/the
    OS resolver do with a hostname this uploader's transport would
    eventually connect to) treats several legacy numeric IPv4 notations
    as `127.0.0.1`. It exists purely to justify
    `TestHexadecimalAndLegacyNumericIpv4Notation` below being a security
    fix rather than a cosmetic one, and is deliberately kept out of that
    class and out of the production validator entirely:
    `cloudops_guard.uploader.endpoint.validate_endpoint` never calls
    `socket.inet_aton`, `socket.getaddrinfo`, or any other resolution/
    connection primitive -- see that module's own docstring.

    Platform note: `socket.inet_aton` is documented by CPython as
    available on Windows and Unix; this suite runs in CI on Linux
    (GitHub Actions) and was additionally verified locally on macOS.
    """

    def test_hex_forms_are_resolved_to_127_0_0_1_by_inet_aton(self) -> None:
        import socket

        assert socket.inet_aton("0x7f.0.0.1") == socket.inet_aton("127.0.0.1")
        assert socket.inet_aton("0x7f000001") == socket.inet_aton("127.0.0.1")
        assert socket.inet_aton("0x7f.1") == socket.inet_aton("127.0.0.1")

    def test_hex_form_is_resolved_to_127_0_0_1_by_getaddrinfo(self) -> None:
        import socket

        results = socket.getaddrinfo("0x7f.0.0.1", 443)
        resolved_addresses = {result[4][0] for result in results}
        assert resolved_addresses == {"127.0.0.1"}


class TestHexadecimalAndLegacyNumericIpv4Notation:
    """**Correction, hexadecimal IPv4 notation.** `0x7f.0.0.1` and its
    relatives were previously treated as ordinary DNS hostnames (a
    pre-existing test in this file even asserted that acceptance
    directly) despite `TestRealParserDifferentialReproduction` above
    proving the real networking stack resolves them straight to
    `127.0.0.1` -- a genuine parser differential between this module's
    canonical-endpoint guarantee and the transport that actually
    connects. Every case below was independently reproduced as accepted
    before this fix.
    """

    @pytest.mark.parametrize(
        "host",
        [
            "0x7f.0.0.1",  # hexadecimal dotted components
            "0X7F.0.0.1",  # uppercase hex prefix/digits
            "0x7f000001",  # hexadecimal whole-address notation
            "0x7f.1",  # mixed hex + abbreviated decimal shorthand
            "0x7f.0x0.0x0.0x1",  # all-hex dotted components
            "127.1",  # abbreviated two-component decimal shorthand
            "127.0.1",  # abbreviated three-component decimal shorthand
            "0177.0.0.1",  # octal-looking leading-zero component
            "017700000001",  # octal-looking whole-address notation
            "2130706433",  # oversized single decimal component
            "127.0.0.01",  # leading-zero decimal octet
        ],
    )
    def test_legacy_numeric_ipv4_notation_is_rejected(self, host: str) -> None:
        url = f"https://{host}/api/v1/reports"
        with pytest.raises(EndpointValidationError) as exc_info:
            validate_endpoint(url)
        message = str(exc_info.value)
        # Proof: raises only EndpointValidationError (the type itself,
        # via pytest.raises above); never echoes the submitted URL or
        # host.
        assert url not in message
        assert host not in message

    @pytest.mark.parametrize(
        "host",
        [
            "example.com",  # ordinary DNS name
            "a-b.example.com",  # ordinary hyphenated DNS name
            "xn--e1aybc.xn--p1ai",  # punycode name
            "0x7f-server.example.com",  # contains "0x7f" but not numeric-shaped overall
            "example0x7f.com",  # contains a hex-looking substring, not a full numeric component
            "x123.example.com",  # a label starting with a bare "x", not "0x"-prefixed
            "0xzz.example.com",  # "0xzz" is not valid hex; not all labels are numeric-shaped
        ],
    )
    def test_hostnames_merely_containing_hex_looking_text_are_still_accepted(
        self, host: str
    ) -> None:
        url = f"https://{host}/api/v1/reports"
        assert validate_endpoint(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "https://127.0.0.1/api/v1/reports",  # canonical IPv4
            "https://[::1]/api/v1/reports",  # unscoped IPv6
            "http://localhost/api/v1/reports",  # localhost
            "https://xn--e1aybc.xn--p1ai/api/v1/reports",  # punycode
        ],
    )
    def test_canonical_forms_remain_accepted(self, url: str) -> None:
        assert validate_endpoint(url) == url

    def test_rejection_performs_no_dns_lookup_or_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import socket

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("endpoint validation must never touch the network.")

        monkeypatch.setattr(socket, "getaddrinfo", forbidden)
        monkeypatch.setattr(socket, "inet_aton", forbidden)
        monkeypatch.setattr(socket.socket, "connect", forbidden)

        with pytest.raises(EndpointValidationError):
            validate_endpoint("https://0x7f.0.0.1/api/v1/reports")
