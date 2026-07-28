# cloudops-guard

Cloud reliability, security and cost control for growing software companies.

CloudOps Guard is (eventually) a commercial, **read-only** auditing platform covering
Kubernetes, GitLab CI/CD, cloud reliability, security and cost optimization. This
repository currently implements the first milestone only: a **Kubernetes audit MVP**.

## Current MVP scope

- A single CLI command: `cloudops-guard audit kubernetes`.
- Read-only collection of namespace, pod, container and deployment metadata via the
  official Kubernetes Python client.
- Five deterministic container checks (missing CPU/memory requests and limits, mutable
  image tags), plus a pod excessive-restart check.
- `report.json` and `report.html` output, and a terminal summary of findings by
  severity.

## Non-goals (for this milestone)

The following are explicitly **not** implemented yet:

- GitLab CI/CD auditing.
- AKS/EKS-specific integrations.
- A database or any persistent storage.
- SaaS multi-tenancy.
- Authentication or authorization.
- A web dashboard.
- Billing.
- LLM integration.
- Remediation (automatic or suggested fixes are not applied; findings only note whether
  a check *could* eventually be auto-remediated).

## Python setup

Requires Python 3.12+.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Installing dependencies

```bash
pip install -e ".[dev]"
```

This installs the package in editable mode plus development dependencies (pytest,
ruff).

## CLI usage

```bash
# Audit every namespace in a context
cloudops-guard audit kubernetes --context my-cluster --output ./reports

# Restrict to one namespace
cloudops-guard audit kubernetes --context my-cluster --output ./reports --namespace payments

# Use a config file and a custom restart threshold
cloudops-guard audit kubernetes --context my-cluster --output ./reports \
  --config sample-config.yaml --restart-threshold 10
```

Command-line flags always override values set in a `--config` YAML file. See
[`sample-config.yaml`](sample-config.yaml) for the supported keys.

The command exits non-zero if collection from the Kubernetes API or report generation
fails. It exits `0` regardless of how many findings are reported — findings are not
failures, they are the audit result.

## Creating a local `kind` cluster for manual testing

[`kind`](https://kind.sigs.k8s.io/) runs a disposable Kubernetes cluster in Docker, so
you can exercise the CLI without touching a real cluster.

```bash
# Install kind and kubectl (macOS example)
brew install kind kubectl

# Create a cluster (this also adds a "kind-guard-demo" context to your kubeconfig)
kind create cluster --name guard-demo

# Deploy something worth auditing, e.g. a Deployment with no resource limits
kubectl create deployment demo --image=nginx:latest

# Run the audit against it
cloudops-guard audit kubernetes --context kind-guard-demo --output ./reports

# Tear down when done
kind delete cluster --name guard-demo
```

## Selecting a Kubernetes context

CloudOps Guard never reads your "current context" implicitly — you must always pass
`--context` explicitly:

```bash
kubectl config get-contexts   # list available contexts
cloudops-guard audit kubernetes --context <context-name> --output ./reports
```

The tool respects the `KUBECONFIG` environment variable and otherwise falls back to
`~/.kube/config`, exactly like `kubectl`.

## Security and privacy behaviour

CloudOps Guard is **read-only** by design:

- It never modifies, creates or deletes any Kubernetes resource — it only calls `list`
  APIs.
- It never reads Kubernetes **Secret** contents.
- It never reads **ConfigMap** contents.
- It never collects container **environment variable values**.
- It never collects **application logs**.
- It never prints kubeconfig credentials, tokens or certificates. Errors from the
  Kubernetes client are surfaced with status codes and reasons only, never raw
  credential material.
- The Kubernetes API client is injectable, so the test suite never needs a live
  cluster and never talks to a real API server.

## Running tests, linting and formatting

```bash
pytest
ruff check .
ruff format --check .
```

To auto-fix formatting/lint issues locally:

```bash
ruff check . --fix
ruff format .
```

## Expected output files

Running `audit kubernetes --output <dir>` writes two files into `<dir>` (created if it
doesn't exist):

- `report.json` — the full `AuditReport` (findings, summary counts, metadata) as JSON.
- `report.html` — a static, dependency-free HTML report. No JavaScript, no external
  resources (fonts, scripts, stylesheets) — it renders fully offline in any browser.

## Roadmap

Not implemented yet, planned for future milestones:

- GitLab CI/CD pipeline auditing.
- Cloud cost analysis.
- A persistent, multi-tenant web dashboard.
