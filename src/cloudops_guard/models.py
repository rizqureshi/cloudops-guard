"""Pydantic models for the normalized internal representation of audited state.

The Kubernetes models below are unchanged since the v0.1.0 release: checks and
report generation depend only on these models, never on the Kubernetes API
client types, so evaluation and reporting can be tested and reasoned about
without a live cluster. Their field names, types, ordering and serialization
are the released report contract and must not change.

The GitLab models (see the "GitLab models" section below) are additive and
platform-specific: they intentionally do not extend, refactor, or share
fields with the Kubernetes `Finding`/`AuditReport`, so the released v0.1.0
JSON/HTML report contract is unaffected by their presence. `Severity` and
`AuditSummary` are the only models shared between platforms, since they were
already platform-neutral.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, StrictBool, StrictInt, field_validator


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ResourceKind(StrEnum):
    NAMESPACE = "Namespace"
    POD = "Pod"
    DEPLOYMENT = "Deployment"


class ResourceRequirements(BaseModel):
    """Container CPU/memory requests and limits, as raw Kubernetes quantity strings."""

    cpu_request: str | None = None
    memory_request: str | None = None
    cpu_limit: str | None = None
    memory_limit: str | None = None


class ContainerInfo(BaseModel):
    """Normalized container metadata. Never includes env vars or secret data."""

    name: str
    image: str
    resources: ResourceRequirements = Field(default_factory=ResourceRequirements)


class ContainerRuntimeStatus(BaseModel):
    """Per-container runtime state. Restart counts are cumulative for the pod's

    current lifetime (Kubernetes does not expose a time-windowed rate), and no
    log content, messages or command output are ever included here.
    """

    container_name: str
    restart_count: int = 0
    ready: bool = False
    waiting_reason: str | None = None
    last_termination_reason: str | None = None


class PodInfo(BaseModel):
    name: str
    namespace: str
    containers: list[ContainerInfo] = Field(default_factory=list)
    container_statuses: list[ContainerRuntimeStatus] = Field(default_factory=list)
    owning_deployment: str | None = None


class DeploymentInfo(BaseModel):
    name: str
    namespace: str
    uid: str | None = None
    replicas: int
    containers: list[ContainerInfo] = Field(default_factory=list)


class NamespaceInfo(BaseModel):
    name: str


class ClusterSnapshot(BaseModel):
    """Everything collected from a cluster for a single audit run."""

    context: str
    collected_at: dt.datetime
    namespaces: list[NamespaceInfo] = Field(default_factory=list)
    pods: list[PodInfo] = Field(default_factory=list)
    deployments: list[DeploymentInfo] = Field(default_factory=list)


class Finding(BaseModel):
    check_id: str
    title: str
    severity: Severity
    cluster_context: str
    namespace: str
    resource_kind: ResourceKind
    resource_name: str
    container_name: str | None = None
    evidence: str
    impact: str
    recommendation: str
    auto_remediable: bool
    audited_at: dt.datetime


class AuditSummary(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0

    @property
    def total(self) -> int:
        return self.critical + self.high + self.medium + self.low


class AuditReport(BaseModel):
    cluster_context: str
    namespace_filter: str | None
    generated_at: dt.datetime
    findings: list[Finding] = Field(default_factory=list)
    summary: AuditSummary = Field(default_factory=AuditSummary)


# --- GitLab models (v0.2.0 Phase 1) -----------------------------------------
#
# Additive and platform-specific by design (see docs/milestones/v0.2.0-gitlab-
# audit.md, Phase 1). These do not inherit from or modify any Kubernetes model
# above. `Severity` and `AuditSummary` are reused as-is since they were
# already platform-neutral.


class GitLabResourceKind(StrEnum):
    PROJECT = "Project"
    PROTECTED_BRANCH = "ProtectedBranch"
    CI_JOB = "CIJob"
    CI_SERVICE = "CIService"


class GitLabFinding(BaseModel):
    """A single GitLab audit finding.

    Kept separate from the Kubernetes `Finding` model rather than sharing a
    base class: the two report different resource shapes (no cluster,
    namespace or container concepts on GitLab), and preserving the released
    Kubernetes report contract matters more than removing this small amount
    of duplication. Never includes scripts, variables, credentials, tokens,
    logs, traces or raw CI configuration.
    """

    check_id: str = Field(min_length=1)
    title: str
    severity: Severity
    project_path: str = Field(min_length=1)
    resource_kind: GitLabResourceKind
    resource_name: str = Field(min_length=1)
    job_name: str | None = Field(default=None, min_length=1)
    evidence: str
    impact: str
    recommendation: str
    auto_remediable: bool
    audited_at: dt.datetime


class GitLabAuditReport(BaseModel):
    """A single-project GitLab audit report.

    Kept separate from the Kubernetes `AuditReport` rather than adding a
    `platform` discriminator or GitLab-shaped fields to it, which would
    change the released v0.1.0 JSON report contract. Secure GitLab URL
    parsing/validation belongs to a later phase; `gitlab_url` is only
    required to be non-empty here.
    """

    platform: Literal["gitlab"] = "gitlab"
    gitlab_url: str = Field(min_length=1)
    project_id: int = Field(gt=0)
    project_path: str = Field(min_length=1)
    default_branch: str = Field(min_length=1)
    generated_at: dt.datetime
    findings: list[GitLabFinding] = Field(default_factory=list)
    summary: AuditSummary = Field(default_factory=AuditSummary)


# --- GitLab normalized collection models (v0.2.0 Phase 2B) -------------------
#
# Additive and platform-specific, like the Phase 1 models above. These hold
# the normalized, read-only instance/project/protected-branch snapshot that
# the checks in a later phase will evaluate. They are constructed only from
# an explicit allowlist of fields extracted by the collector -- never from a
# raw API response dict -- and never carry a catch-all/raw-response field, so
# a sensitive field GitLab returns but this milestone does not need (for
# example `runners_token` on the Projects API response) has nowhere to end
# up. See `docs/milestones/v0.2.0-gitlab-audit.md`, "Approved unavoidable-
# field exception: GET /projects/:id".


class GitLabProjectSettings(BaseModel):
    """Normalized, allowlisted project settings needed by the non-CI-Lint checks.

    Every field is required (no default), including ones whose valid value
    may be `False`, `0`, or an enum member that reads as "safe" -- so a
    field GitLab silently omits (e.g. a lower-than-Owner token on a
    restricted field) fails model construction instead of being mistaken
    for that field's presence with a safe value. `ci_default_git_depth` is
    the sole exception: it may legitimately be `null` on GitLab's side, so
    `None` is an accepted value, but the field itself is still required --
    a response missing the key entirely still fails. GitLab validates
    `ci_default_git_depth` to the range 0-1000 (see the Phase 2C-D1
    investigation in `docs/milestones/v0.2.0-gitlab-audit.md`); a value
    outside that range indicates a malformed response, not a legitimate
    GitLab state.
    """

    project_id: StrictInt = Field(gt=0)
    project_path: str = Field(min_length=1)
    default_branch: str = Field(min_length=1)
    visibility: Literal["private", "internal", "public"]
    only_allow_merge_if_pipeline_succeeds: StrictBool
    public_jobs: StrictBool
    ci_push_repository_for_job_token_allowed: StrictBool
    ci_pipeline_variables_minimum_override_role: Literal[
        "no_one_allowed", "owner", "maintainer", "developer"
    ]
    auto_cancel_pending_pipelines: Literal["enabled", "disabled"]
    ci_default_git_depth: StrictInt | None = Field(ge=0, le=1000)
    build_timeout: StrictInt = Field(gt=0)


_ALLOWED_ROLE_ACCESS_LEVELS = frozenset({0, 30, 40, 60})


def _validate_role_access_level(value: int) -> int:
    if value not in _ALLOWED_ROLE_ACCESS_LEVELS:
        raise ValueError("role_push_access_levels entries must be one of 0, 30, 40, or 60.")
    return value


# `Literal[0, 30, 40, 60]` alone is not strict enough: Python's `bool` is an
# `int` subclass, and pydantic's Literal matching compares by equality, so
# `False`/`True` pass through as `0`/`1` unless the underlying type is
# itself strict. `StrictInt` closes that (and also rejects `float`/`str`,
# e.g. `30.0` or `"30"`); the validator below then restricts the accepted
# integers to the four documented role levels.
_RoleAccessLevel = Annotated[StrictInt, AfterValidator(_validate_role_access_level)]


class GitLabProtectedBranchRule(BaseModel):
    """A single normalized protected-branch rule.

    `role_push_access_levels` retains only the four documented role-based
    access levels (0 = no one, 30 = Developer, 40 = Maintainer, 60 = Admin),
    as actual Python `int` values -- never a `bool`, `float`, or numeric
    string, even one that would compare equal (e.g. `False`, `30.0`, `"30"`).
    The collector normalization that builds this list is responsible for
    discarding any user/group/deploy-key/custom-role-scoped entry (and its
    identifiers/description) before construction -- this model never has a
    field to hold them in the first place.
    """

    name: str = Field(min_length=1)
    allow_force_push: StrictBool
    inherited: StrictBool | None = None
    role_push_access_levels: list[_RoleAccessLevel] = Field(default_factory=list)


class GitLabProjectSnapshot(BaseModel):
    """Everything collected from a GitLab instance for a single project audit.

    Deliberately has no raw-response or catch-all metadata field: only the
    normalized `project` and `protected_branches` models below, plus
    instance-identity fields. CI configuration (`GL-CI-001`) normalization
    is a separate, later task and has no representation here yet.
    """

    gitlab_url: str = Field(min_length=1)
    gitlab_version: str = Field(min_length=1)
    enterprise: StrictBool
    collected_at: dt.datetime
    project: GitLabProjectSettings
    protected_branches: list[GitLabProtectedBranchRule] = Field(default_factory=list)

    @field_validator("collected_at")
    @classmethod
    def _collected_at_must_be_timezone_aware(cls, value: dt.datetime) -> dt.datetime:
        # `tzinfo is not None` alone is insufficient: a custom `tzinfo`
        # subclass can be attached yet still return `None` from
        # `utcoffset()`, which Python's own datetime docs describe as
        # behaving like a naive datetime for arithmetic/comparison purposes.
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware.")
        return value
