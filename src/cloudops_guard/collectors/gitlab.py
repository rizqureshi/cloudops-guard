"""Read-only GitLab HTTP client and normalized project collector (v0.2.0 Phase 2A/2B).

Phase 2A provides the transport-level building blocks: token retrieval,
base-URL validation, project identifier canonicalization, and an
injectable, GET-only HTTP client with sanitized error handling and safe
pagination. Phase 2B (`GitLabCollector`) uses only that client to produce a
normalized `GitLabProjectSnapshot` (instance metadata, allowlisted project
settings, and protected-branch rules) for the non-CI-Lint checks. Neither
phase implements any of the eleven planned GitLab checks, CI configuration
inspection, or CLI wiring -- see `docs/milestones/v0.2.0-gitlab-audit.md`.

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
- `GitLabCollector` issues only three GET operations: `/metadata`,
  `/projects/:id`, and `/projects/:id/protected_branches` (paginated). The
  `/projects/:id` response may include unrelated sensitive fields GitLab
  provides automatically (e.g. `runners_token`) that cannot be filtered at
  the API level -- see "Approved unavoidable-field exception: GET
  /projects/:id" in the milestone doc. Only an explicit allowlist of
  normalized fields is ever retained; the raw response is never stored,
  logged, printed, cached, reported, returned, or included in an exception.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import ssl
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

import urllib3
import urllib3.exceptions

from cloudops_guard.models import (
    GitLabProjectSettings,
    GitLabProjectSnapshot,
    GitLabProtectedBranchRule,
)

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

    Returns the normalized *instance* base URL with no trailing slash and no
    `/api/v4` suffix (e.g. "https://example.com/gitlab"), regardless of
    whether `url` was given with or without that suffix -- both
    "https://gitlab.com" and "https://gitlab.com/api/v4" normalize to
    "https://gitlab.com", and both "https://example.com/gitlab" and
    "https://example.com/gitlab/api/v4" normalize to
    "https://example.com/gitlab". A similar-looking path such as
    "/api/v40" is not mistaken for the suffix (exact trailing-segment
    match only). Raises `GitLabClientError` for anything not safe to use as
    a trusted API origin. Never performs DNS resolution; this is syntactic
    validation only.
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
    if path.endswith(_API_PATH_SUFFIX):
        path = path[: -len(_API_PATH_SUFFIX)]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def gitlab_api_base_url(url: str) -> str:
    """Return the validated, normalized REST API v4 base URL for `url`.

    Appends `/api/v4` to the normalized instance base from
    `normalize_gitlab_base_url`, which always strips a pre-existing
    `/api/v4` suffix from `url` first -- so passing either
    "https://gitlab.example.com" or "https://gitlab.example.com/api/v4"
    (with or without a trailing slash) both produce exactly
    "https://gitlab.example.com/api/v4", never a duplicated suffix. The
    `endswith` guard below is defense in depth in case `normalize_gitlab_base_url`
    is ever changed to stop stripping the suffix itself.
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
        self._instance_base_url = normalize_gitlab_base_url(base_url)
        self._api_base_url = gitlab_api_base_url(base_url)
        self._token = _validate_client_token(token)
        self._transport: Transport = transport if transport is not None else urllib3.PoolManager()
        self._timeout = urllib3.Timeout(connect=connect_timeout, read=read_timeout)
        self._max_pages = max_pages

    @property
    def api_base_url(self) -> str:
        """The validated, normalized `.../api/v4` base this client requests against."""
        return self._api_base_url

    @property
    def instance_base_url(self) -> str:
        """The validated, normalized GitLab instance base URL (no `/api/v4` suffix).

        Suitable for recording on a normalized snapshot as the audited
        instance's identity, e.g. "https://gitlab.example.com" or
        "https://example.com/gitlab" for a self-managed path-prefixed
        install -- never with a trailing slash or a duplicated `/api/v4`.
        """
        return self._instance_base_url

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


# --- Phase 2B: normalized instance/project/protected-branch collector -----------
#
# Uses only the injectable `GitLabClient` above -- no independent transport,
# no second token. Issues exactly three GET operations (see module
# docstring). Every function below reads only an explicit allowlist of
# fields out of a raw response dict; the dict itself is never stored,
# returned, logged, or included in an exception -- it goes out of scope as
# soon as normalization returns or raises.

_ALLOWED_VISIBILITY = frozenset({"private", "internal", "public"})
_ALLOWED_OVERRIDE_ROLE = frozenset({"no_one_allowed", "owner", "maintainer", "developer"})
_ALLOWED_AUTO_CANCEL = frozenset({"enabled", "disabled"})
_ALLOWED_ROLE_LEVELS = frozenset({0, 30, 40, 60})
_SCOPE_IDENTIFIER_KEYS = ("user_id", "group_id", "deploy_key_id", "member_role_id")

_GITLAB_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.\d+)?(?:-[A-Za-z0-9]+)?$")
_MIN_SELF_MANAGED_VERSION = (18, 4)
_GITLAB_COM_HOSTNAMES = frozenset({"gitlab.com"})


def _is_gitlab_com(instance_base_url: str) -> bool:
    """True if `instance_base_url` is GitLab.com itself (not a self-managed instance).

    GitLab.com is treated as always current per the milestone's supported-
    versions boundary; a self-managed instance is still subject to the
    minimum-version check.
    """
    return urlsplit(instance_base_url).hostname in _GITLAB_COM_HOSTNAMES


def _normalize_metadata(payload: object, *, is_gitlab_com: bool) -> tuple[str, bool]:
    """Validate a `GET /metadata` response and return `(version, enterprise)`.

    Requires a non-empty `version` string in a recognizable
    `major.minor[.patch]` form, optionally with a GitLab suffix such as
    "-ee", and a real `enterprise` boolean. On a self-managed instance
    (i.e. not GitLab.com), rejects a version earlier than 18.4 with a
    fixed, sanitized error -- the raw version value is never reproduced in
    an exception. `revision`, KAS URLs, and any other metadata field are
    read never and retained never.
    """
    if not isinstance(payload, dict):
        raise GitLabClientError("GitLab metadata response was not a JSON object.")

    version = payload.get("version")
    if not isinstance(version, str) or not version:
        raise GitLabClientError("GitLab metadata response is missing a valid 'version' string.")
    match = _GITLAB_VERSION_RE.match(version)
    if match is None:
        raise GitLabClientError("GitLab metadata response has an unrecognized 'version' format.")

    enterprise = payload.get("enterprise")
    if not isinstance(enterprise, bool):
        raise GitLabClientError("GitLab metadata response is missing a boolean 'enterprise' field.")

    if not is_gitlab_com:
        major, minor = int(match.group(1)), int(match.group(2))
        if (major, minor) < _MIN_SELF_MANAGED_VERSION:
            raise GitLabClientError(
                "GitLab instance version is not supported for this audit "
                "(self-managed GitLab 18.4 or later is required)."
            )

    return version, enterprise


def _require_bool_field(payload: Mapping[str, Any], field: str) -> bool:
    if field not in payload:
        raise GitLabClientError(f"GitLab project response is missing required field '{field}'.")
    value = payload[field]
    if not isinstance(value, bool):
        raise GitLabClientError(f"GitLab project field '{field}' must be a boolean.")
    return value


def _require_nonempty_str_field(payload: Mapping[str, Any], field: str) -> str:
    if field not in payload:
        raise GitLabClientError(f"GitLab project response is missing required field '{field}'.")
    value = payload[field]
    if not isinstance(value, str) or not value:
        raise GitLabClientError(f"GitLab project field '{field}' must be a non-empty string.")
    return value


def _require_enum_field(payload: Mapping[str, Any], field: str, allowed: frozenset[str]) -> str:
    value = _require_nonempty_str_field(payload, field)
    if value not in allowed:
        raise GitLabClientError(f"GitLab project field '{field}' has an unrecognized value.")
    return value


def _require_positive_int_field(payload: Mapping[str, Any], field: str) -> int:
    if field not in payload:
        raise GitLabClientError(f"GitLab project response is missing required field '{field}'.")
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GitLabClientError(f"GitLab project field '{field}' must be a positive integer.")
    return value


def _require_optional_non_negative_int_field(payload: Mapping[str, Any], field: str) -> int | None:
    if field not in payload:
        raise GitLabClientError(f"GitLab project response is missing required field '{field}'.")
    value = payload[field]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GitLabClientError(
            f"GitLab project field '{field}' must be a non-negative integer or null."
        )
    return value


def _normalize_project_settings(payload: object) -> GitLabProjectSettings:
    """Validate and normalize a `GET /projects/:id` response.

    Reads only the explicit allowlist of fields `GitLabProjectSettings`
    needs -- every other field GitLab returns, including sensitive ones
    such as `runners_token`, `owner`, `import_url`, or repository clone
    URLs, is never read from `payload` at all. `payload` itself is never
    stored, returned, or included in an exception; it goes out of scope
    when this function returns. Every required field must be present and
    correctly typed (no silent coercion of `"false"`/`0`/`False`, and no
    truthiness-based presence check), or this raises a fixed,
    sanitized `GitLabClientError` -- never reproducing the offending raw
    value. `ci_default_git_depth` is the sole field allowed to be `null`;
    every other field must be present and non-null.
    """
    if not isinstance(payload, dict):
        raise GitLabClientError("GitLab project response was not a JSON object.")

    project_id = _require_positive_int_field(payload, "id")
    project_path = _require_nonempty_str_field(payload, "path_with_namespace")
    default_branch = _require_nonempty_str_field(payload, "default_branch")
    visibility = _require_enum_field(payload, "visibility", _ALLOWED_VISIBILITY)
    only_allow_merge_if_pipeline_succeeds = _require_bool_field(
        payload, "only_allow_merge_if_pipeline_succeeds"
    )
    public_jobs = _require_bool_field(payload, "public_jobs")
    ci_push_repository_for_job_token_allowed = _require_bool_field(
        payload, "ci_push_repository_for_job_token_allowed"
    )
    ci_pipeline_variables_minimum_override_role = _require_enum_field(
        payload, "ci_pipeline_variables_minimum_override_role", _ALLOWED_OVERRIDE_ROLE
    )
    auto_cancel_pending_pipelines = _require_enum_field(
        payload, "auto_cancel_pending_pipelines", _ALLOWED_AUTO_CANCEL
    )
    ci_default_git_depth = _require_optional_non_negative_int_field(payload, "ci_default_git_depth")
    build_timeout = _require_positive_int_field(payload, "build_timeout")

    return GitLabProjectSettings(
        project_id=project_id,
        project_path=project_path,
        default_branch=default_branch,
        visibility=visibility,
        only_allow_merge_if_pipeline_succeeds=only_allow_merge_if_pipeline_succeeds,
        public_jobs=public_jobs,
        ci_push_repository_for_job_token_allowed=ci_push_repository_for_job_token_allowed,
        ci_pipeline_variables_minimum_override_role=ci_pipeline_variables_minimum_override_role,
        auto_cancel_pending_pipelines=auto_cancel_pending_pipelines,
        ci_default_git_depth=ci_default_git_depth,
        build_timeout=build_timeout,
    )


def _normalize_protected_branch(payload: object) -> GitLabProtectedBranchRule:
    """Validate and normalize one `GET /projects/:id/protected_branches` entry.

    Requires a non-empty `name` and a boolean `allow_force_push`. `inherited`
    is optional (tier-dependent): absent normalizes to `None`; if the key is
    present at all, it must be a boolean. `push_access_levels` entries scoped
    to a specific user, group, deploy key, or custom role (any of
    `user_id`/`group_id`/`deploy_key_id`/`member_role_id` non-null) are
    outside v0.2.0's role-based scope and are ignored -- their identifiers
    and `access_level_description` are never read into any retained value.
    A role-based entry's `access_level` must be exactly one of the four
    documented levels (0, 30, 40, 60); retained levels are deduplicated and
    sorted deterministically. Any other field on the entry is ignored.
    """
    if not isinstance(payload, dict):
        raise GitLabClientError("GitLab protected branch entry was not a JSON object.")

    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise GitLabClientError("GitLab protected branch entry is missing a non-empty 'name'.")

    allow_force_push = payload.get("allow_force_push")
    if not isinstance(allow_force_push, bool):
        raise GitLabClientError(
            "GitLab protected branch entry is missing a boolean 'allow_force_push'."
        )

    inherited: bool | None = None
    if "inherited" in payload:
        inherited_value = payload["inherited"]
        if not isinstance(inherited_value, bool):
            raise GitLabClientError(
                "GitLab protected branch entry's 'inherited' field must be a boolean."
            )
        inherited = inherited_value

    raw_levels = payload.get("push_access_levels")
    if not isinstance(raw_levels, list):
        raise GitLabClientError(
            "GitLab protected branch entry is missing a 'push_access_levels' list."
        )

    role_levels: set[int] = set()
    for entry in raw_levels:
        if not isinstance(entry, dict):
            raise GitLabClientError(
                "GitLab protected branch entry has a malformed push_access_levels entry."
            )
        if any(entry.get(key) is not None for key in _SCOPE_IDENTIFIER_KEYS):
            # Scoped to a specific user/group/deploy-key/custom-role -- outside
            # v0.2.0's role-based evaluation. Ignored without retaining its
            # identifiers or access_level_description.
            continue
        access_level = entry.get("access_level")
        if isinstance(access_level, bool) or not isinstance(access_level, int):
            raise GitLabClientError(
                "GitLab protected branch entry has a malformed role-based access_level."
            )
        if access_level not in _ALLOWED_ROLE_LEVELS:
            raise GitLabClientError(
                "GitLab protected branch entry has an unrecognized role-based access_level."
            )
        role_levels.add(access_level)

    return GitLabProtectedBranchRule(
        name=name,
        allow_force_push=allow_force_push,
        inherited=inherited,
        role_push_access_levels=sorted(role_levels),
    )


class GitLabCollector:
    """Collects a normalized, read-only `GitLabProjectSnapshot` for one project.

    Issues only three GET operations, all through the injected
    `GitLabClient`: `GET /metadata`, `GET /projects/:id`, and every page of
    `GET /projects/:id/protected_branches`. Never calls CI Lint, CI/CD
    variables, runner-management, pipeline/job, log/trace/artifact,
    repository-file, or member/user/group/token endpoints, and never issues
    a non-GET request. Implements no check, CLI command, or report.
    """

    def __init__(self, client: GitLabClient) -> None:
        self._client = client

    def collect_project_snapshot(
        self,
        project: str | int,
        *,
        collected_at: dt.datetime | None = None,
    ) -> GitLabProjectSnapshot:
        """Collect and return the normalized snapshot for `project`.

        `project` may be a numeric project ID, a raw full path (e.g.
        "group/subgroup/project"), or an already percent-encoded full path
        -- see `canonicalize_gitlab_project`. The protected-branches request
        uses the numeric project ID GitLab itself returns from `GET
        /projects/:id`, not the caller-supplied identifier. `collected_at`
        is injectable for deterministic tests; it otherwise defaults to
        `datetime.now(datetime.UTC)`.

        No raw response is ever stored on `self` or attached to the
        returned snapshot -- and each raw response (in particular the `GET
        /projects/:id` dict, which can carry unrelated sensitive fields
        such as `runners_token`; see "Approved unavoidable-field exception"
        in the milestone doc) is deliberately never bound to a named local
        variable either. Each `get_json_object`/`get_json_list` call is
        nested directly inside its normalization call, so the decoded
        response is reachable only for the duration of that single
        normalization call and is eligible for garbage collection
        immediately afterward -- in particular, the raw project response is
        gone well before the protected-branches request begins.
        """
        when = collected_at if collected_at is not None else dt.datetime.now(dt.UTC)

        is_gitlab_com = _is_gitlab_com(self._client.instance_base_url)
        version, enterprise = _normalize_metadata(
            self._client.get_json_object("Get GitLab metadata", "/metadata"),
            is_gitlab_com=is_gitlab_com,
        )

        canonical_id = canonicalize_gitlab_project(project)
        project_settings = _normalize_project_settings(
            self._client.get_json_object("Get project", f"/projects/{canonical_id}")
        )

        protected_branches = [
            _normalize_protected_branch(entry)
            for entry in self._client.get_json_list(
                "List protected branches",
                f"/projects/{project_settings.project_id}/protected_branches",
            )
        ]

        return GitLabProjectSnapshot(
            gitlab_url=self._client.instance_base_url,
            gitlab_version=version,
            enterprise=enterprise,
            collected_at=when,
            project=project_settings,
            protected_branches=protected_branches,
        )
