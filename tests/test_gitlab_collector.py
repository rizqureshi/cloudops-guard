"""Tests for the normalized GitLab instance/project/protected-branch collector
(v0.2.0 Phase 2B, `GitLabCollector` in `src/cloudops_guard/collectors/gitlab.py`).

No test in this file may access the real network -- see `_no_real_network`
below. Tests exercise the real `GitLabClient` and `GitLabCollector` together
through an injected `FakeTransport`, mirroring realistic GitLab API response
shapes (including fields this milestone never reads) rather than minimal
stand-ins, so normalization is exercised against something closer to a real
response. All values are synthetic and non-sensitive; sentinel strings below
exist only to prove they never appear in this project's own output.
"""

from __future__ import annotations

import datetime as dt
import gc
import inspect
import json
import re
import weakref
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl

import pytest
import urllib3

from cloudops_guard.collectors.gitlab import (
    GitLabClient,
    GitLabClientError,
    GitLabCollector,
)
from cloudops_guard.models import (
    GitLabCiConfigSnapshot,
    GitLabProjectSettings,
    GitLabProjectSnapshot,
    GitLabResourceKind,
)

SYNTHETIC_TOKEN = "glpat-SYNTH3T1C-N3V3R-R34L-000000000000"  # test fixture only, never real

# Sentinel values that must never survive normalization, in any form.
SENTINEL_RUNNERS_TOKEN = "SYNTHETIC-RUNNERS-TOKEN-MUST-NOT-ESCAPE"
SENTINEL_OWNER_EMAIL = "synthetic-owner@example.invalid"
SENTINEL_OWNER_NAME = "Synthetic Project Owner"
SENTINEL_IMPORT_URL = "https://synthetic-user:synthetic-secret@example.invalid/upstream.git"
SENTINEL_CLONE_HTTP = "https://gitlab.example.com/group/subgroup/project.git"
SENTINEL_CLONE_SSH = "git@gitlab.example.com:group/subgroup/project.git"
SENTINEL_DESCRIPTION = "SYNTHETIC-PROJECT-DESCRIPTION-SENTINEL"
SENTINEL_UNKNOWN_NESTED = "SYNTHETIC-UNKNOWN-NESTED-FIELD-SENTINEL"
SENTINEL_PERSON_NAME = "Synthetic Person Name"
SENTINEL_GROUP_NAME = "Synthetic Group Name"
SENTINEL_DEPLOY_KEY_NAME = "Synthetic Deploy Key Name"
SENTINEL_CUSTOM_ROLE_NAME = "Synthetic Custom Role Name"

ALL_SENSITIVE_SENTINELS = [
    SENTINEL_RUNNERS_TOKEN,
    SENTINEL_OWNER_EMAIL,
    SENTINEL_OWNER_NAME,
    SENTINEL_IMPORT_URL,
    "synthetic-secret",  # the embedded credential inside SENTINEL_IMPORT_URL
    SENTINEL_CLONE_HTTP,
    SENTINEL_CLONE_SSH,
    SENTINEL_DESCRIPTION,
    SENTINEL_UNKNOWN_NESTED,
    SENTINEL_PERSON_NAME,
    SENTINEL_GROUP_NAME,
    SENTINEL_DEPLOY_KEY_NAME,
    SENTINEL_CUSTOM_ROLE_NAME,
]

# Field names that must never appear as keys anywhere in the snapshot's own
# JSON output (as opposed to their *values*, checked above).
EXCLUDED_FIELD_NAMES = [
    "runners_token",
    "owner",
    "import_url",
    "http_url_to_repo",
    "ssh_url_to_repo",
    "description",
    "access_level_description",
    "user_id",
    "group_id",
    "deploy_key_id",
    "member_role_id",
]


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


# --- Fake transport (mirrors tests/test_gitlab_client.py) ----------------------


class FakeResponse:
    def __init__(
        self, status: int, data: bytes = b"", headers: Mapping[str, str] | None = None
    ) -> None:
        self.status = status
        self.data = data
        self.headers: Mapping[str, str] = headers or {}


class FakeTransport:
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
        self.calls.append({"method": method, "url": url, "headers": dict(headers)})
        if not self._queue:
            raise AssertionError("FakeTransport has no queued responses left")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def json_response(
    status: int, payload: object, headers: Mapping[str, str] | None = None
) -> FakeResponse:
    return FakeResponse(status, json.dumps(payload).encode("utf-8"), headers)


def make_collector(
    transport: FakeTransport, *, base_url: str = "https://gitlab.example.com"
) -> GitLabCollector:
    client = GitLabClient(base_url, SYNTHETIC_TOKEN, transport=transport)
    return GitLabCollector(client)


# --- Realistic fixture payloads --------------------------------------------------


def metadata_payload(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "version": "18.4.1-ee",
        "revision": "abc1234",
        "kas": {
            "enabled": True,
            "externalUrl": "wss://kas.gitlab.example.com",
            "version": "18.4.1",
        },
        "enterprise": True,
    }
    defaults.update(overrides)
    return defaults


def project_payload(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "id": 42,
        "path_with_namespace": "group/subgroup/project",
        "default_branch": "main",
        "visibility": "private",
        "only_allow_merge_if_pipeline_succeeds": False,
        "public_jobs": False,
        "ci_push_repository_for_job_token_allowed": False,
        "ci_pipeline_variables_minimum_override_role": "maintainer",
        "auto_cancel_pending_pipelines": "enabled",
        "ci_default_git_depth": 50,
        "build_timeout": 3600,
        # Unrelated/sensitive fields GitLab includes automatically (see the
        # "Approved unavoidable-field exception" in the milestone doc) --
        # must never survive normalization.
        "runners_token": SENTINEL_RUNNERS_TOKEN,
        "owner": {"id": 7, "name": SENTINEL_OWNER_NAME, "public_email": SENTINEL_OWNER_EMAIL},
        "import_url": SENTINEL_IMPORT_URL,
        "http_url_to_repo": SENTINEL_CLONE_HTTP,
        "ssh_url_to_repo": SENTINEL_CLONE_SSH,
        "description": SENTINEL_DESCRIPTION,
        "some_future_field": {"nested": {"data": SENTINEL_UNKNOWN_NESTED}},
    }
    defaults.update(overrides)
    return defaults


def protected_branch_payload(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "id": 1,
        "name": "main",
        "push_access_levels": [{"access_level": 40, "access_level_description": "Maintainers"}],
        "merge_access_levels": [{"access_level": 40, "access_level_description": "Maintainers"}],
        "allow_force_push": False,
        "code_owner_approval_required": False,
    }
    defaults.update(overrides)
    return defaults


def _link_header(next_url: str | None) -> dict[str, str]:
    if next_url is None:
        return {}
    return {"Link": f'<{next_url}>; rel="next"'}


# --- Successful collection: identifiers, endpoint sequence ---------------------


def test_collects_snapshot_for_numeric_project_id() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [protected_branch_payload()], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert isinstance(snapshot, GitLabProjectSnapshot)
    assert snapshot.project.project_id == 42


def test_collects_snapshot_for_raw_subgroup_path() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    make_collector(transport).collect_project_snapshot("group/subgroup/project")
    project_call_url = str(transport.calls[1]["url"])
    assert "/projects/group%2Fsubgroup%2Fproject" in project_call_url


def test_collects_snapshot_for_already_encoded_subgroup_path() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    make_collector(transport).collect_project_snapshot("group%2Fsubgroup%2Fproject")
    project_call_url = str(transport.calls[1]["url"])
    assert "/projects/group%2Fsubgroup%2Fproject" in project_call_url


def test_protected_branches_request_uses_the_returned_numeric_project_id() -> None:
    # The caller supplies a path; GitLab's response reports a different
    # numeric ID (999) than any caller-supplied value -- the protected-
    # branches request must use GitLab's own returned ID, not re-derive one
    # from the caller's input.
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload(id=999)))
        .queue(json_response(200, [], _link_header(None)))
    )
    make_collector(transport).collect_project_snapshot("group/subgroup/project")
    branches_call_url = str(transport.calls[2]["url"])
    assert "/projects/999/protected_branches" in branches_call_url


_ALLOWED_CALL_RE = re.compile(
    r"^https://gitlab\.example\.com/api/v4/"
    r"(metadata|projects/[^/?]+|projects/\d+/protected_branches(\?.*)?)$"
)


def test_only_the_three_permitted_endpoint_families_are_called() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [protected_branch_payload()], _link_header(None)))
    )
    make_collector(transport).collect_project_snapshot(42)
    assert len(transport.calls) == 3
    for call in transport.calls:
        assert call["method"] == "GET"
        assert _ALLOWED_CALL_RE.match(str(call["url"])), call["url"]


def test_no_disallowed_endpoint_is_ever_called() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    make_collector(transport).collect_project_snapshot(42)
    forbidden_fragments = (
        "/ci/lint",
        "/variables",
        "/runners",
        "/pipelines",
        "/jobs",
        "/trace",
        "/artifacts",
        "/repository",
        "/members",
        "/access_tokens",
    )
    for call in transport.calls:
        url = str(call["url"])
        for fragment in forbidden_fragments:
            assert fragment not in url


# --- Protected-branch pagination and empty list ---------------------------------


def test_protected_branches_pagination_is_followed_and_combined() -> None:
    base = "https://gitlab.example.com/api/v4/projects/42/protected_branches"
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(
            json_response(
                200,
                [protected_branch_payload(name="main")],
                _link_header(f"{base}?per_page=100&page=2"),
            )
        )
        .queue(json_response(200, [protected_branch_payload(name="release/*")], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert [rule.name for rule in snapshot.protected_branches] == ["main", "release/*"]
    assert len(transport.calls) == 4


def test_empty_protected_branch_list_is_valid() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert snapshot.protected_branches == []


# --- Extra/unknown response fields are ignored ----------------------------------


def test_extra_project_response_fields_are_ignored_without_error() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload(extra_metadata_field="ignored")))
        .queue(json_response(200, project_payload(yet_another_unknown_field=[1, 2, 3])))
        .queue(
            json_response(
                200,
                [protected_branch_payload(unexpected_branch_field="ignored")],
                _link_header(None),
            )
        )
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert snapshot.project.project_id == 42
    assert len(snapshot.protected_branches) == 1


# --- Required project fields: missing, parametrized -----------------------------

_REQUIRED_PROJECT_FIELDS = [
    "id",
    "path_with_namespace",
    "default_branch",
    "visibility",
    "only_allow_merge_if_pipeline_succeeds",
    "public_jobs",
    "ci_push_repository_for_job_token_allowed",
    "ci_pipeline_variables_minimum_override_role",
    "auto_cancel_pending_pipelines",
    "ci_default_git_depth",
    "build_timeout",
]


@pytest.mark.parametrize("field", _REQUIRED_PROJECT_FIELDS)
def test_missing_required_project_field_raises(field: str) -> None:
    payload = project_payload()
    del payload[field]
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, payload))
    )
    with pytest.raises(GitLabClientError):
        make_collector(transport).collect_project_snapshot(42)


# --- Required project fields: wrong types ----------------------------------------


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("id", True),
        ("id", "42"),
        ("id", 0),
        ("id", -5),
        ("path_with_namespace", 123),
        ("path_with_namespace", None),
        ("default_branch", None),
        ("default_branch", ""),
        ("visibility", 1),
        ("only_allow_merge_if_pipeline_succeeds", 0),
        ("only_allow_merge_if_pipeline_succeeds", "false"),
        ("only_allow_merge_if_pipeline_succeeds", None),
        ("public_jobs", 1),
        ("ci_push_repository_for_job_token_allowed", "true"),
        ("ci_pipeline_variables_minimum_override_role", 1),
        ("auto_cancel_pending_pipelines", True),
        ("ci_default_git_depth", True),
        ("ci_default_git_depth", False),
        ("ci_default_git_depth", "50"),
        ("ci_default_git_depth", -1),
        ("ci_default_git_depth", 1001),
        ("ci_default_git_depth", 999_999_999),
        ("ci_default_git_depth", 1.5),
        ("ci_default_git_depth", [1]),
        ("ci_default_git_depth", {}),
        ("build_timeout", True),
        ("build_timeout", "3600"),
        ("build_timeout", 0),
        ("build_timeout", -1),
    ],
)
def test_wrong_type_required_project_field_raises(field: str, bad_value: object) -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload(**{field: bad_value})))
    )
    with pytest.raises(GitLabClientError):
        make_collector(transport).collect_project_snapshot(42)


# --- Enum fields: unknown values ---------------------------------------------------


def test_unknown_visibility_value_raises() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload(visibility="unknown")))
    )
    with pytest.raises(GitLabClientError):
        make_collector(transport).collect_project_snapshot(42)


def test_unknown_override_role_value_raises() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(
            json_response(200, project_payload(ci_pipeline_variables_minimum_override_role="admin"))
        )
    )
    with pytest.raises(GitLabClientError):
        make_collector(transport).collect_project_snapshot(42)


def test_unknown_auto_cancel_value_raises() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload(auto_cancel_pending_pipelines="maybe")))
    )
    with pytest.raises(GitLabClientError):
        make_collector(transport).collect_project_snapshot(42)


# --- ci_default_git_depth: null retained, missing rejected ------------------------


def test_null_ci_default_git_depth_is_retained_as_none() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload(ci_default_git_depth=None)))
        .queue(json_response(200, [], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert snapshot.project.ci_default_git_depth is None


def test_zero_ci_default_git_depth_is_retained() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload(ci_default_git_depth=0)))
        .queue(json_response(200, [], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert snapshot.project.ci_default_git_depth == 0


@pytest.mark.parametrize("value", [0, 1, 1000])
def test_ci_default_git_depth_boundary_values_are_accepted_unchanged(value: int) -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload(ci_default_git_depth=value)))
        .queue(json_response(200, [], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert snapshot.project.ci_default_git_depth == value


@pytest.mark.parametrize("value", [1001, 1_000_000_000])
def test_ci_default_git_depth_above_1000_is_rejected(value: int) -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload(ci_default_git_depth=value)))
    )
    with pytest.raises(GitLabClientError, match="between 0 and 1000"):
        make_collector(transport).collect_project_snapshot(42)


def test_ci_default_git_depth_rejected_value_not_reproduced_in_exception() -> None:
    sentinel_value = 123_456_789
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload(ci_default_git_depth=sentinel_value)))
    )
    with pytest.raises(GitLabClientError) as excinfo:
        make_collector(transport).collect_project_snapshot(42)
    assert str(sentinel_value) not in str(excinfo.value)


# (missing ci_default_git_depth is covered by test_missing_required_project_field_raises)


# --- Empty repository / no default branch -----------------------------------------


@pytest.mark.parametrize("bad_default_branch", [None, ""])
def test_empty_or_null_default_branch_raises(bad_default_branch: object) -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload(default_branch=bad_default_branch)))
    )
    with pytest.raises(GitLabClientError):
        make_collector(transport).collect_project_snapshot(42)


# --- Metadata: missing/malformed version or enterprise -----------------------------


@pytest.mark.parametrize(
    "bad_metadata",
    [
        {},
        {"enterprise": True},  # missing version
        {"version": ""},  # empty version, missing enterprise
        {"version": "not-a-version", "enterprise": True},
        {"version": "18.4", "enterprise": "true"},
        {"version": "18.4", "enterprise": 1},
        {"version": "18.4"},  # missing enterprise
        {"version": None, "enterprise": True},
        {"version": 18.4, "enterprise": True},
    ],
)
def test_malformed_metadata_raises(bad_metadata: dict[str, object]) -> None:
    transport = FakeTransport().queue(json_response(200, bad_metadata))
    with pytest.raises(GitLabClientError):
        make_collector(transport).collect_project_snapshot(42)
    # Failed before any further (project/protected-branches) request.
    assert len(transport.calls) == 1


# --- Self-managed version boundary -------------------------------------------------


def test_self_managed_below_minimum_version_is_rejected() -> None:
    transport = FakeTransport().queue(json_response(200, metadata_payload(version="18.3.9")))
    with pytest.raises(GitLabClientError):
        make_collector(transport, base_url="https://gitlab.example.com").collect_project_snapshot(
            42
        )


def test_self_managed_at_minimum_version_is_accepted() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload(version="18.4.0")))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    snapshot = make_collector(
        transport, base_url="https://gitlab.example.com"
    ).collect_project_snapshot(42)
    assert snapshot.gitlab_version == "18.4.0"


@pytest.mark.parametrize("version", ["19.0.0", "20.1.3", "25.6"])
def test_self_managed_later_major_versions_are_accepted(version: str) -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload(version=version)))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    snapshot = make_collector(
        transport, base_url="https://gitlab.example.com"
    ).collect_project_snapshot(42)
    assert snapshot.gitlab_version == version


def test_self_managed_version_with_gitlab_style_suffix_is_accepted() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload(version="18.4.2-ee")))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    snapshot = make_collector(
        transport, base_url="https://gitlab.example.com"
    ).collect_project_snapshot(42)
    assert snapshot.gitlab_version == "18.4.2-ee"


def test_gitlab_com_is_accepted_regardless_of_version() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload(version="17.0.0")))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    snapshot = make_collector(transport, base_url="https://gitlab.com").collect_project_snapshot(42)
    assert snapshot.gitlab_version == "17.0.0"
    assert snapshot.gitlab_url == "https://gitlab.com"


def test_gitlab_com_still_requires_a_valid_metadata_response() -> None:
    transport = FakeTransport().queue(json_response(200, {"version": "not-a-version"}))
    with pytest.raises(GitLabClientError):
        make_collector(transport, base_url="https://gitlab.com").collect_project_snapshot(42)


# --- inherited field: absent vs. present ------------------------------------------


def test_inherited_absent_normalizes_to_none() -> None:
    payload = protected_branch_payload()
    del payload["merge_access_levels"]  # unrelated -- keep payload realistic
    assert "inherited" not in payload
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [payload], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert snapshot.protected_branches[0].inherited is None


@pytest.mark.parametrize("value", [True, False])
def test_inherited_present_is_retained_as_boolean(value: bool) -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [protected_branch_payload(inherited=value)], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert snapshot.protected_branches[0].inherited is value


# --- Missing/malformed protected-branch fields ------------------------------------


@pytest.mark.parametrize(
    "bad_branch",
    [
        {"allow_force_push": False, "push_access_levels": []},  # missing name
        {"name": "", "allow_force_push": False, "push_access_levels": []},  # empty name
        {"name": "main", "push_access_levels": []},  # missing allow_force_push
        {"name": "main", "allow_force_push": "false", "push_access_levels": []},  # wrong type
        {"name": "main", "allow_force_push": False},  # missing push_access_levels
        {"name": "main", "allow_force_push": False, "push_access_levels": "not-a-list"},
        {
            "name": "main",
            "allow_force_push": False,
            "push_access_levels": [{"access_level": True}],
        },
        {
            "name": "main",
            "allow_force_push": False,
            "push_access_levels": [{"access_level": 99}],
        },
        {
            "name": "main",
            "allow_force_push": False,
            "push_access_levels": ["not-an-object"],
        },
        {"name": "main", "allow_force_push": False, "inherited": None, "push_access_levels": []},
    ],
)
def test_malformed_protected_branch_entry_raises(bad_branch: dict[str, object]) -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [bad_branch], _link_header(None)))
    )
    with pytest.raises(GitLabClientError):
        make_collector(transport).collect_project_snapshot(42)


# --- Role-based push levels vs. identifier-scoped entries -------------------------


def test_role_based_push_levels_are_retained_sorted_and_deduplicated() -> None:
    payload = protected_branch_payload(
        push_access_levels=[
            {"access_level": 40},
            {"access_level": 0},
            {"access_level": 40},  # duplicate
            {"access_level": 30},
        ]
    )
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [payload], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert snapshot.protected_branches[0].role_push_access_levels == [0, 30, 40]


def test_user_group_deploy_key_and_custom_role_entries_are_ignored() -> None:
    payload = protected_branch_payload(
        push_access_levels=[
            {"access_level": 40},  # role-based, retained
            {"access_level": 30, "user_id": 101, "access_level_description": SENTINEL_PERSON_NAME},
            {"access_level": 30, "group_id": 202, "access_level_description": SENTINEL_GROUP_NAME},
            {
                "access_level": 30,
                "deploy_key_id": 303,
                "access_level_description": SENTINEL_DEPLOY_KEY_NAME,
            },
            {
                "access_level": 30,
                "member_role_id": 404,
                "access_level_description": SENTINEL_CUSTOM_ROLE_NAME,
            },
        ]
    )
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [payload], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    # Only the role-based (unscoped) entry survives; every identifier-scoped
    # entry -- even ones that also carry a role-shaped access_level -- is
    # excluded, and none of their identifiers or descriptions appear anywhere.
    assert snapshot.protected_branches[0].role_push_access_levels == [40]
    dumped = snapshot.model_dump_json()
    for sentinel in (
        SENTINEL_PERSON_NAME,
        SENTINEL_GROUP_NAME,
        SENTINEL_DEPLOY_KEY_NAME,
        SENTINEL_CUSTOM_ROLE_NAME,
    ):
        assert sentinel not in dumped


def test_zero_scope_identifier_still_excludes_the_entry() -> None:
    # A scope identifier of 0 is still non-null and must exclude the entry --
    # presence must never be judged by truthiness.
    payload = protected_branch_payload(push_access_levels=[{"access_level": 30, "user_id": 0}])
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [payload], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    assert snapshot.protected_branches[0].role_push_access_levels == []


# --- Transport/API errors: sanitized, no partial snapshot -------------------------


def test_metadata_transport_failure_is_sanitized_and_no_further_calls_made() -> None:
    transport = FakeTransport().queue(
        urllib3.exceptions.NewConnectionError(None, "Failed to establish a new connection")
    )
    with pytest.raises(GitLabClientError, match="connection failed"):
        make_collector(transport).collect_project_snapshot(42)
    assert len(transport.calls) == 1


def test_project_not_found_is_sanitized_and_no_snapshot_returned() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(FakeResponse(404, b"{}"))
    )
    with pytest.raises(GitLabClientError, match="not found"):
        make_collector(transport).collect_project_snapshot(42)
    # The failure happened before the protected-branches request.
    assert len(transport.calls) == 2


def test_protected_branches_failure_is_sanitized_and_no_snapshot_returned() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(FakeResponse(500, b"{}"))
    )
    with pytest.raises(GitLabClientError, match="GitLab server error"):
        make_collector(transport).collect_project_snapshot(42)


def test_error_message_does_not_reproduce_raw_project_response() -> None:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(FakeResponse(403, b'{"message": "insufficient_scope glpat-DENIED-TOKEN-VALUE"}'))
    )
    with pytest.raises(GitLabClientError) as excinfo:
        make_collector(transport).collect_project_snapshot(42)
    assert "glpat-DENIED-TOKEN-VALUE" not in str(excinfo.value)
    assert "insufficient_scope" not in str(excinfo.value)


# --- Deterministic collected_at injection ------------------------------------------


def test_collected_at_uses_the_injected_value_exactly() -> None:
    when = dt.datetime(2026, 3, 15, 8, 30, tzinfo=dt.UTC)
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42, collected_at=when)
    assert snapshot.collected_at == when


def test_collected_at_defaults_to_now_utc_when_not_injected() -> None:
    before = dt.datetime.now(dt.UTC)
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    snapshot = make_collector(transport).collect_project_snapshot(42)
    after = dt.datetime.now(dt.UTC)
    assert snapshot.collected_at.tzinfo is not None
    assert before <= snapshot.collected_at <= after


# --- No real-network access --------------------------------------------------------


def test_default_transport_falls_back_to_poolmanager_but_is_guarded() -> None:
    client = GitLabClient("https://gitlab.example.com", SYNTHETIC_TOKEN)
    collector = GitLabCollector(client)
    with pytest.raises(AssertionError, match="real urllib3.PoolManager"):
        collector.collect_project_snapshot(42)


# --- Raw-response minimization ------------------------------------------------------
#
# The raw `GET /projects/:id` dict can carry unrelated sensitive fields (see
# `runners_token` in `project_payload()` above). It must not merely be
# excluded from the *normalized* output (covered by the sensitive-field
# firewall tests below) -- it must actually become eligible for garbage
# collection before the protected-branches request begins, rather than
# lingering as a live local variable for the rest of `collect_project_
# snapshot()`. A plain `dict` doesn't support weak references, so this test
# wraps the raw project response in a `dict` subclass (which does) at the
# exact point `GitLabClient.get_json_object` would normally return it to
# the collector, then asserts the weakref is already dead by the time the
# protected-branches request is issued.


class _TrackedDict(dict):
    """A `dict` subclass -- adds `__weakref__` support (plain `dict` has
    none), letting this test hold only a weak reference to a raw response
    and prove it was actually collected, not merely unused.
    """


class _ProjectResponseTrackingClient(GitLabClient):
    """Wraps the `GET /projects/:id` response in a weak-referenceable dict.

    Every other `get_json_object`/`get_json_list` call (metadata,
    protected branches) passes through unchanged.
    """

    def __init__(self, *args: Any, tracked_ref_holder: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tracked_ref_holder = tracked_ref_holder

    def get_json_object(self, operation: str, path: str) -> dict[str, Any]:
        result = super().get_json_object(operation, path)
        if path.startswith("/projects/") and not path.endswith("/protected_branches"):
            tracked = _TrackedDict(result)
            self._tracked_ref_holder["ref"] = weakref.ref(tracked)
            return tracked
        return result


class _ProtectedBranchesLivenessGuardTransport(FakeTransport):
    """Asserts the tracked raw project response is already dead the moment
    the protected-branches request is issued -- checked from inside
    `request()` so the assertion runs at exactly that point in time, not
    merely at the end of the whole collection call.
    """

    def __init__(self, tracked_ref_holder: dict[str, Any]) -> None:
        super().__init__()
        self._tracked_ref_holder = tracked_ref_holder
        self.checked_at_protected_branches_request = False

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if "/protected_branches" in url:
            gc.collect()
            ref = self._tracked_ref_holder.get("ref")
            assert ref is not None, "tracked weakref was never set -- test setup is broken"
            assert ref() is None, (
                "raw GET /projects/:id response was still referenced when the "
                "protected-branches request began"
            )
            self.checked_at_protected_branches_request = True
        return super().request(method, url, **kwargs)


def test_raw_project_response_is_collected_before_protected_branches_request() -> None:
    tracked_ref_holder: dict[str, Any] = {}
    transport = (
        _ProtectedBranchesLivenessGuardTransport(tracked_ref_holder)
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [], _link_header(None)))
    )
    client = _ProjectResponseTrackingClient(
        "https://gitlab.example.com",
        SYNTHETIC_TOKEN,
        transport=transport,
        tracked_ref_holder=tracked_ref_holder,
    )
    GitLabCollector(client).collect_project_snapshot(42)
    assert transport.checked_at_protected_branches_request is True


# --- Sensitive-field firewall -------------------------------------------------------


def _full_sensitive_protected_branch_payload() -> dict[str, object]:
    return protected_branch_payload(
        inherited=True,
        push_access_levels=[
            {"access_level": 40},
            {
                "access_level": 30,
                "user_id": 101,
                "access_level_description": SENTINEL_PERSON_NAME,
            },
            {
                "access_level": 30,
                "group_id": 202,
                "access_level_description": SENTINEL_GROUP_NAME,
            },
            {
                "access_level": 30,
                "deploy_key_id": 303,
                "access_level_description": SENTINEL_DEPLOY_KEY_NAME,
            },
            {
                "access_level": 30,
                "member_role_id": 404,
                "access_level_description": SENTINEL_CUSTOM_ROLE_NAME,
            },
        ],
    )


def _collect_sensitive_snapshot() -> GitLabProjectSnapshot:
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [_full_sensitive_protected_branch_payload()], _link_header(None)))
    )
    return make_collector(transport).collect_project_snapshot(42)


def test_sensitive_values_do_not_appear_in_the_snapshot_model_dump() -> None:
    snapshot = _collect_sensitive_snapshot()
    dumped = snapshot.model_dump()
    dumped_text = json.dumps(dumped, default=str)
    for sentinel in ALL_SENSITIVE_SENTINELS:
        assert sentinel not in dumped_text


def test_sensitive_values_do_not_appear_in_the_snapshot_json() -> None:
    snapshot = _collect_sensitive_snapshot()
    dumped_json = snapshot.model_dump_json()
    for sentinel in ALL_SENSITIVE_SENTINELS:
        assert sentinel not in dumped_json


def test_sensitive_values_do_not_appear_in_the_snapshot_repr() -> None:
    snapshot = _collect_sensitive_snapshot()
    snapshot_repr = repr(snapshot)
    for sentinel in ALL_SENSITIVE_SENTINELS:
        assert sentinel not in snapshot_repr


def test_excluded_field_names_do_not_appear_as_keys_in_snapshot_json() -> None:
    snapshot = _collect_sensitive_snapshot()
    dumped_json = snapshot.model_dump_json()
    for field_name in EXCLUDED_FIELD_NAMES:
        assert f'"{field_name}"' not in dumped_json


def test_sensitive_values_do_not_appear_in_normalization_exceptions() -> None:
    # Force a normalization failure on a payload that still carries every
    # sentinel value, to prove failure paths don't reproduce them either.
    bad_project = project_payload(visibility="not-a-real-visibility")
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, bad_project))
    )
    with pytest.raises(GitLabClientError) as excinfo:
        make_collector(transport).collect_project_snapshot(42)
    message = str(excinfo.value)
    for sentinel in ALL_SENSITIVE_SENTINELS:
        assert sentinel not in message


def test_sensitive_protected_branch_values_do_not_appear_in_normalization_exceptions() -> None:
    bad_branch = _full_sensitive_protected_branch_payload()
    bad_branch["push_access_levels"].append({"access_level": 12345})  # forces a raise
    transport = (
        FakeTransport()
        .queue(json_response(200, metadata_payload()))
        .queue(json_response(200, project_payload()))
        .queue(json_response(200, [bad_branch], _link_header(None)))
    )
    with pytest.raises(GitLabClientError) as excinfo:
        make_collector(transport).collect_project_snapshot(42)
    message = str(excinfo.value)
    for sentinel in ALL_SENSITIVE_SENTINELS:
        assert sentinel not in message


# ============================================================================
# Phase 2C-E2: CI Lint collection and normalization
# (`GitLabCollector.collect_ci_config_snapshot`)
# ============================================================================
#
# A separate collection concern from `collect_project_snapshot` above: these
# tests exercise `collect_ci_config_snapshot` in isolation, queuing only the
# single `GET /projects/:id/ci/lint` response it issues -- never the
# metadata/project/protected-branches sequence. All merged_yaml content
# below is synthetic and non-sensitive; sentinel strings exist only to prove
# they never survive normalization.
#
# Phase 2C-E2 review follow-up: `collect_ci_config_snapshot` takes a
# `GitLabProjectSnapshot` (typically produced by a prior
# `collect_project_snapshot()` call on the same client) rather than
# independent `project`/`project_path`/`default_branch` values -- the
# project ID, path, and default branch used for the CI Lint request are all
# derived exclusively from that snapshot, so a caller cannot substitute a
# different project path or branch than the one it was actually collected
# against. `make_project_snapshot` below builds a synthetic snapshot
# in-process (no HTTP) for these tests.

SENTINEL_CI_VARIABLE_SECRET = "SYNTHETIC-CI-VARIABLE-SECRET-MUST-NOT-ESCAPE"
SENTINEL_CI_SCRIPT = "echo SYNTHETIC-SCRIPT-CONTENT-MUST-NOT-ESCAPE"
SENTINEL_CI_WARNING = "SYNTHETIC-WARNING-TEXT-MUST-NOT-ESCAPE"
SENTINEL_CI_ERROR = "SYNTHETIC-ERROR-TEXT-MUST-NOT-ESCAPE"
SENTINEL_CI_INCLUDE = "synthetic/included-file.yml"
SENTINEL_SERVICE_ALIAS = "SYNTHETIC-SERVICE-ALIAS"
SENTINEL_SERVICE_COMMAND = "SYNTHETIC-SERVICE-COMMAND"
SENTINEL_SERVICE_ENTRYPOINT = "SYNTHETIC-SERVICE-ENTRYPOINT"
SENTINEL_SERVICE_VARIABLE_SECRET = "SYNTHETIC-SERVICE-VARIABLE-SECRET"


def make_project_snapshot(
    *,
    gitlab_url: str = "https://gitlab.example.com",
    project_id: int = 42,
    project_path: str = "group/project",
    default_branch: str = "main",
    collected_at: dt.datetime | None = None,
) -> GitLabProjectSnapshot:
    return GitLabProjectSnapshot(
        gitlab_url=gitlab_url,
        gitlab_version="18.4.1",
        enterprise=False,
        collected_at=collected_at
        if collected_at is not None
        else dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        project=GitLabProjectSettings(
            project_id=project_id,
            project_path=project_path,
            default_branch=default_branch,
            visibility="private",
            only_allow_merge_if_pipeline_succeeds=False,
            public_jobs=False,
            ci_push_repository_for_job_token_allowed=False,
            ci_pipeline_variables_minimum_override_role="maintainer",
            auto_cancel_pending_pipelines="enabled",
            ci_default_git_depth=50,
            build_timeout=3600,
        ),
        protected_branches=[],
    )


def ci_lint_payload(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "valid": True,
        "merged_yaml": "build:\n  script:\n    - echo hi\n  image: alpine:3.19\n",
        "errors": [],
        "warnings": [],
        "includes": [],
    }
    defaults.update(overrides)
    return defaults


def _queue_ci_lint(**overrides: object) -> FakeTransport:
    return FakeTransport().queue(json_response(200, ci_lint_payload(**overrides)))


def _collect_ci_config(
    transport: FakeTransport,
    *,
    project_snapshot: GitLabProjectSnapshot | None = None,
    base_url: str = "https://gitlab.example.com",
    collected_at: dt.datetime | None = None,
) -> GitLabCiConfigSnapshot:
    snapshot = (
        project_snapshot
        if project_snapshot is not None
        else make_project_snapshot(gitlab_url=base_url)
    )
    return make_collector(transport, base_url=base_url).collect_ci_config_snapshot(
        snapshot, collected_at=collected_at
    )


def _collect_from_yaml(
    merged_yaml: str,
    *,
    project_path: str = "group/project",
    default_branch: str = "main",
    collected_at: dt.datetime | None = None,
) -> GitLabCiConfigSnapshot:
    transport = _queue_ci_lint(merged_yaml=merged_yaml)
    snapshot = make_project_snapshot(project_path=project_path, default_branch=default_branch)
    return _collect_ci_config(transport, project_snapshot=snapshot, collected_at=collected_at)


def _images_as_tuples(
    snapshot: GitLabCiConfigSnapshot,
) -> list[tuple[str, GitLabResourceKind, str | None, bool]]:
    return [(r.job_name, r.resource_kind, r.image, r.dynamic) for r in snapshot.images]


# --- Transport/request contract --------------------------------------------------


def test_ci_lint_request_is_a_single_get_to_the_lint_endpoint() -> None:
    transport = _queue_ci_lint()
    _collect_ci_config(transport)
    assert len(transport.calls) == 1
    assert transport.calls[0]["method"] == "GET"
    url = str(transport.calls[0]["url"])
    assert url.startswith("https://gitlab.example.com/api/v4/projects/42/ci/lint?")


def test_ci_lint_request_query_params_are_exact() -> None:
    transport = _queue_ci_lint()
    _collect_ci_config(transport)
    url = str(transport.calls[0]["url"])
    params = dict(parse_qsl(url.split("?", 1)[1]))
    assert params == {"content_ref": "main", "dry_run": "false", "include_jobs": "false"}


def test_ci_lint_token_sent_only_in_header_not_url() -> None:
    transport = _queue_ci_lint()
    _collect_ci_config(transport)
    call = transport.calls[0]
    assert SYNTHETIC_TOKEN not in str(call["url"])
    assert call["headers"]["PRIVATE-TOKEN"] == SYNTHETIC_TOKEN


# --- Project identity is bound to the supplied GitLabProjectSnapshot --------------


def test_ci_lint_request_project_id_comes_from_the_project_snapshot() -> None:
    snapshot = make_project_snapshot(project_id=999, project_path="group/other")
    transport = _queue_ci_lint()
    _collect_ci_config(transport, project_snapshot=snapshot)
    url = str(transport.calls[0]["url"])
    assert "/projects/999/ci/lint" in url


def test_ci_lint_content_ref_comes_from_the_project_snapshot() -> None:
    snapshot = make_project_snapshot(default_branch="release/1.2")
    transport = _queue_ci_lint()
    _collect_ci_config(transport, project_snapshot=snapshot)
    url = str(transport.calls[0]["url"])
    params = dict(parse_qsl(url.split("?", 1)[1]))
    assert params["content_ref"] == "release/1.2"


def test_returned_project_path_comes_from_the_project_snapshot() -> None:
    snapshot = make_project_snapshot(project_path="group/subgroup/other-project")
    transport = _queue_ci_lint()
    result = _collect_ci_config(transport, project_snapshot=snapshot)
    assert result.project_path == "group/subgroup/other-project"


def test_content_ref_special_characters_in_the_snapshot_default_branch_are_encoded() -> None:
    branch = "feature/my branch & stuff?"
    snapshot = make_project_snapshot(default_branch=branch)
    transport = _queue_ci_lint()
    _collect_ci_config(transport, project_snapshot=snapshot)
    url = str(transport.calls[0]["url"])
    assert branch not in url
    params = dict(parse_qsl(url.split("?", 1)[1]))
    assert params["content_ref"] == branch


def test_caller_cannot_substitute_a_project_path_or_branch_independent_of_the_snapshot() -> None:
    # There is no project/project_path/default_branch parameter at all on
    # collect_ci_config_snapshot any more -- a structural guarantee, proven
    # directly from the method's own signature rather than by probing for
    # an override that no longer has anywhere to be passed.
    signature = inspect.signature(GitLabCollector.collect_ci_config_snapshot)
    assert list(signature.parameters) == ["self", "project_snapshot", "collected_at"]


def test_ci_lint_client_and_snapshot_gitlab_url_mismatch_fails_before_transport_access() -> None:
    snapshot = make_project_snapshot(gitlab_url="https://gitlab.other-instance.example.com")
    transport = _queue_ci_lint()
    with pytest.raises(GitLabClientError, match="does not match"):
        _collect_ci_config(
            transport, project_snapshot=snapshot, base_url="https://gitlab.example.com"
        )
    assert transport.calls == []  # no HTTP request was ever made


def test_gitlab_url_mismatch_error_does_not_reproduce_either_url() -> None:
    snapshot = make_project_snapshot(gitlab_url="https://gitlab.other-instance.example.com")
    transport = _queue_ci_lint()
    with pytest.raises(GitLabClientError) as excinfo:
        _collect_ci_config(
            transport, project_snapshot=snapshot, base_url="https://gitlab.example.com"
        )
    message = str(excinfo.value)
    assert "other-instance" not in message
    assert "gitlab.example.com" not in message


def test_matching_gitlab_url_succeeds() -> None:
    snapshot = make_project_snapshot(gitlab_url="https://gitlab.example.com")
    transport = _queue_ci_lint()
    result = _collect_ci_config(
        transport, project_snapshot=snapshot, base_url="https://gitlab.example.com"
    )
    assert isinstance(result, GitLabCiConfigSnapshot)


def test_ci_lint_request_disables_redirects_and_retries() -> None:
    captured: dict[str, object] = {}

    class _CapturingTransport(FakeTransport):
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
            captured["redirect"] = redirect
            captured["retries"] = retries
            return super().request(
                method, url, headers=headers, timeout=timeout, redirect=redirect, retries=retries
            )

    transport = _CapturingTransport().queue(json_response(200, ci_lint_payload()))
    _collect_ci_config(transport)
    assert captured == {"redirect": False, "retries": False}


def test_collect_ci_config_snapshot_issues_exactly_one_request() -> None:
    transport = _queue_ci_lint()
    _collect_ci_config(transport)
    assert len(transport.calls) == 1


def test_collect_ci_config_snapshot_never_calls_a_disallowed_endpoint() -> None:
    transport = _queue_ci_lint()
    _collect_ci_config(transport)
    forbidden_fragments = (
        "/variables",
        "/runners",
        "/pipelines",
        "/jobs/",
        "/trace",
        "/artifacts",
        "/repository/files",
        "/raw",
    )
    url = str(transport.calls[0]["url"])
    for fragment in forbidden_fragments:
        assert fragment not in url


@pytest.mark.parametrize(
    ("status", "match"),
    [
        (401, "authentication failed"),
        (403, "insufficient permissions"),
        (404, "not found"),
        (429, "rate limited"),
        (500, "GitLab server error"),
    ],
)
def test_ci_lint_http_error_statuses_are_sanitized(status: int, match: str) -> None:
    transport = FakeTransport().queue(FakeResponse(status, b"{}"))
    with pytest.raises(GitLabClientError, match=match):
        _collect_ci_config(transport)


def test_ci_lint_404_is_never_reinterpreted_as_no_ci_configuration() -> None:
    transport = FakeTransport().queue(FakeResponse(404, b'{"message": "404 Project Not Found"}'))
    with pytest.raises(GitLabClientError) as excinfo:
        _collect_ci_config(transport)
    message = str(excinfo.value).lower()
    assert "no ci configuration" not in message
    assert "no configuration" not in message
    assert "not found" in message


def test_ci_lint_404_response_body_is_never_reproduced() -> None:
    transport = FakeTransport().queue(
        FakeResponse(404, json.dumps({"message": SENTINEL_CI_ERROR}).encode("utf-8"))
    )
    with pytest.raises(GitLabClientError) as excinfo:
        _collect_ci_config(transport)
    assert SENTINEL_CI_ERROR not in str(excinfo.value)


def test_ci_lint_malformed_json_response_is_sanitized() -> None:
    transport = FakeTransport().queue(FakeResponse(200, b"not json{{{"))
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_ci_lint_wrong_root_response_type_is_rejected() -> None:
    transport = FakeTransport().queue(json_response(200, [1, 2, 3]))
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


# --- Response validation: 'valid' boolean strictness ------------------------------


@pytest.mark.parametrize("bad_valid", [None, "true", "false", 1, 0, [], {}])
def test_ci_lint_valid_must_be_a_real_boolean(bad_valid: object) -> None:
    transport = _queue_ci_lint(valid=bad_valid)
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_ci_lint_missing_valid_field_raises() -> None:
    payload = ci_lint_payload()
    del payload["valid"]
    transport = FakeTransport().queue(json_response(200, payload))
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_ci_lint_valid_false_raises_fixed_sanitized_error_without_inspecting_errors() -> None:
    transport = _queue_ci_lint(
        valid=False, errors=[SENTINEL_CI_ERROR], warnings=[SENTINEL_CI_WARNING]
    )
    with pytest.raises(GitLabClientError) as excinfo:
        _collect_ci_config(transport)
    message = str(excinfo.value)
    assert SENTINEL_CI_ERROR not in message
    assert SENTINEL_CI_WARNING not in message
    assert "invalid" in message.lower()


def test_ci_lint_valid_false_from_a_legacy_default_conflict_uses_the_same_generic_error() -> None:
    # Simulates GitLab's own rejection of a config mixing legacy top-level
    # image/services with a default: block -- the same generic
    # invalid-configuration error is raised; GitLab's specific error text is
    # never inspected, reproduced, or used to select special handling.
    transport = _queue_ci_lint(valid=False, errors=["image config should be a hash or a string"])
    with pytest.raises(GitLabClientError, match="invalid"):
        _collect_ci_config(transport)


# --- Response validation: merged_yaml presence/type/size --------------------------


@pytest.mark.parametrize("bad_merged_yaml", [None, "", 123, True, [], {}])
def test_ci_lint_merged_yaml_must_be_a_non_empty_string(bad_merged_yaml: object) -> None:
    transport = _queue_ci_lint(merged_yaml=bad_merged_yaml)
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_ci_lint_missing_merged_yaml_field_raises() -> None:
    payload = ci_lint_payload()
    del payload["merged_yaml"]
    transport = FakeTransport().queue(json_response(200, payload))
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_ci_lint_malformed_yaml_raises_sanitized_error_without_parser_text() -> None:
    bad_yaml = "build:\n  script: [unterminated\n  image: alpine\n"
    transport = _queue_ci_lint(merged_yaml=bad_yaml)
    with pytest.raises(GitLabClientError) as excinfo:
        _collect_ci_config(transport)
    message = str(excinfo.value)
    assert "unterminated" not in message
    assert "line" not in message.lower()
    assert "column" not in message.lower()


def test_ci_lint_non_mapping_yaml_root_raises() -> None:
    transport = _queue_ci_lint(merged_yaml="- one\n- two\n")
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_ci_lint_oversized_merged_yaml_is_rejected() -> None:
    huge_yaml = "build:\n  script:\n    - echo hi\n" + ("# padding\n" * 200_000)
    transport = _queue_ci_lint(merged_yaml=huge_yaml)
    with pytest.raises(GitLabClientError, match="size limit"):
        _collect_ci_config(transport)


# --- Response validation/privacy: discarded fields never survive ------------------


def test_ci_lint_discards_scripts_variables_warnings_errors_and_includes() -> None:
    merged_yaml = f"""\
variables:
  SECRET_VAR: "{SENTINEL_CI_VARIABLE_SECRET}"
build:
  image: alpine:3.19
  script:
    - "{SENTINEL_CI_SCRIPT}"
  variables:
    JOB_SECRET: "{SENTINEL_CI_VARIABLE_SECRET}"
"""
    transport = _queue_ci_lint(
        merged_yaml=merged_yaml,
        warnings=[SENTINEL_CI_WARNING],
        includes=[{"local": SENTINEL_CI_INCLUDE}],
    )
    snapshot = _collect_ci_config(transport)
    dumped = snapshot.model_dump_json()
    for sentinel in (
        SENTINEL_CI_VARIABLE_SECRET,
        SENTINEL_CI_SCRIPT,
        SENTINEL_CI_WARNING,
        SENTINEL_CI_INCLUDE,
    ):
        assert sentinel not in dumped
    assert sentinel not in repr(snapshot)


def test_ci_lint_discards_service_alias_command_entrypoint_and_variables() -> None:
    merged_yaml = f"""\
build:
  script:
    - echo hi
  image: alpine:3.19
  services:
    - name: postgres:15
      alias: {SENTINEL_SERVICE_ALIAS}
      command: ["{SENTINEL_SERVICE_COMMAND}"]
      entrypoint: ["{SENTINEL_SERVICE_ENTRYPOINT}"]
      pull_policy: always
      variables:
        SERVICE_SECRET: {SENTINEL_SERVICE_VARIABLE_SECRET}
"""
    snapshot = _collect_from_yaml(merged_yaml)
    dumped = snapshot.model_dump_json()
    for sentinel in (
        SENTINEL_SERVICE_ALIAS,
        SENTINEL_SERVICE_COMMAND,
        SENTINEL_SERVICE_ENTRYPOINT,
        SENTINEL_SERVICE_VARIABLE_SECRET,
    ):
        assert sentinel not in dumped
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False),
        ("build", GitLabResourceKind.CI_SERVICE, "postgres:15", False),
    ]


@pytest.mark.parametrize(
    "image_expr",
    [
        "$IMAGE_TAG",
        "${IMAGE_TAG}",
        "registry.example.com/$IMAGE_NAME:latest",
        "alpine:$TAG",
    ],
)
def test_dynamic_image_reference_is_marked_dynamic_without_retaining_the_expression(
    image_expr: str,
) -> None:
    merged_yaml = f'build:\n  script:\n    - echo hi\n  image: "{image_expr}"\n'
    snapshot = _collect_from_yaml(merged_yaml)
    assert len(snapshot.images) == 1
    ref = snapshot.images[0]
    assert ref.dynamic is True
    assert ref.image is None
    assert image_expr not in snapshot.model_dump_json()
    assert image_expr not in repr(snapshot)


def test_dynamic_service_image_reference_is_marked_dynamic_without_retaining_the_expression() -> (
    None
):
    merged_yaml = (
        "build:\n  script:\n    - echo hi\n  image: alpine:3.19\n"
        '  services:\n    - "postgres:$PG_VERSION"\n'
    )
    snapshot = _collect_from_yaml(merged_yaml)
    service_ref = [r for r in snapshot.images if r.resource_kind == GitLabResourceKind.CI_SERVICE][
        0
    ]
    assert service_ref.dynamic is True
    assert service_ref.image is None
    assert "$PG_VERSION" not in snapshot.model_dump_json()


# --- Inheritance matrix ------------------------------------------------------------


def test_job_image_as_plain_string() -> None:
    snapshot = _collect_from_yaml("build:\n  script: [echo hi]\n  image: alpine:3.19\n")
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False)
    ]


def test_job_image_as_mapping_with_name() -> None:
    snapshot = _collect_from_yaml("build:\n  script: [echo hi]\n  image:\n    name: alpine:3.19\n")
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False)
    ]


def test_job_services_as_strings() -> None:
    snapshot = _collect_from_yaml(
        "build:\n  script: [echo hi]\n  image: alpine:3.19\n"
        "  services:\n    - postgres:15\n    - redis:7\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False),
        ("build", GitLabResourceKind.CI_SERVICE, "postgres:15", False),
        ("build", GitLabResourceKind.CI_SERVICE, "redis:7", False),
    ]


def test_service_as_mapping_with_name() -> None:
    snapshot = _collect_from_yaml(
        "build:\n  script: [echo hi]\n  services:\n    - name: postgres:15\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_SERVICE, "postgres:15", False)
    ]


def test_default_image_is_inherited_by_a_job_with_no_own_image() -> None:
    snapshot = _collect_from_yaml("default:\n  image: alpine:3.19\nbuild:\n  script: [echo hi]\n")
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False)
    ]


def test_default_services_are_inherited_by_a_job_with_no_own_services() -> None:
    snapshot = _collect_from_yaml(
        "default:\n  services:\n    - postgres:15\n"
        "build:\n  script: [echo hi]\n  image: alpine:3.19\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False),
        ("build", GitLabResourceKind.CI_SERVICE, "postgres:15", False),
    ]


def test_legacy_top_level_image_is_inherited_like_default_image() -> None:
    snapshot = _collect_from_yaml("image: alpine:3.19\nbuild:\n  script: [echo hi]\n")
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False)
    ]


def test_legacy_top_level_services_are_inherited_like_default_services() -> None:
    snapshot = _collect_from_yaml(
        "services:\n  - postgres:15\nbuild:\n  script: [echo hi]\n  image: alpine:3.19\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False),
        ("build", GitLabResourceKind.CI_SERVICE, "postgres:15", False),
    ]


def test_job_image_overrides_default_image() -> None:
    snapshot = _collect_from_yaml(
        "default:\n  image: alpine:3.19\nbuild:\n  script: [echo hi]\n  image: python:3.12\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "python:3.12", False)
    ]


def test_job_services_override_default_services() -> None:
    snapshot = _collect_from_yaml(
        "default:\n  services:\n    - postgres:15\n"
        "build:\n  script: [echo hi]\n  image: alpine:3.19\n  services:\n    - redis:7\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False),
        ("build", GitLabResourceKind.CI_SERVICE, "redis:7", False),
    ]


def test_explicit_empty_services_list_suppresses_inherited_default_services() -> None:
    snapshot = _collect_from_yaml(
        "default:\n  services:\n    - postgres:15\n"
        "build:\n  script: [echo hi]\n  image: alpine:3.19\n  services: []\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False)
    ]


def test_inherit_default_false_suppresses_default_image_and_services() -> None:
    snapshot = _collect_from_yaml(
        "default:\n  image: alpine:3.19\n  services:\n    - postgres:15\n"
        "build:\n  script: [echo hi]\n  inherit:\n    default: false\n"
    )
    assert snapshot.images == []


def test_inherit_default_selective_image_only() -> None:
    snapshot = _collect_from_yaml(
        "default:\n  image: alpine:3.19\n  services:\n    - postgres:15\n"
        "build:\n  script: [echo hi]\n  inherit:\n    default: [image]\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False)
    ]


def test_inherit_default_selective_services_only() -> None:
    snapshot = _collect_from_yaml(
        "default:\n  image: alpine:3.19\n  services:\n    - postgres:15\n"
        "build:\n  script: [echo hi]\n  inherit:\n    default: [services]\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_SERVICE, "postgres:15", False)
    ]


def test_job_with_neither_image_nor_services_produces_no_entries() -> None:
    snapshot = _collect_from_yaml("build:\n  script: [echo hi]\n")
    assert snapshot.images == []


def test_hidden_template_entry_is_ignored() -> None:
    snapshot = _collect_from_yaml(".template:\n  image: alpine:3.19\nbuild:\n  script: [echo hi]\n")
    assert snapshot.images == []


def test_trigger_job_is_ignored() -> None:
    snapshot = _collect_from_yaml(
        "default:\n  image: alpine:3.19\ndeploy:\n  trigger:\n    project: group/other\n"
    )
    assert snapshot.images == []


def test_needs_only_bridge_job_is_ignored() -> None:
    snapshot = _collect_from_yaml(
        "default:\n  image: alpine:3.19\ndeploy:\n  needs:\n    - pipeline: group/other\n"
    )
    assert snapshot.images == []


def test_run_based_job_is_accepted() -> None:
    snapshot = _collect_from_yaml(
        "build:\n  run:\n    - name: step1\n      script: echo hi\n  image: alpine:3.19\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False)
    ]


def test_parallel_job_definition_is_handled_like_a_normal_job() -> None:
    snapshot = _collect_from_yaml(
        "build:\n  script: [echo hi]\n  image: alpine:3.19\n  parallel: 3\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False)
    ]


def test_multiple_jobs_preserve_merged_yaml_declaration_order() -> None:
    snapshot = _collect_from_yaml(
        "first:\n  script: [echo hi]\n  image: alpine:3.19\n"
        "second:\n  script: [echo hi]\n  image: python:3.12\n"
    )
    assert [ref.job_name for ref in snapshot.images] == ["first", "second"]


def test_job_image_precedes_its_own_service_images_in_order() -> None:
    snapshot = _collect_from_yaml(
        "build:\n  script: [echo hi]\n  image: alpine:3.19\n  services:\n    - redis:7\n"
    )
    assert [(ref.job_name, ref.resource_kind) for ref in snapshot.images] == [
        ("build", GitLabResourceKind.CI_JOB),
        ("build", GitLabResourceKind.CI_SERVICE),
    ]


def test_included_and_extended_result_is_treated_as_already_flattened() -> None:
    # No include:/extends:/!reference processing occurs in this module --
    # merged_yaml is trusted as already fully resolved by GitLab. This only
    # proves a job that in reality came from extends/!reference composition,
    # but arrives here as an ordinary flattened job (as merged_yaml would
    # represent it), normalizes exactly like any other job.
    snapshot = _collect_from_yaml(
        "build:\n  script: [echo hi]\n  image: alpine:3.19\n  services:\n    - redis:7\n"
    )
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False),
        ("build", GitLabResourceKind.CI_SERVICE, "redis:7", False),
    ]


def test_unsupported_image_structure_fails_closed() -> None:
    transport = _queue_ci_lint(merged_yaml="build:\n  script: [echo hi]\n  image: 12345\n")
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_unsupported_service_structure_fails_closed() -> None:
    transport = _queue_ci_lint(
        merged_yaml="build:\n  script: [echo hi]\n  services:\n    - 12345\n"
    )
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_malformed_inherit_default_value_fails_closed() -> None:
    transport = _queue_ci_lint(
        merged_yaml="build:\n  script: [echo hi]\n  inherit:\n    default: 123\n"
    )
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_malformed_default_block_type_fails_closed() -> None:
    transport = _queue_ci_lint(merged_yaml="default: not-a-mapping\nbuild:\n  script: [echo hi]\n")
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_entry_matching_neither_job_nor_bridge_shape_fails_closed() -> None:
    # A top-level, non-reserved, non-hidden key that is a mapping but has
    # none of script/run/trigger/needs -- something `valid: true` should
    # never actually produce, but normalization still fails closed on it
    # rather than silently skipping it.
    transport = _queue_ci_lint(merged_yaml="build:\n  tags:\n    - docker\n")
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


# --- Malformed `valid: true` responses: inherit:default list hardening ------------
#
# GitLab's own validation normally guarantees these shapes, but a malformed
# or nonconforming `valid: true` response must still fail closed with the
# fixed unsupported-structure error rather than being partially trusted.


@pytest.mark.parametrize(
    "list_literal",
    [
        "[image, true]",  # boolean member
        "[image, 123]",  # integer member
        "[image, null]",  # null member
        "[image, {a: b}]",  # mapping member
        "[image, [nested]]",  # nested list member
        "[image, not_a_real_keyword]",  # string not in GitLab's default: keyword set
    ],
)
def test_inherit_default_list_with_an_invalid_member_fails_closed(list_literal: str) -> None:
    merged_yaml = (
        "default:\n  image: alpine:3.19\n"
        f"build:\n  script: [echo hi]\n  inherit:\n    default: {list_literal}\n"
    )
    transport = _queue_ci_lint(merged_yaml=merged_yaml)
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_inherit_default_list_accepts_other_valid_gitlab_default_keywords() -> None:
    # "tags" is a real `default:` keyword unrelated to image/services --
    # proves the allowed set isn't hardcoded to just those two.
    merged_yaml = (
        "default:\n  image: alpine:3.19\n  tags:\n    - docker\n"
        "build:\n  script: [echo hi]\n  inherit:\n    default: [image, tags]\n"
    )
    snapshot = _collect_from_yaml(merged_yaml)
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False)
    ]


def test_inherit_default_list_still_accepts_image_and_services_together() -> None:
    merged_yaml = (
        "default:\n  image: alpine:3.19\n  services:\n    - postgres:15\n"
        "build:\n  script: [echo hi]\n  inherit:\n    default: [image, services]\n"
    )
    snapshot = _collect_from_yaml(merged_yaml)
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False),
        ("build", GitLabResourceKind.CI_SERVICE, "postgres:15", False),
    ]


def test_inherit_default_rejected_member_is_not_reproduced_in_exception() -> None:
    sentinel = "SYNTHETIC-INVALID-KEYWORD-SENTINEL"
    merged_yaml = f"build:\n  script: [echo hi]\n  inherit:\n    default: [{sentinel}]\n"
    transport = _queue_ci_lint(merged_yaml=merged_yaml)
    with pytest.raises(GitLabClientError) as excinfo:
        _collect_ci_config(transport)
    assert sentinel not in str(excinfo.value)


# --- Malformed `valid: true` responses: needs-only bridge hardening ---------------


def test_needs_empty_list_fails_closed() -> None:
    transport = _queue_ci_lint(merged_yaml="deploy:\n  needs: []\n")
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_job_only_mapping_entries_fail_closed() -> None:
    transport = _queue_ci_lint(merged_yaml="deploy:\n  needs:\n    - job: build\n")
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_plain_string_job_name_fails_closed() -> None:
    transport = _queue_ci_lint(merged_yaml="deploy:\n  needs:\n    - build\n")
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_scalar_value_fails_closed() -> None:
    transport = _queue_ci_lint(merged_yaml="deploy:\n  needs: build\n")
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_multiple_pipeline_entries_fail_closed() -> None:
    # Entry::Bridge itself requires *at most one* bridge-shaped need.
    transport = _queue_ci_lint(
        merged_yaml=(
            "deploy:\n  needs:\n    - pipeline: group/other-a\n    - pipeline: group/other-b\n"
        )
    )
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_pipeline_entry_with_a_job_key_is_a_cross_dependency_and_fails_closed() -> None:
    # A needs entry carrying both `pipeline:` and `job:` is a cross_dependency
    # (job-level needs on an upstream pipeline's job), not a bridge -- this
    # is needs-only (no trigger/script/run) but not the narrow bridge shape,
    # so it fails closed rather than being silently treated as ignorable.
    transport = _queue_ci_lint(
        merged_yaml="deploy:\n  needs:\n    - pipeline: group/other\n      job: build\n"
    )
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_job_only_entry_job_name_is_never_reproduced_in_exception() -> None:
    sentinel = "SYNTHETIC-DOWNSTREAM-JOB-NAME-SENTINEL"
    transport = _queue_ci_lint(merged_yaml=f"deploy:\n  needs:\n    - job: {sentinel}\n")
    with pytest.raises(GitLabClientError) as excinfo:
        _collect_ci_config(transport)
    assert sentinel not in str(excinfo.value)


def test_valid_needs_pipeline_bridge_downstream_project_name_is_never_retained() -> None:
    sentinel = "SYNTHETIC-DOWNSTREAM-PROJECT-SENTINEL"
    snapshot = _collect_from_yaml(f"deploy:\n  needs:\n    - pipeline: {sentinel}\n")
    assert snapshot.images == []
    assert sentinel not in snapshot.model_dump_json()


# --- needs:pipeline bridge: the documented bare-mapping form ----------------------
#
# Source-proven (GitLab EE's Entry::Need prepend, BridgeHash strategy,
# confirmed identical on 18-4-stable-ee and current master): GitLab's own
# ComposableArray#compose! wraps a bare Hash `needs:` value into a
# one-element array before per-item classification, so `needs: {pipeline:
# x}` (the exact form shown in GitLab's own CI/CD YAML reference example)
# and `needs: [{pipeline: x}]` reach GitLab identically. Both must be
# accepted here.


def test_needs_pipeline_official_mapping_syntax_is_accepted_and_skipped() -> None:
    # The exact example from GitLab's own CI/CD YAML reference for
    # needs:pipeline.
    snapshot = _collect_from_yaml(
        "upstream_status:\n  stage: test\n  needs:\n    pipeline: other/project\n"
    )
    assert snapshot.images == []


def test_needs_pipeline_mapping_syntax_downstream_project_name_is_never_retained() -> None:
    sentinel = "SYNTHETIC-DOWNSTREAM-PROJECT-SENTINEL"
    snapshot = _collect_from_yaml(f"upstream_status:\n  needs:\n    pipeline: {sentinel}\n")
    assert snapshot.images == []
    assert sentinel not in snapshot.model_dump_json()


def test_needs_pipeline_mapping_syntax_with_variable_reference_is_accepted() -> None:
    # GitLab validates only pipeline's *type* (a non-empty string), never
    # its content -- a CI/CD variable reference is itself a valid string
    # and must remain acceptable, without ever being retained.
    snapshot = _collect_from_yaml("upstream_status:\n  needs:\n    pipeline: $UPSTREAM_PROJECT\n")
    assert snapshot.images == []
    assert "$UPSTREAM_PROJECT" not in snapshot.model_dump_json()
    assert "UPSTREAM_PROJECT" not in snapshot.model_dump_json()


def test_needs_pipeline_mapping_and_list_forms_are_equivalent() -> None:
    mapping_form = _collect_from_yaml("deploy:\n  needs:\n    pipeline: group/other\n")
    list_form = _collect_from_yaml("deploy:\n  needs:\n    - pipeline: group/other\n")
    assert mapping_form.images == list_form.images == []


# --- needs:pipeline bridge: malformed mapping-form variants -----------------------


def test_needs_pipeline_empty_mapping_fails_closed() -> None:
    transport = _queue_ci_lint(merged_yaml="deploy:\n  needs: {}\n")
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_pipeline_mapping_missing_pipeline_key_fails_closed() -> None:
    transport = _queue_ci_lint(merged_yaml="deploy:\n  needs:\n    job: build\n")
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


@pytest.mark.parametrize(
    "pipeline_yaml_literal",
    [
        "''",  # empty string
        "null",  # None
        "123",  # int
        "true",  # bool
        "[]",  # empty list
        "{}",  # empty mapping
    ],
)
def test_needs_pipeline_mapping_with_empty_or_non_string_pipeline_fails_closed(
    pipeline_yaml_literal: str,
) -> None:
    transport = _queue_ci_lint(
        merged_yaml=f"deploy:\n  needs:\n    pipeline: {pipeline_yaml_literal}\n"
    )
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_pipeline_mapping_with_job_key_fails_closed() -> None:
    # pipeline + job (no list wrapper) is a cross-dependency shape, not a
    # bridge -- same fail-closed outcome as the list-form equivalent above.
    transport = _queue_ci_lint(
        merged_yaml="deploy:\n  needs:\n    pipeline: group/other\n    job: build\n"
    )
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_pipeline_mapping_with_project_key_fails_closed() -> None:
    # GitLab's own `bridge_to_upstream_pipeline?` excludes a `project` key
    # from the bridge strategy just like `job` does.
    transport = _queue_ci_lint(
        merged_yaml="deploy:\n  needs:\n    pipeline: group/other\n    project: group/other\n"
    )
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_pipeline_mapping_with_an_extra_key_fails_closed() -> None:
    # GitLab's BridgeHash allows exactly the single key `pipeline`.
    transport = _queue_ci_lint(
        merged_yaml="deploy:\n  needs:\n    pipeline: group/other\n    extra: value\n"
    )
    with pytest.raises(GitLabClientError):
        _collect_ci_config(transport)


def test_needs_pipeline_mapping_rejected_value_is_never_reproduced_in_exception() -> None:
    sentinel = "SYNTHETIC-REJECTED-PIPELINE-VALUE-SENTINEL"
    transport = _queue_ci_lint(
        merged_yaml=f"deploy:\n  needs:\n    pipeline: {sentinel}\n    extra: value\n"
    )
    with pytest.raises(GitLabClientError) as excinfo:
        _collect_ci_config(transport)
    assert sentinel not in str(excinfo.value)


# --- Existing bridge/job/image behavior remains unchanged -------------------------


def test_trigger_bridge_needs_pipeline_bridge_and_runnable_job_coexist_correctly() -> None:
    merged_yaml = (
        "default:\n  image: alpine:3.19\n"
        "trigger_deploy:\n  trigger:\n    project: group/other\n"
        "upstream_status:\n  needs:\n    pipeline: group/other\n"
        "build:\n  script: [echo hi]\n"
    )
    snapshot = _collect_from_yaml(merged_yaml)
    assert _images_as_tuples(snapshot) == [
        ("build", GitLabResourceKind.CI_JOB, "alpine:3.19", False)
    ]


# --- Malformed `valid: true` responses: model-construction errors ----------------
#
# No Pydantic ValidationError caused by untrusted merged-YAML content may
# escape `_normalize_ci_lint_response` directly -- it must be caught at the
# normalization boundary and replaced with the fixed, sanitized
# unsupported-structure message.

_UNSUPPORTED_STRUCTURE_MESSAGE = (
    "GitLab CI Lint merged YAML has an unexpected or unsupported structure."
)


def test_empty_job_name_from_merged_yaml_is_caught_and_sanitized() -> None:
    # An empty string is a syntactically valid YAML mapping key; this
    # proves the resulting Pydantic ValidationError (GitLabCiImageReference's
    # job_name has min_length=1) never escapes directly.
    transport = _queue_ci_lint(merged_yaml='"":\n  script: [echo hi]\n  image: alpine:3.19\n')
    with pytest.raises(GitLabClientError) as excinfo:
        _collect_ci_config(transport)
    assert str(excinfo.value) == _UNSUPPORTED_STRUCTURE_MESSAGE


def test_model_validation_error_never_leaks_pydantic_input_value_detail() -> None:
    transport = _queue_ci_lint(merged_yaml='"":\n  script: [echo hi]\n  image: alpine:3.19\n')
    with pytest.raises(GitLabClientError) as excinfo:
        _collect_ci_config(transport)
    message = str(excinfo.value)
    assert "input_value" not in message
    assert "job_name" not in message
    assert "alpine:3.19" not in message
    assert "ValidationError" not in message
    assert "for GitLabCiImageReference" not in message


# --- project_path / collected_at propagation ---------------------------------------


def test_ci_config_snapshot_uses_the_supplied_project_path() -> None:
    snapshot = _collect_from_yaml("build:\n  script: [echo hi]\n", project_path="group/other")
    assert snapshot.project_path == "group/other"


def test_ci_config_snapshot_uses_the_injected_collected_at() -> None:
    when = dt.datetime(2026, 5, 1, 10, 0, tzinfo=dt.UTC)
    snapshot = _collect_from_yaml("build:\n  script: [echo hi]\n", collected_at=when)
    assert snapshot.collected_at == when


def test_ci_config_snapshot_defaults_collected_at_to_now_utc() -> None:
    before = dt.datetime.now(dt.UTC)
    snapshot = _collect_from_yaml("build:\n  script: [echo hi]\n")
    after = dt.datetime.now(dt.UTC)
    assert snapshot.collected_at.tzinfo is not None
    assert before <= snapshot.collected_at <= after


# --- No real-network access ---------------------------------------------------------


def test_ci_lint_default_transport_falls_back_to_poolmanager_but_is_guarded() -> None:
    client = GitLabClient("https://gitlab.example.com", SYNTHETIC_TOKEN)
    collector = GitLabCollector(client)
    snapshot = make_project_snapshot()
    with pytest.raises(AssertionError, match="real urllib3.PoolManager"):
        collector.collect_ci_config_snapshot(snapshot)
