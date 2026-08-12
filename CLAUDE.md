# Durable project rules for CloudOps Guard

These rules govern how work on this repository should be approached. They apply
regardless of which milestone is currently in progress.

## Scope discipline

- Work incrementally, one milestone at a time. v0.1.0, the Kubernetes audit MVP, is
  released (see README.md). The current milestone is v0.2.0: a read-only,
  single-project GitLab CI/CD Audit MVP — see
  `docs/milestones/v0.2.0-gitlab-audit.md` for its objective, command interface,
  checks, invariants and non-goals. GitLab implementation is in progress: the HTTP
  client foundation, a normalized instance/project/protected-branch collector, and
  the protected-default-branch checks (`GL-BR-001` through `GL-BR-003`) exist; the
  remaining project-setting checks, CI Lint/image inspection, GitLab
  evaluator/report integration, and CLI integration do not yet. Do not start
  AKS/EKS-specific code, a database, SaaS multi-tenancy, authentication, a web
  dashboard, billing or LLM integration until a milestone explicitly calls for it.
- Do not introduce a database, web framework, cloud SDK (beyond the official
  Kubernetes client) or AI/LLM API until the relevant milestone requires it.
- Explain important architectural changes before making them — don't silently restructure
  the collector/checks/engine/reports separation.

## Read-only invariant

- CloudOps Guard is a read-only auditing tool. It must never modify, create, patch or
  delete any resource in an audited system.
- Never retrieve or log Kubernetes Secret contents.
- Never retrieve or log ConfigMap contents.
- Never collect container environment variable values or application logs.
- Never print kubeconfig credentials, tokens or certificate material — including in
  exception messages.

## GitLab read-only and privacy invariants (v0.2.0+)

These apply once GitLab audit implementation begins; see
`docs/milestones/v0.2.0-gitlab-audit.md` §D for full rationale.

- Use read-only GitLab API operations only.
- Never call project, group, or instance CI/CD variables endpoints.
- Never collect or report job traces, logs, artifacts, credentials, or tokens.
- Never persist raw or merged CI YAML in reports.
- Never reproduce CI scripts or variable values in findings or error messages.
- If CI configuration must be processed to evaluate a check, process it only in
  memory and retain only the normalized, non-sensitive fields that check needs.
- Never log authentication headers.
- Sanitize remote API errors and untrusted response content before they reach a
  report or the terminal.
- A failure to access required information must not silently produce a partial clean
  report — fail the audit rather than under-report.
- The GitLab access token is read only from the `CLOUDOPS_GUARD_GITLAB_TOKEN`
  environment variable; it must never be accepted as a CLI option or read from a
  configuration file.
- An approved read-only endpoint may return unrelated sensitive fields that GitLab
  provides automatically. Such fields may exist only transiently during response
  parsing and must be discarded immediately at the normalization boundary. They must
  never be retained, logged, persisted, reported, cached, or included in errors.

## Testing

- Add tests for every check (existing and new). Tests must not require a live cluster —
  use the injectable Kubernetes client and representative `kubernetes.client` model
  objects (see `tests/fixtures/builders.py`), not hand-rolled dicts standing in for API
  responses.
- Tests should exercise real project code, not reimplement its logic to check against
  itself.

## Dependencies

- Avoid unnecessary dependencies. The dependency set (kubernetes, typer, pydantic,
  jinja2, pyyaml, pytest, ruff) is deliberately modest — justify any addition.

## Git

- Do not make commits or push changes unless explicitly requested by the user.
