# Durable project rules for CloudOps Guard

These rules govern how work on this repository should be approached. They apply
regardless of which milestone is currently in progress.

## Scope discipline

- Work incrementally, one milestone at a time. The current milestone is the Kubernetes
  audit MVP (see README.md). Do not start GitLab integration, AKS/EKS-specific code, a
  database, SaaS multi-tenancy, authentication, a web dashboard, billing or LLM
  integration until a milestone explicitly calls for it.
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
