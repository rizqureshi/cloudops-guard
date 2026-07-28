"""Pydantic models shared across collection, evaluation and reporting.

These models are the normalized internal representation of Kubernetes state.
Checks and report generation depend only on these models, never on the
Kubernetes API client types, so evaluation and reporting can be tested and
reasoned about without a live cluster.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

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
