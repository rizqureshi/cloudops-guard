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
import json
import re
import weakref
from collections.abc import Mapping
from typing import Any

import pytest
import urllib3

from cloudops_guard.collectors.gitlab import (
    GitLabClient,
    GitLabClientError,
    GitLabCollector,
)
from cloudops_guard.models import GitLabProjectSnapshot

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
