"""Read-only GitLab HTTP client foundation (v0.2.0 Phase 2A).

This module provides only the transport-level building blocks needed by a
future GitLab collector: token retrieval, base-URL validation, project
identifier canonicalization, and an injectable, GET-only HTTP client with
sanitized error handling and safe pagination. It does not fetch any GitLab
project data, does not know about protected branches, CI configuration, or
any of the eleven planned GitLab checks, and is not wired into the CLI yet
-- see `docs/milestones/v0.2.0-gitlab-audit.md`.

Security posture (see CLAUDE.md's GitLab invariants):
- GET requests only; there is no method parameter that could later be set to
  a write verb by mistake.
- The access token is read only from the `CLOUDOPS_GUARD_GITLAB_TOKEN`
  environment variable, sent only in the `PRIVATE-TOKEN` header, and never
  appears in a URL, query string, log message, or exception.
- Errors are sanitized: messages carry only an operation label and, where
  applicable, an HTTP status code -- never raw response bodies, headers,
  GitLab JSON error content, or full exception text from the transport.
- Redirects and automatic retries are disabled; TLS verification is left at
  urllib3's secure-by-default behavior (verified certificates), with no
  option to disable it.
"""

from __future__ import annotations

import json
import os
import re
import ssl
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

import urllib3
import urllib3.exceptions

GITLAB_TOKEN_ENV_VAR = "CLOUDOPS_GUARD_GITLAB_TOKEN"

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_PER_PAGE = 100
DEFAULT_MAX_PAGES = 100

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_API_PATH_SUFFIX = "/api/v4"

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_MALFORMED_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_NUMERIC_ID_RE = re.compile(r"-?\d+")
_LINK_ENTRY_RE = re.compile(r'<([^>]*)>\s*;\s*rel="?([\w-]+)"?')


class GitLabClientError(Exception):
    """Raised for GitLab configuration, transport, or API failures.

    Messages never include token values, authentication headers, raw
    response bodies, raw GitLab JSON error content, job scripts, CI
    variables, or full exception representations from the underlying
    transport -- only a fixed, sanitized explanation and, where useful, an
    operation label and/or numeric HTTP status.
    """


# --- 1. Token retrieval -------------------------------------------------------


def load_gitlab_token(env: Mapping[str, str] | None = None) -> str:
    """Read the GitLab access token from `CLOUDOPS_GUARD_GITLAB_TOKEN`.

    `env` is injectable for deterministic tests; it defaults to the real
    process environment. Returns the token exactly as read (never stripped
    or otherwise transformed) once confirmed non-empty. Never reads a config
    file, a CLI option, or any other environment variable name.
    """
    environment = env if env is not None else os.environ
    value = environment.get(GITLAB_TOKEN_ENV_VAR)
    if value is None:
        raise GitLabClientError(
            f"{GITLAB_TOKEN_ENV_VAR} is not set. Set it to a GitLab access token "
            f"with the read_api scope."
        )
    if not value.strip():
        raise GitLabClientError(f"{GITLAB_TOKEN_ENV_VAR} is set but empty.")
    return value


# --- 2. GitLab base-URL validation and normalization --------------------------


def normalize_gitlab_base_url(url: str) -> str:
    """Validate and normalize a user-supplied GitLab base URL.

    Returns the normalized base URL with no trailing slash and no `/api/v4`
    suffix (e.g. "https://example.com/gitlab"). Raises `GitLabClientError`
    for anything not safe to use as a trusted API origin. Never performs DNS
    resolution; this is syntactic validation only.
    """
    if not isinstance(url, str) or not url.strip():
        raise GitLabClientError("GitLab base URL must not be empty.")
    # Checked on the raw string first: urlsplit silently strips some control
    # characters (e.g. \n, \r, \t) before parsing, so checking post-parse
    # would miss them.
    if _CONTROL_CHAR_RE.search(url):
        raise GitLabClientError("GitLab base URL contains control characters.")

    parsed = urlsplit(url.strip())

    if not parsed.scheme or not parsed.netloc:
        raise GitLabClientError(
            "GitLab base URL must be an absolute URL with a scheme and hostname."
        )
    if parsed.scheme not in ("http", "https"):
        raise GitLabClientError("GitLab base URL must use http or https.")
    if parsed.username or parsed.password:
        raise GitLabClientError("GitLab base URL must not include embedded credentials.")
    if parsed.query:
        raise GitLabClientError("GitLab base URL must not include a query string.")
    if parsed.fragment:
        raise GitLabClientError("GitLab base URL must not include a fragment.")

    try:
        hostname = parsed.hostname
        _ = parsed.port  # accessed only to trigger its malformed-port validation
    except ValueError:
        raise GitLabClientError("GitLab base URL has a malformed host or port.") from None
    if not hostname:
        raise GitLabClientError("GitLab base URL must include a hostname.")

    is_loopback = hostname in _LOOPBACK_HOSTS
    if parsed.scheme == "http" and not is_loopback:
        raise GitLabClientError(
            "GitLab base URL must use https, except for explicit loopback hosts "
            "(localhost, 127.0.0.1, ::1)."
        )

    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def gitlab_api_base_url(url: str) -> str:
    """Return the validated, normalized REST API v4 base URL for `url`.

    Appends `/api/v4` to the normalized base from `normalize_gitlab_base_url`
    -- but only if it is not already present: this is idempotent, so passing
    either "https://gitlab.example.com" or "https://gitlab.example.com/api/v4"
    (with or without a trailing slash, which normalization strips) both
    produce "https://gitlab.example.com/api/v4", never a duplicated suffix.
    A similar-looking path such as "/api/v40" is not mistaken for the
    suffix, since the check is an exact trailing-segment match.
    """
    normalized = normalize_gitlab_base_url(url)
    if normalized.endswith(_API_PATH_SUFFIX):
        return normalized
    return normalized + _API_PATH_SUFFIX


# --- 3. Project identifier canonicalization -----------------------------------


def canonicalize_gitlab_project(identifier: str | int) -> str:
    """Return the canonical GitLab `:id` path segment for a project identifier.

    Accepts a positive numeric ID (`int` or a numeric string), a raw path
    such as "group/subgroup/project", or an already percent-encoded path
    such as "group%2Fsubgroup%2Fproject". Numeric input is returned in
    canonical decimal form. Path input is decoded exactly once, validated,
    and re-encoded canonically (never double-encoded). Never contacts
    GitLab to resolve the project.

    `bool` is rejected even though Python treats it as an `int` subclass:
    `True`/`False` are not meaningful project identifiers.
    """
    if isinstance(identifier, bool):
        raise GitLabClientError("GitLab project identifier must not be a boolean.")
    if isinstance(identifier, int):
        return _canonicalize_numeric_id(identifier)
    if not isinstance(identifier, str):
        raise GitLabClientError("GitLab project identifier must be a string or integer.")

    stripped = identifier.strip()
    if not stripped:
        raise GitLabClientError("GitLab project identifier must not be empty.")

    if _NUMERIC_ID_RE.fullmatch(stripped):
        return _canonicalize_numeric_id(int(stripped))

    return _canonicalize_project_path(stripped)


def _canonicalize_numeric_id(value: int) -> str:
    if value <= 0:
        raise GitLabClientError("GitLab numeric project ID must be a positive integer.")
    return str(value)


def _canonicalize_project_path(raw: str) -> str:
    if "://" in raw:
        raise GitLabClientError("GitLab project identifier must not be a full URL.")
    if _CONTROL_CHAR_RE.search(raw):
        raise GitLabClientError("GitLab project path contains control characters.")
    if _MALFORMED_PERCENT_RE.search(raw):
        raise GitLabClientError("GitLab project path contains a malformed percent-escape.")

    try:
        # Decoded exactly once: an already-encoded input becomes its raw
        # path here; a raw input is unaffected (unquote of a string with no
        # "%" is a no-op). Re-encoding below is therefore never applied
        # twice to the same character.
        decoded = unquote(raw, errors="strict")
    except UnicodeDecodeError:
        raise GitLabClientError(
            "GitLab project path contains an invalid percent-encoded byte sequence."
        ) from None

    if "://" in decoded:
        raise GitLabClientError("GitLab project identifier must not be a full URL.")
    if _CONTROL_CHAR_RE.search(decoded):
        raise GitLabClientError("GitLab project path contains control characters.")
    if "\\" in decoded:
        raise GitLabClientError("GitLab project path must not contain a backslash.")
    if decoded.startswith("/") or decoded.endswith("/"):
        raise GitLabClientError("GitLab project path must not start or end with '/'.")

    segments = decoded.split("/")
    if any(not segment for segment in segments):
        raise GitLabClientError("GitLab project path must not contain empty segments.")
    if any(segment in (".", "..") for segment in segments):
        raise GitLabClientError("GitLab project path must not contain '.' or '..' segments.")

    # safe="" percent-encodes "/" too: GitLab's :id path segment requires the
    # entire path -- including internal separators -- to be encoded as one
    # segment (this is what makes nested subgroups addressable at all).
    return quote(decoded, safe="")


# --- 4-7. Injectable, read-only HTTP client -----------------------------------


_MAX_PER_PAGE = 100
_DIGITS_RE = re.compile(r"[0-9]+")


def _validate_client_token(token: object) -> str:
    """Validate a token passed directly to `GitLabClient`, not via

    `load_gitlab_token`. Never includes the rejected value in the raised
    error, exactly like `load_gitlab_token`'s own validation.
    """
    if not isinstance(token, str):
        raise GitLabClientError("GitLab token must be a non-empty string.")
    if not token.strip():
        raise GitLabClientError("GitLab token must not be empty or whitespace-only.")
    return token


def _validate_max_pages(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GitLabClientError("max_pages must be a positive, non-boolean integer.")


def _validate_per_page(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= _MAX_PER_PAGE):
        raise GitLabClientError(
            f"per_page must be a non-boolean integer between 1 and {_MAX_PER_PAGE}."
        )


class TransportResponse(Protocol):
    status: int
    data: bytes
    headers: Mapping[str, str]


class Transport(Protocol):
    """Structural type satisfied by `urllib3.PoolManager` without wrapping.

    A fake implementation for tests only needs to satisfy this shape; it
    never has to touch `urllib3` or the network.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: Any,
        redirect: bool,
        retries: Any,
    ) -> TransportResponse: ...


class GitLabClient:
    """Injectable, GET-only GitLab REST API v4 client.

    Only ever issues GET requests -- there is no method parameter that could
    later be set to a write verb by mistake. The token is sent only in the
    `PRIVATE-TOKEN` header, never in a URL or query string. Redirects and
    automatic retries are disabled; TLS verification uses urllib3's
    secure-by-default behavior with no option to disable it.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: Transport | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> None:
        _validate_max_pages(max_pages)
        self._api_base_url = gitlab_api_base_url(base_url)
        self._token = _validate_client_token(token)
        self._transport: Transport = transport if transport is not None else urllib3.PoolManager()
        self._timeout = urllib3.Timeout(connect=connect_timeout, read=read_timeout)
        self._max_pages = max_pages

    @property
    def api_base_url(self) -> str:
        """The validated, normalized `.../api/v4` base this client requests against."""
        return self._api_base_url

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self._token, "Accept": "application/json"}

    def _request(self, operation: str, url: str) -> tuple[int, bytes, Mapping[str, str]]:
        try:
            response = self._transport.request(
                "GET",
                url,
                headers=self._headers(),
                timeout=self._timeout,
                redirect=False,
                retries=False,
            )
        except urllib3.exceptions.NameResolutionError:
            raise GitLabClientError(f"{operation} failed: DNS resolution failed.") from None
        except urllib3.exceptions.NewConnectionError:
            raise GitLabClientError(f"{operation} failed: connection failed.") from None
        except urllib3.exceptions.ConnectTimeoutError:
            raise GitLabClientError(f"{operation} failed: connect timeout.") from None
        except urllib3.exceptions.ReadTimeoutError:
            raise GitLabClientError(f"{operation} failed: read timeout.") from None
        except (urllib3.exceptions.SSLError, ssl.SSLError):
            raise GitLabClientError(f"{operation} failed: TLS/certificate error.") from None
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            raise GitLabClientError(
                f"{operation} failed: transport error ({type(exc).__name__})."
            ) from None
        return response.status, response.data, response.headers

    @staticmethod
    def _raise_for_status(operation: str, status: int) -> None:
        if 200 <= status < 300:
            return
        if 300 <= status < 400:
            raise GitLabClientError(f"{operation} failed: unexpected redirect (HTTP {status}).")
        if status == 401:
            raise GitLabClientError(f"{operation} failed: authentication failed (HTTP 401).")
        if status == 403:
            raise GitLabClientError(f"{operation} failed: insufficient permissions (HTTP 403).")
        if status == 404:
            raise GitLabClientError(f"{operation} failed: not found (HTTP 404).")
        if status == 429:
            raise GitLabClientError(f"{operation} failed: rate limited (HTTP 429).")
        if 400 <= status < 500:
            raise GitLabClientError(f"{operation} failed (HTTP {status}).")
        if status >= 500:
            raise GitLabClientError(f"{operation} failed: GitLab server error (HTTP {status}).")
        raise GitLabClientError(f"{operation} failed: unexpected HTTP status {status}.")

    @staticmethod
    def _decode_json(operation: str, data: bytes) -> Any:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise GitLabClientError(f"{operation} failed: response was not valid UTF-8.") from None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise GitLabClientError(f"{operation} failed: response was not valid JSON.") from None

    def get_json_object(self, operation: str, path: str) -> dict[str, Any]:
        """GET `path` (relative to the API base) and return a JSON object."""
        url = f"{self._api_base_url}{path}"
        status, data, _headers = self._request(operation, url)
        self._raise_for_status(operation, status)
        payload = self._decode_json(operation, data)
        if not isinstance(payload, dict):
            raise GitLabClientError(f"{operation} failed: expected a JSON object.")
        return payload

    @staticmethod
    def _with_per_page(path: str, per_page: int) -> str:
        """Return `path` with a `per_page` query parameter, added via proper

        query construction rather than naive string concatenation: an
        existing query string is preserved, and exactly one "?" is ever
        produced. If `path` already specifies `per_page` internally, that
        value is validated -- it must appear exactly once and be a base-10
        integer between 1 and `_MAX_PER_PAGE` -- and preserved as-is rather
        than overwritten by the `per_page` method argument, which is only
        appended when the path does not already specify one. An invalid
        existing value (empty, non-numeric, out of range, or duplicated)
        raises `GitLabClientError` here, before any request is made.
        """
        split_path = urlsplit(path)
        query_pairs = parse_qsl(split_path.query, keep_blank_values=True)
        existing = [value for key, value in query_pairs if key == "per_page"]
        if len(existing) > 1:
            raise GitLabClientError("path must not specify per_page more than once.")
        if existing:
            value = existing[0]
            if not _DIGITS_RE.fullmatch(value) or not (1 <= int(value) <= _MAX_PER_PAGE):
                raise GitLabClientError(
                    f"path's per_page value must be a base-10 integer between 1 and "
                    f"{_MAX_PER_PAGE}."
                )
        else:
            query_pairs.append(("per_page", str(per_page)))
        new_query = urlencode(query_pairs)
        return urlunsplit(("", "", split_path.path, new_query, ""))

    def get_json_list(
        self, operation: str, path: str, *, per_page: int = DEFAULT_PER_PAGE
    ) -> list[Any]:
        """GET `path` (relative to the API base), following pagination safely.

        Requests `per_page` items per page (default 100; must be an integer
        from 1 to 100), follows GitLab's `Link: rel="next"` header,
        validates every next-page URL stays on the configured
        scheme/host/port and under the API path prefix (rejecting any
        traversal segment, raw or percent-decoded), detects repeated-URL
        pagination loops, and stops after `max_pages` (constructor setting)
        pages. Results are combined in the order returned.

        If `path` already includes a query string, its parameters are
        preserved; `per_page` is added to them unless already present,
        never producing a second "?" or a duplicated `per_page`. An
        already-present `per_page` in `path` must be a single, base-10
        integer between 1 and 100 -- an empty, non-numeric, out-of-range,
        or duplicated value raises `GitLabClientError` before any request
        is made.
        """
        _validate_per_page(per_page)
        url: str | None = f"{self._api_base_url}{self._with_per_page(path, per_page)}"
        results: list[Any] = []
        seen_urls: set[str] = set()
        pages = 0

        while url is not None:
            if url in seen_urls:
                raise GitLabClientError(f"{operation} failed: pagination loop detected.")
            seen_urls.add(url)

            pages += 1
            if pages > self._max_pages:
                raise GitLabClientError(
                    f"{operation} failed: exceeded the maximum of {self._max_pages} pages."
                )

            status, data, headers = self._request(operation, url)
            self._raise_for_status(operation, status)
            payload = self._decode_json(operation, data)
            if not isinstance(payload, list):
                raise GitLabClientError(f"{operation} failed: expected a JSON array.")
            results.extend(payload)

            url = self._next_page_url(operation, headers)

        return results

    def _next_page_url(self, operation: str, headers: Mapping[str, str]) -> str | None:
        link_header = headers.get("Link") or headers.get("link")
        if not link_header:
            return None
        next_url = _parse_next_link(link_header)
        if next_url is None:
            return None
        return self._validate_same_origin_url(operation, next_url)

    def _validate_same_origin_url(self, operation: str, next_url: str) -> str:
        # Deliberately not urljoin(base, next_url): if next_url were an
        # absolute URL, urljoin would silently replace the trusted origin
        # instead of raising, which would let a malicious/misbehaving
        # response redirect our token to a different host.
        if _CONTROL_CHAR_RE.search(next_url):
            raise GitLabClientError(f"{operation} failed: malformed pagination link.")

        trusted = urlsplit(self._api_base_url)
        candidate = urlsplit(next_url)
        try:
            candidate_host = candidate.hostname
            candidate_port = candidate.port
        except ValueError:
            raise GitLabClientError(f"{operation} failed: malformed pagination link.") from None

        if (
            candidate.scheme != trusted.scheme
            or candidate_host != trusted.hostname
            or candidate_port != trusted.port
            or candidate.username
            or candidate.password
            or not _is_under_trusted_path(candidate.path, trusted.path)
            or _path_has_traversal_segment(candidate.path)
        ):
            raise GitLabClientError(
                f"{operation} failed: pagination link was cross-origin or malformed."
            )
        return next_url


def _is_under_trusted_path(candidate_path: str, trusted_path: str) -> bool:
    """True if `candidate_path` is exactly `trusted_path`, or a real sub-path of it.

    A plain `str.startswith(trusted_path)` is not boundary-aware: it would
    wrongly accept "/api/v40/outside" or "/api/v4evil/outside" as being
    "under" "/api/v4", since both start with the literal characters
    "/api/v4". Requiring an exact match or a "/"-bounded prefix closes that.
    """
    return candidate_path == trusted_path or candidate_path.startswith(trusted_path + "/")


def _path_has_traversal_segment(path: str) -> bool:
    """True if `path` contains a "." or ".." segment, checked both raw and

    percent-decoded (e.g. "/api/v4/%2e%2e/outside" decodes to
    "/api/v4/../outside"). A malformed percent-encoding is itself treated
    as unsafe -- checked explicitly via `_MALFORMED_PERCENT_RE` before
    decoding, since `unquote(..., errors="strict")` only raises for a
    complete escape that decodes to an invalid UTF-8 byte sequence; an
    incomplete/invalid escape such as "%2Z" is left unchanged rather than
    rejected. Checked in addition to (not instead of) the trusted-path
    prefix check, since a traversal segment could otherwise still resolve
    outside the trusted path even when the literal string starts with it.
    """
    if _MALFORMED_PERCENT_RE.search(path):
        return True
    try:
        decoded = unquote(path, errors="strict")
    except UnicodeDecodeError:
        return True
    for candidate_form in (path, decoded):
        if any(segment in (".", "..") for segment in candidate_form.split("/")):
            return True
    return False


def _parse_next_link(link_header: str) -> str | None:
    """Extract the `rel="next"` URL from an RFC 8288 `Link` header, if any."""
    for match in _LINK_ENTRY_RE.finditer(link_header):
        url, rel = match.group(1), match.group(2)
        if rel == "next":
            return url
    return None
