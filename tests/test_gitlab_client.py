"""Tests for the GitLab HTTP client foundation (v0.2.0 Phase 2A).

No test in this file may access the real network -- see `_no_real_network`
below, which patches `urllib3.PoolManager` to fail loudly if anything ever
falls back to it. All HTTP behavior is exercised through `FakeTransport`,
an in-memory stand-in for the `Transport` protocol.
"""

from __future__ import annotations

import json
import socket
import ssl
from collections.abc import Mapping
from unittest.mock import MagicMock

import pytest
import urllib3
import urllib3.exceptions

from cloudops_guard.collectors.gitlab import (
    DEFAULT_MAX_PAGES,
    GITLAB_TOKEN_ENV_VAR,
    GitLabClient,
    GitLabClientError,
    canonicalize_gitlab_project,
    gitlab_api_base_url,
    load_gitlab_token,
    normalize_gitlab_base_url,
)

SYNTHETIC_TOKEN = "glpat-SYNTH3T1C-N3V3R-R34L-000000000000"  # test fixture only, never real


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any test ever falls through to the real transport."""

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "test attempted to use the real urllib3.PoolManager transport -- "
            "inject a FakeTransport instead"
        )

    monkeypatch.setattr(urllib3.PoolManager, "request", _forbidden)
    monkeypatch.setattr(urllib3.PoolManager, "urlopen", _forbidden)


# --- Fake transport ------------------------------------------------------------


class FakeResponse:
    def __init__(
        self, status: int, data: bytes = b"", headers: Mapping[str, str] | None = None
    ) -> None:
        self.status = status
        self.data = data
        self.headers: Mapping[str, str] = headers or {}


class FakeTransport:
    """In-memory stand-in for the `Transport` protocol.

    Queue `FakeResponse` instances or exception instances with `.queue()`;
    each call to `.request()` pops the next one. Records every call for
    assertions (headers sent, URL requested, redirect/retries flags).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._queue: list[FakeResponse | Exception] = []

    def queue(self, item: FakeResponse | Exception) -> FakeTransport:
        self._queue.append(item)
        return self

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: object,
        redirect: bool,
        retries: object,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "timeout": timeout,
                "redirect": redirect,
                "retries": retries,
            }
        )
        if not self._queue:
            raise AssertionError("FakeTransport has no queued responses left")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(
    transport: FakeTransport,
    *,
    base_url: str = "https://gitlab.example.com",
    max_pages: int = DEFAULT_MAX_PAGES,
) -> GitLabClient:
    return GitLabClient(base_url, SYNTHETIC_TOKEN, transport=transport, max_pages=max_pages)


def json_response(
    status: int, payload: object, headers: Mapping[str, str] | None = None
) -> FakeResponse:
    return FakeResponse(status, json.dumps(payload).encode("utf-8"), headers)


# --- 1. Token retrieval ---------------------------------------------------------


def test_load_gitlab_token_reads_only_the_approved_variable() -> None:
    token = load_gitlab_token({GITLAB_TOKEN_ENV_VAR: SYNTHETIC_TOKEN})
    assert token == SYNTHETIC_TOKEN


def test_load_gitlab_token_returns_value_unmodified() -> None:
    padded = f"  {SYNTHETIC_TOKEN}  "
    token = load_gitlab_token({GITLAB_TOKEN_ENV_VAR: padded})
    assert token == padded  # not stripped -- returned exactly as read


def test_load_gitlab_token_rejects_missing_value() -> None:
    with pytest.raises(GitLabClientError, match=GITLAB_TOKEN_ENV_VAR):
        load_gitlab_token({})


def test_load_gitlab_token_rejects_empty_value() -> None:
    with pytest.raises(GitLabClientError):
        load_gitlab_token({GITLAB_TOKEN_ENV_VAR: ""})


def test_load_gitlab_token_rejects_whitespace_only_value() -> None:
    with pytest.raises(GitLabClientError):
        load_gitlab_token({GITLAB_TOKEN_ENV_VAR: "   "})


def test_load_gitlab_token_ignores_other_variable_names() -> None:
    with pytest.raises(GitLabClientError):
        load_gitlab_token({"GITLAB_TOKEN": SYNTHETIC_TOKEN, "CI_JOB_TOKEN": SYNTHETIC_TOKEN})


def test_load_gitlab_token_missing_value_error_does_not_leak_other_env_values() -> None:
    with pytest.raises(GitLabClientError) as excinfo:
        load_gitlab_token({"SOME_OTHER_SECRET": "super-secret-unrelated-value"})
    assert "super-secret-unrelated-value" not in str(excinfo.value)


# --- 2. GitLab base-URL validation and normalization ----------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://gitlab.com", "https://gitlab.com"),
        ("https://gitlab.com/", "https://gitlab.com"),
        ("https://example.com/gitlab", "https://example.com/gitlab"),
        ("https://example.com/gitlab/", "https://example.com/gitlab"),
        ("https://example.com:8443", "https://example.com:8443"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("http://127.0.0.1:3000/", "http://127.0.0.1:3000"),
        ("http://[::1]:3000/", "http://[::1]:3000"),
        ("https://[::1]/", "https://[::1]"),
    ],
)
def test_normalize_gitlab_base_url_accepts_valid_urls(url: str, expected: str) -> None:
    assert normalize_gitlab_base_url(url) == expected


def test_normalize_gitlab_base_url_normalizes_trailing_slash() -> None:
    assert normalize_gitlab_base_url("https://gitlab.com///") == "https://gitlab.com"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://gitlab.com/api/v4", "https://gitlab.com"),
        ("https://gitlab.com/api/v4/", "https://gitlab.com"),
        ("https://example.com/gitlab/api/v4", "https://example.com/gitlab"),
        ("https://example.com/gitlab/api/v4/", "https://example.com/gitlab"),
    ],
)
def test_normalize_gitlab_base_url_strips_api_v4_suffix(url: str, expected: str) -> None:
    assert normalize_gitlab_base_url(url) == expected


def test_normalize_gitlab_base_url_does_not_mistake_similar_path_for_suffix() -> None:
    # "/api/v40" must not be treated as an already-present "/api/v4" suffix.
    assert normalize_gitlab_base_url("https://gitlab.example.com/api/v40") == (
        "https://gitlab.example.com/api/v40"
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not-a-url",
        "gitlab.com",
        "ftp://gitlab.com",
        "https://",
        "https://user:pass@gitlab.com",
        "https://user@gitlab.com",
        "https://gitlab.com?x=1",
        "https://gitlab.com#frag",
        "https://gitlab.com:99999",
        "https://gitlab.com:abc",
        "http://gitlab.com",  # non-loopback http
        "http://evil.example.com",
        "https://gitlab.com/\n",
        "https://gitlab.com/\x00",
    ],
)
def test_normalize_gitlab_base_url_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(GitLabClientError):
        normalize_gitlab_base_url(url)


def test_gitlab_api_base_url_appends_api_v4_exactly_once() -> None:
    assert gitlab_api_base_url("https://gitlab.com") == "https://gitlab.com/api/v4"
    assert gitlab_api_base_url("https://gitlab.com/") == "https://gitlab.com/api/v4"


def test_gitlab_api_base_url_preserves_self_managed_path_prefix() -> None:
    assert gitlab_api_base_url("https://example.com/gitlab") == "https://example.com/gitlab/api/v4"


def test_gitlab_api_base_url_is_idempotent_when_already_suffixed() -> None:
    assert gitlab_api_base_url("https://gitlab.example.com/api/v4") == (
        "https://gitlab.example.com/api/v4"
    )


def test_gitlab_api_base_url_is_idempotent_with_trailing_slash() -> None:
    assert gitlab_api_base_url("https://gitlab.example.com/api/v4/") == (
        "https://gitlab.example.com/api/v4"
    )


def test_gitlab_api_base_url_is_idempotent_with_self_managed_path_prefix() -> None:
    assert gitlab_api_base_url("https://example.com/gitlab/api/v4") == (
        "https://example.com/gitlab/api/v4"
    )


def test_gitlab_api_base_url_does_not_mistake_similar_path_for_suffix() -> None:
    # "/api/v40" must not be treated as an already-present "/api/v4" suffix.
    assert gitlab_api_base_url("https://gitlab.example.com/api/v40") == (
        "https://gitlab.example.com/api/v40/api/v4"
    )


# --- 3. Project identifier canonicalization -------------------------------------


def test_canonicalize_positive_numeric_id() -> None:
    assert canonicalize_gitlab_project(42) == "42"
    assert canonicalize_gitlab_project("42") == "42"


@pytest.mark.parametrize("value", [0, -1, "-1", "0", "-100"])
def test_canonicalize_rejects_non_positive_numeric_id(value: object) -> None:
    with pytest.raises(GitLabClientError):
        canonicalize_gitlab_project(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False])
def test_canonicalize_rejects_booleans(value: bool) -> None:
    # bool is an int subclass in Python; True/False must not silently pass
    # through as project IDs 1/0.
    with pytest.raises(GitLabClientError):
        canonicalize_gitlab_project(value)


def test_canonicalize_raw_path_is_encoded() -> None:
    assert canonicalize_gitlab_project("group/project") == "group%2Fproject"


def test_canonicalize_nested_subgroup_path() -> None:
    assert (
        canonicalize_gitlab_project("group/subgroup/subsubgroup/project")
        == "group%2Fsubgroup%2Fsubsubgroup%2Fproject"
    )


def test_canonicalize_unicode_path_uses_utf8_encoding() -> None:
    assert canonicalize_gitlab_project("gröup/prøject") == "gr%C3%B6up%2Fpr%C3%B8ject"


def test_canonicalize_already_encoded_path_is_not_double_encoded() -> None:
    assert canonicalize_gitlab_project("group%2Fsubgroup%2Fproject") == "group%2Fsubgroup%2Fproject"


def test_canonicalize_raw_and_encoded_forms_are_equivalent() -> None:
    raw = canonicalize_gitlab_project("group/subgroup/project")
    encoded = canonicalize_gitlab_project("group%2Fsubgroup%2Fproject")
    assert raw == encoded


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "/group/project",
        "group/project/",
        "group//project",
        "group/./project",
        "group/../project",
        "group\\project",
        "group/proj\x00ect",
        "group/proj%2Zect",
        "https://gitlab.com/group/project",
        "http://gitlab.com/group/project",
    ],
)
def test_canonicalize_rejects_malformed_paths(value: str) -> None:
    with pytest.raises(GitLabClientError):
        canonicalize_gitlab_project(value)


def test_canonicalize_rejects_invalid_percent_encoded_utf8() -> None:
    with pytest.raises(GitLabClientError):
        canonicalize_gitlab_project("group%2F%FF%FE")


# --- 4. Sanitized error type -----------------------------------------------------


def test_gitlab_client_error_is_a_plain_exception_type() -> None:
    assert issubclass(GitLabClientError, Exception)


def test_401_error_message_excludes_the_token() -> None:
    transport = FakeTransport().queue(FakeResponse(401, b'{"message": "401 Unauthorized"}'))
    client = make_client(transport)
    with pytest.raises(GitLabClientError) as excinfo:
        client.get_json_object("Get project", "/projects/1")
    assert SYNTHETIC_TOKEN not in str(excinfo.value)


def test_error_message_excludes_raw_response_body() -> None:
    body = b'{"message": "denied for token glpat-RAW-BODY-SECRET-VALUE"}'
    transport = FakeTransport().queue(FakeResponse(403, body))
    client = make_client(transport)
    with pytest.raises(GitLabClientError) as excinfo:
        client.get_json_object("Get project", "/projects/1")
    assert "glpat-RAW-BODY-SECRET-VALUE" not in str(excinfo.value)
    assert "denied for token" not in str(excinfo.value)


def test_error_message_excludes_raw_response_headers() -> None:
    headers = {"WWW-Authenticate": "Bearer error=invalid_token, secret=header-secret-value"}
    transport = FakeTransport().queue(FakeResponse(401, b"{}", headers))
    client = make_client(transport)
    with pytest.raises(GitLabClientError) as excinfo:
        client.get_json_object("Get project", "/projects/1")
    assert "header-secret-value" not in str(excinfo.value)
    assert "WWW-Authenticate" not in str(excinfo.value)


def test_transport_exception_text_is_not_reproduced() -> None:
    transport = FakeTransport().queue(
        urllib3.exceptions.NewConnectionError(
            MagicMock(),
            "Failed to establish a new connection: [Errno 61] token=glpat-CONN-ERR-SECRET",
        )
    )
    client = make_client(transport)
    with pytest.raises(GitLabClientError) as excinfo:
        client.get_json_object("Get project", "/projects/1")
    assert "glpat-CONN-ERR-SECRET" not in str(excinfo.value)
    assert "Errno 61" not in str(excinfo.value)


# --- Constructor validation: token and max_pages --------------------------------


def test_constructor_rejects_missing_token() -> None:
    with pytest.raises(GitLabClientError):
        GitLabClient("https://gitlab.example.com", None)  # type: ignore[arg-type]


def test_constructor_rejects_non_string_token() -> None:
    with pytest.raises(GitLabClientError):
        GitLabClient("https://gitlab.example.com", 12345)  # type: ignore[arg-type]


def test_constructor_rejects_empty_token() -> None:
    with pytest.raises(GitLabClientError):
        GitLabClient("https://gitlab.example.com", "")


def test_constructor_rejects_whitespace_only_token() -> None:
    with pytest.raises(GitLabClientError):
        GitLabClient("https://gitlab.example.com", "   ")


def test_constructor_rejected_token_value_not_in_exception() -> None:
    # A whitespace-padded but otherwise non-empty token is valid (only the
    # stripped-empty case is rejected), so use a non-string value that still
    # carries recognizable content to prove it never reaches the exception.
    with pytest.raises(GitLabClientError) as excinfo:
        GitLabClient("https://gitlab.example.com", ["glpat-REJECTED-NON-STRING-TOKEN"])  # type: ignore[arg-type]
    assert "glpat-REJECTED-NON-STRING-TOKEN" not in str(excinfo.value)


def test_constructor_accepts_and_sends_a_valid_token_exactly() -> None:
    transport = FakeTransport().queue(json_response(200, {"id": 1}))
    client = GitLabClient("https://gitlab.example.com", SYNTHETIC_TOKEN, transport=transport)
    client.get_json_object("Get project", "/projects/1")
    assert transport.calls[0]["headers"]["PRIVATE-TOKEN"] == SYNTHETIC_TOKEN


@pytest.mark.parametrize("value", [0, -1, -100, True, False, "5", 1.5])
def test_constructor_rejects_invalid_max_pages(value: object) -> None:
    with pytest.raises(GitLabClientError):
        GitLabClient("https://gitlab.example.com", SYNTHETIC_TOKEN, max_pages=value)  # type: ignore[arg-type]


def test_constructor_accepts_max_pages_boundary_value_1() -> None:
    client = GitLabClient("https://gitlab.example.com", SYNTHETIC_TOKEN, max_pages=1)
    assert client is not None


# --- instance_base_url property --------------------------------------------------


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://gitlab.com", "https://gitlab.com"),
        ("https://gitlab.com/", "https://gitlab.com"),
        ("https://gitlab.com/api/v4", "https://gitlab.com"),
        ("https://example.com/gitlab", "https://example.com/gitlab"),
        ("https://example.com/gitlab/api/v4", "https://example.com/gitlab"),
    ],
)
def test_instance_base_url_is_normalized_without_api_v4_suffix(
    base_url: str, expected: str
) -> None:
    client = GitLabClient(base_url, SYNTHETIC_TOKEN)
    assert client.instance_base_url == expected


def test_instance_base_url_and_api_base_url_are_consistent() -> None:
    client = GitLabClient("https://example.com/gitlab/api/v4", SYNTHETIC_TOKEN)
    assert client.api_base_url == f"{client.instance_base_url}/api/v4"


# --- 5 & 6. HTTP client behavior and response handling --------------------------


def test_get_json_object_sends_get_only() -> None:
    transport = FakeTransport().queue(json_response(200, {"id": 1}))
    make_client(transport).get_json_object("Get project", "/projects/1")
    assert transport.calls[0]["method"] == "GET"


def test_get_json_object_sends_private_token_header() -> None:
    transport = FakeTransport().queue(json_response(200, {"id": 1}))
    make_client(transport).get_json_object("Get project", "/projects/1")
    assert transport.calls[0]["headers"]["PRIVATE-TOKEN"] == SYNTHETIC_TOKEN


def test_get_json_object_sends_json_accept_header() -> None:
    transport = FakeTransport().queue(json_response(200, {"id": 1}))
    make_client(transport).get_json_object("Get project", "/projects/1")
    assert transport.calls[0]["headers"]["Accept"] == "application/json"


def test_token_is_never_placed_in_the_url() -> None:
    transport = FakeTransport().queue(json_response(200, {"id": 1}))
    make_client(transport).get_json_object("Get project", "/projects/1")
    assert SYNTHETIC_TOKEN not in str(transport.calls[0]["url"])


def test_request_disables_automatic_redirects() -> None:
    transport = FakeTransport().queue(json_response(200, {"id": 1}))
    make_client(transport).get_json_object("Get project", "/projects/1")
    assert transport.calls[0]["redirect"] is False


def test_request_disables_automatic_retries() -> None:
    transport = FakeTransport().queue(json_response(200, {"id": 1}))
    make_client(transport).get_json_object("Get project", "/projects/1")
    assert transport.calls[0]["retries"] is False


def test_request_uses_bounded_timeout() -> None:
    transport = FakeTransport().queue(json_response(200, {"id": 1}))
    client = GitLabClient(
        "https://gitlab.example.com",
        SYNTHETIC_TOKEN,
        transport=transport,
        connect_timeout=5.0,
        read_timeout=15.0,
    )
    client.get_json_object("Get project", "/projects/1")
    timeout = transport.calls[0]["timeout"]
    assert isinstance(timeout, urllib3.Timeout)


def test_get_json_object_returns_the_decoded_object() -> None:
    transport = FakeTransport().queue(json_response(200, {"id": 1, "path": "group/project"}))
    result = make_client(transport).get_json_object("Get project", "/projects/1")
    assert result == {"id": 1, "path": "group/project"}


def test_get_json_object_rejects_a_json_array() -> None:
    transport = FakeTransport().queue(json_response(200, [1, 2, 3]))
    with pytest.raises(GitLabClientError, match="expected a JSON object"):
        make_client(transport).get_json_object("Get project", "/projects/1")


def test_get_json_list_rejects_a_json_object() -> None:
    transport = FakeTransport().queue(json_response(200, {"not": "a list"}))
    with pytest.raises(GitLabClientError, match="expected a JSON array"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")


@pytest.mark.parametrize(
    ("status", "match"),
    [
        (301, "redirect"),
        (302, "redirect"),
        (401, "authentication failed"),
        (403, "insufficient permissions"),
        (404, "not found"),
        (429, "rate limited"),
        (418, "HTTP 418"),
        (500, "GitLab server error"),
        (503, "GitLab server error"),
    ],
)
def test_response_status_produces_sanitized_category(status: int, match: str) -> None:
    transport = FakeTransport().queue(FakeResponse(status, b"{}"))
    with pytest.raises(GitLabClientError, match=match):
        make_client(transport).get_json_object("Get project", "/projects/1")


def test_3xx_does_not_follow_the_redirect() -> None:
    transport = FakeTransport().queue(
        FakeResponse(302, b"", {"Location": "https://gitlab.example.com/projects/1"})
    )
    with pytest.raises(GitLabClientError):
        make_client(transport).get_json_object("Get project", "/projects/1")
    assert len(transport.calls) == 1  # never made a second request to follow it


def test_invalid_json_raises_sanitized_error() -> None:
    transport = FakeTransport().queue(FakeResponse(200, b"{not valid json"))
    with pytest.raises(GitLabClientError, match="not valid JSON"):
        make_client(transport).get_json_object("Get project", "/projects/1")


def test_invalid_utf8_raises_sanitized_error() -> None:
    transport = FakeTransport().queue(FakeResponse(200, b"\xff\xfe not utf-8"))
    with pytest.raises(GitLabClientError, match="not valid UTF-8"):
        make_client(transport).get_json_object("Get project", "/projects/1")


def test_connection_failure_is_sanitized() -> None:
    transport = FakeTransport().queue(
        urllib3.exceptions.NewConnectionError(MagicMock(), "Failed to establish a new connection")
    )
    with pytest.raises(GitLabClientError, match="connection failed"):
        make_client(transport).get_json_object("Get project", "/projects/1")


def test_dns_failure_is_sanitized() -> None:
    transport = FakeTransport().queue(
        urllib3.exceptions.NameResolutionError(
            "evil.example.invalid", MagicMock(), socket.gaierror("Name or service not known")
        )
    )
    with pytest.raises(GitLabClientError, match="DNS resolution failed"):
        make_client(transport).get_json_object("Get project", "/projects/1")


def test_tls_failure_is_sanitized() -> None:
    transport = FakeTransport().queue(ssl.SSLError("certificate verify failed"))
    with pytest.raises(GitLabClientError, match="TLS/certificate error"):
        make_client(transport).get_json_object("Get project", "/projects/1")


def test_urllib3_ssl_failure_is_sanitized() -> None:
    transport = FakeTransport().queue(urllib3.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(GitLabClientError, match="TLS/certificate error"):
        make_client(transport).get_json_object("Get project", "/projects/1")


def test_connect_timeout_is_sanitized() -> None:
    transport = FakeTransport().queue(urllib3.exceptions.ConnectTimeoutError("connect timed out"))
    with pytest.raises(GitLabClientError, match="connect timeout"):
        make_client(transport).get_json_object("Get project", "/projects/1")


def test_read_timeout_is_sanitized() -> None:
    transport = FakeTransport().queue(
        urllib3.exceptions.ReadTimeoutError(MagicMock(), "/projects/1", "Read timed out.")
    )
    with pytest.raises(GitLabClientError, match="read timeout"):
        make_client(transport).get_json_object("Get project", "/projects/1")


def test_unexpected_programming_error_is_not_masked() -> None:
    """An AttributeError from a badly-behaved transport must keep propagating,

    not be reported as a sanitized transport failure.
    """
    transport = FakeTransport().queue(AttributeError("'NoneType' object has no attribute 'x'"))
    with pytest.raises(AttributeError):
        make_client(transport).get_json_object("Get project", "/projects/1")


# --- 8. Pagination safety --------------------------------------------------------


def _link_header(
    next_url: str | None, base_url: str = "https://gitlab.example.com/api/v4"
) -> dict[str, str]:
    if next_url is None:
        return {}
    return {"Link": f'<{next_url}>; rel="next", <{base_url}/x?page=99>; rel="last"'}


def test_pagination_single_page() -> None:
    transport = FakeTransport().queue(json_response(200, [1, 2, 3], _link_header(None)))
    result = make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    assert result == [1, 2, 3]
    assert len(transport.calls) == 1


def test_pagination_requests_per_page_100_by_default() -> None:
    transport = FakeTransport().queue(json_response(200, [], _link_header(None)))
    make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    assert "per_page=100" in str(transport.calls[0]["url"])


# --- 8b. per_page validation ----------------------------------------------------


@pytest.mark.parametrize("value", [1, 100])
def test_get_json_list_accepts_per_page_boundary_values(value: int) -> None:
    transport = FakeTransport().queue(json_response(200, [], _link_header(None)))
    make_client(transport).get_json_list(
        "List branches", "/projects/1/protected_branches", per_page=value
    )
    assert f"per_page={value}" in str(transport.calls[0]["url"])


@pytest.mark.parametrize("value", [0, -1, 101, 1000, True, False])
def test_get_json_list_rejects_invalid_per_page(value: object) -> None:
    transport = FakeTransport()
    with pytest.raises(GitLabClientError):
        make_client(transport).get_json_list(
            "List branches", "/projects/1/protected_branches", per_page=value
        )  # type: ignore[arg-type]
    # Rejected before any request was ever sent.
    assert transport.calls == []


# --- 8c. per_page query-string construction -------------------------------------


def test_get_json_list_preserves_existing_query_parameters() -> None:
    transport = FakeTransport().queue(json_response(200, [], _link_header(None)))
    make_client(transport).get_json_list("List jobs", "/projects/1/jobs?scope=success", per_page=50)
    url = str(transport.calls[0]["url"])
    assert "scope=success" in url
    assert "per_page=50" in url
    assert url.count("?") == 1


def test_get_json_list_does_not_duplicate_per_page_already_in_path() -> None:
    transport = FakeTransport().queue(json_response(200, [], _link_header(None)))
    make_client(transport).get_json_list("List jobs", "/projects/1/jobs?per_page=25", per_page=100)
    url = str(transport.calls[0]["url"])
    assert url.count("per_page=") == 1
    assert "per_page=25" in url
    assert url.count("?") == 1


# --- 8d. Validation of an existing per_page query parameter --------------------
#
# `_validate_per_page()` validates the `per_page` *method argument*, but a
# `per_page` embedded directly in `path`'s query string bypassed it entirely.
# These tests exercise `_with_per_page()`'s own validation of that case.


@pytest.mark.parametrize("value", [1, 100])
def test_get_json_list_accepts_and_preserves_existing_per_page_boundary_values(
    value: int,
) -> None:
    transport = FakeTransport().queue(json_response(200, [], _link_header(None)))
    make_client(transport).get_json_list(
        "List jobs", f"/projects/1/jobs?per_page={value}", per_page=100
    )
    url = str(transport.calls[0]["url"])
    assert url.count("per_page=") == 1
    assert f"per_page={value}" in url


@pytest.mark.parametrize(
    "path",
    [
        "/projects/1/jobs?per_page=0",
        "/projects/1/jobs?per_page=101",
        "/projects/1/jobs?per_page=",
        "/projects/1/jobs?per_page=abc",
        "/projects/1/jobs?per_page=-5",
    ],
)
def test_get_json_list_rejects_invalid_existing_per_page(path: str) -> None:
    transport = FakeTransport()
    with pytest.raises(GitLabClientError):
        make_client(transport).get_json_list("List jobs", path, per_page=100)
    # Rejected before any request was ever sent.
    assert transport.calls == []


def test_get_json_list_rejects_duplicated_existing_per_page() -> None:
    transport = FakeTransport()
    with pytest.raises(GitLabClientError):
        make_client(transport).get_json_list(
            "List jobs", "/projects/1/jobs?per_page=25&per_page=50", per_page=100
        )
    assert transport.calls == []


def test_get_json_list_preserves_unrelated_query_parameters_alongside_existing_per_page() -> None:
    transport = FakeTransport().queue(json_response(200, [], _link_header(None)))
    make_client(transport).get_json_list(
        "List jobs", "/projects/1/jobs?scope=success&per_page=42", per_page=100
    )
    url = str(transport.calls[0]["url"])
    assert "scope=success" in url
    assert "per_page=42" in url
    assert url.count("per_page=") == 1
    assert url.count("?") == 1


def test_pagination_multiple_pages_combined_in_order() -> None:
    base = "https://gitlab.example.com/api/v4/projects/1/protected_branches"
    page2_url = f"{base}?per_page=100&page=2"
    page3_url = f"{base}?per_page=100&page=3"
    transport = (
        FakeTransport()
        .queue(json_response(200, [1, 2], _link_header(page2_url)))
        .queue(json_response(200, [3, 4], _link_header(page3_url)))
        .queue(json_response(200, [5], _link_header(None)))
    )
    result = make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    assert result == [1, 2, 3, 4, 5]
    assert len(transport.calls) == 3


def test_pagination_empty_list() -> None:
    transport = FakeTransport().queue(json_response(200, [], _link_header(None)))
    result = make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    assert result == []


def test_pagination_succeeds_at_exactly_max_pages_boundary() -> None:
    base = "https://gitlab.example.com/api/v4/projects/1/protected_branches"
    transport = FakeTransport()
    for i in range(2, 4):  # pages 1, 2, 3 -> max_pages=3
        transport.queue(json_response(200, [i - 1], _link_header(f"{base}?per_page=100&page={i}")))
    transport.queue(json_response(200, [3], _link_header(None)))

    result = make_client(transport, max_pages=3).get_json_list(
        "List branches", "/projects/1/protected_branches"
    )
    assert result == [1, 2, 3]
    assert len(transport.calls) == 3


def test_pagination_fails_past_max_pages_boundary() -> None:
    base = "https://gitlab.example.com/api/v4/projects/1/protected_branches"
    transport = FakeTransport()
    for i in range(2, 5):  # pages 1, 2, 3, 4 -> exceeds max_pages=3
        transport.queue(json_response(200, [i - 1], _link_header(f"{base}?per_page=100&page={i}")))
    transport.queue(json_response(200, [4], _link_header(None)))

    with pytest.raises(GitLabClientError, match="maximum of 3 pages"):
        make_client(transport, max_pages=3).get_json_list(
            "List branches", "/projects/1/protected_branches"
        )


def test_pagination_loop_is_detected() -> None:
    base = "https://gitlab.example.com/api/v4/projects/1/protected_branches"
    looping_url = f"{base}?per_page=100&page=2"
    transport = (
        FakeTransport()
        .queue(json_response(200, [1], _link_header(looping_url)))
        .queue(
            json_response(200, [2], _link_header(f"{base}?per_page=100"))
        )  # loops back to page 1's URL
    )
    with pytest.raises(GitLabClientError, match="pagination loop"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")


def test_pagination_rejects_cross_origin_next_link() -> None:
    transport = FakeTransport().queue(
        json_response(200, [1], _link_header("https://evil.example.com/api/v4/x?page=2"))
    )
    with pytest.raises(GitLabClientError, match="cross-origin"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")


def test_pagination_rejects_cross_origin_next_link_never_sends_token_there() -> None:
    transport = FakeTransport().queue(
        json_response(200, [1], _link_header("https://evil.example.com/api/v4/x?page=2"))
    )
    with pytest.raises(GitLabClientError):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    # Only the first, legitimate request was ever made -- the cross-origin
    # link was rejected before a second request (and the token) could be sent.
    assert len(transport.calls) == 1


def test_pagination_rejects_different_port_as_cross_origin() -> None:
    transport = FakeTransport().queue(
        json_response(200, [1], _link_header("https://gitlab.example.com:8443/api/v4/x?page=2"))
    )
    with pytest.raises(GitLabClientError, match="cross-origin"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")


def test_pagination_rejects_next_link_outside_api_path_prefix() -> None:
    transport = FakeTransport().queue(
        json_response(200, [1], _link_header("https://gitlab.example.com/not-the-api/x?page=2"))
    )
    with pytest.raises(GitLabClientError, match="cross-origin"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")


# --- 8a. Pagination next-link path-boundary regression tests -------------------
#
# `str.startswith(trusted_path)` is not a path-boundary check: it would wrongly
# accept "/api/v40/..." (a *longer sibling* path, not a sub-path) and
# "/api/v4evil/..." on the strength of shared leading characters alone, and it
# would not catch a "/api/v4/../outside" (or its percent-encoded equivalent)
# traversal segment that escapes the trusted path even though the literal
# string starts with it. Each rejection case below also asserts that only the
# initial legitimate request was ever made -- the malicious link must be
# rejected before a second, token-bearing request is sent.


def test_pagination_rejects_next_link_with_longer_sibling_path_prefix() -> None:
    transport = FakeTransport().queue(
        json_response(200, [1], _link_header("https://gitlab.example.com/api/v40/outside"))
    )
    with pytest.raises(GitLabClientError, match="cross-origin"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    assert len(transport.calls) == 1


def test_pagination_rejects_next_link_with_similarly_named_path_prefix() -> None:
    transport = FakeTransport().queue(
        json_response(200, [1], _link_header("https://gitlab.example.com/api/v4evil/outside"))
    )
    with pytest.raises(GitLabClientError, match="cross-origin"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    assert len(transport.calls) == 1


def test_pagination_rejects_next_link_with_raw_traversal_segment() -> None:
    transport = FakeTransport().queue(
        json_response(200, [1], _link_header("https://gitlab.example.com/api/v4/../outside"))
    )
    with pytest.raises(GitLabClientError, match="cross-origin"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    assert len(transport.calls) == 1


def test_pagination_rejects_next_link_with_percent_encoded_traversal_segment() -> None:
    transport = FakeTransport().queue(
        json_response(200, [1], _link_header("https://gitlab.example.com/api/v4/%2e%2e/outside"))
    )
    with pytest.raises(GitLabClientError, match="cross-origin"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    assert len(transport.calls) == 1


def test_pagination_rejects_next_link_with_malformed_percent_escape() -> None:
    # "%2Z" is not a valid percent-escape (unquote(..., errors="strict")
    # leaves it unchanged rather than raising), so it must be caught
    # explicitly rather than slipping past the traversal check unnoticed.
    transport = FakeTransport().queue(
        json_response(
            200, [1], _link_header("https://gitlab.example.com/api/v4/%2Z/outside?page=2")
        )
    )
    with pytest.raises(GitLabClientError, match="cross-origin"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    # Only the initial legitimate request occurred -- no second, token-bearing
    # request was sent to the malformed link.
    assert len(transport.calls) == 1


def test_pagination_accepts_a_valid_next_link_under_the_trusted_api_path() -> None:
    base = "https://gitlab.example.com/api/v4/projects/1/protected_branches"
    transport = (
        FakeTransport()
        .queue(json_response(200, [1], _link_header(f"{base}?per_page=100&page=2")))
        .queue(json_response(200, [2], _link_header(None)))
    )
    result = make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    assert result == [1, 2]
    assert len(transport.calls) == 2


def test_pagination_rejects_malformed_next_link() -> None:
    transport = FakeTransport().queue(
        json_response(200, [1], _link_header("https://gitlab.example.com:abc/x"))
    )
    with pytest.raises(GitLabClientError, match="malformed"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")


def test_pagination_stops_normally_with_no_link_header() -> None:
    transport = FakeTransport().queue(FakeResponse(200, b"[1, 2]"))  # no Link header at all
    result = make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")
    assert result == [1, 2]


def test_pagination_later_page_returning_non_list_fails() -> None:
    base = "https://gitlab.example.com/api/v4/projects/1/protected_branches"
    next_url = f"{base}?per_page=100&page=2"
    transport = (
        FakeTransport()
        .queue(json_response(200, [1], _link_header(next_url)))
        .queue(json_response(200, {"unexpected": "object"}))
    )
    with pytest.raises(GitLabClientError, match="expected a JSON array"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")


def test_pagination_later_page_returning_http_error_fails() -> None:
    base = "https://gitlab.example.com/api/v4/projects/1/protected_branches"
    next_url = f"{base}?per_page=100&page=2"
    transport = (
        FakeTransport()
        .queue(json_response(200, [1], _link_header(next_url)))
        .queue(FakeResponse(500, b"{}"))
    )
    with pytest.raises(GitLabClientError, match="GitLab server error"):
        make_client(transport).get_json_list("List branches", "/projects/1/protected_branches")


# --- Guard: FakeTransport itself never touches the real network ----------------


def test_fake_transport_is_not_urllib3_poolmanager() -> None:
    transport = FakeTransport()
    assert not isinstance(transport, urllib3.PoolManager)


def test_default_transport_falls_back_to_poolmanager_but_is_guarded() -> None:
    """Confirms the guard fixture actually intercepts the default transport path."""
    client = GitLabClient("https://gitlab.example.com", SYNTHETIC_TOKEN)
    with pytest.raises(AssertionError, match="real urllib3.PoolManager"):
        client.get_json_object("Get project", "/projects/1")
