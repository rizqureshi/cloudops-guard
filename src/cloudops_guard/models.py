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
from typing import Literal

from pydantic import BaseModel, Field


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
