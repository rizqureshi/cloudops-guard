# cloudops-guard

Cloud reliability, security and cost control for growing software companies.

CloudOps Guard is (eventually) a commercial, **read-only** auditing platform covering
Kubernetes, GitLab CI/CD, cloud reliability, security and cost optimization. This
repository currently implements the first milestone only: a **Kubernetes audit MVP**.

## Current MVP scope

- A single CLI command: `cloudops-guard audit kubernetes`.
- Read-only collection of namespace, pod, container, deployment and replicaset
  metadata via the official Kubernetes Python client. `--namespace` runs entirely on
  namespace-scoped APIs (see [RBAC permissions](#rbac-permissions-least-privilege)).
- Five deterministic container checks (missing CPU/memory requests and limits, mutable
  image tags), plus a per-container excessive-restart check.
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

### With `uv` (recommended, reproducible)

This repo commits a [`uv.lock`](uv.lock) so installs are reproducible across machines
and CI. Install [uv](https://docs.astral.sh/uv/) once, then:

```bash
uv sync --locked   # installs exactly what's in uv.lock, including dev dependencies
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

`uv sync --locked` fails instead of silently re-resolving if `pyproject.toml` and
`uv.lock` have drifted — regenerate the lock with `uv lock` if you intentionally
changed a dependency.

### With `pip`

```bash
pip install -e ".[dev]"
```

This installs the package in editable mode plus development dependencies (pytest,
ruff). Standard wheel/sdist packaging still works normally either way:

```bash
python -m build   # requires the `build` package; produces dist/*.whl and dist/*.tar.gz
```

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

Command-line flags always override values set in a `--config` YAML file — both are
validated by the same rules before anything is collected. See
[`sample-config.yaml`](sample-config.yaml) for the supported keys.

Validation rules (identical whether the value comes from `--config` or a CLI flag):

- `restart_threshold` must be an integer `>= 1` (a threshold of `0` would flag a
  perfectly healthy pod with zero restarts).
- `namespace` is stripped of surrounding whitespace and must not be empty.
- An unknown key in the YAML config file is rejected rather than silently ignored.

Invalid values exit with code `1` and a message identifying the offending field. Note:
a CLI flag of the *wrong type entirely* (e.g. `--restart-threshold notanumber`) is
rejected by Typer's own argument parsing before CloudOps Guard's validation runs, using
Typer/Click's standard exit code `2` — this is ordinary CLI convention (comparable to
how `git`, `docker`, etc. handle malformed flag values) and is distinct from the `1`
used for validation failures on well-typed-but-out-of-range values (e.g. `0`, `-1`,
empty namespace) and for collection/report-generation failures.

The command exits non-zero if configuration validation, collection from the Kubernetes
API, or report generation fails. It exits `0` regardless of how many findings are
reported — findings are not failures, they are the audit result.

## Kubernetes checks

| Check ID     | What it flags                                    | Severity |
| ------------ | ------------------------------------------------- | -------- |
| `K8S-RES-001` | Container has no CPU request                      | Medium   |
| `K8S-RES-002` | Container has no memory request                   | Medium   |
| `K8S-RES-003` | Container has no CPU limit                        | Medium   |
| `K8S-RES-004` | Container has no memory limit                     | High     |
| `K8S-IMG-001` | Container image uses a mutable tag or no tag       | High     |
| `K8S-REL-001` | A single container's restart count meets/exceeds the threshold | High |

Two behaviours worth calling out explicitly:

- **`K8S-IMG-001` and tag immutability**: a specific version tag (e.g. `app:1.4.2`) is
  flagged as fine — it's a real improvement over `latest` for reproducibility — but a
  registry tag can still be overwritten and re-pushed later. Only a digest reference
  (`app@sha256:...`) is truly content-addressed and immutable. The finding's wording
  reflects this: it recommends a version tag at minimum and a digest for the strongest
  guarantee, without claiming a version tag alone is immutable.
- **`K8S-REL-001` is per container, and cumulative**: each container in a pod is
  evaluated independently — a pod is never flagged just because several containers'
  small restart counts add up to the threshold. The count is whatever Kubernetes
  reports as cumulative for the pod's current lifetime; this is not a time-windowed
  restart *rate*, so a pod that's been running for months will naturally accumulate
  more restarts than one that started an hour ago.

Deployment-managed pods are evaluated once, at the Deployment (via real
Pod→ReplicaSet→Deployment owner references, not name matching) — this avoids one
finding per replica. Restart counts are still evaluated per pod, since that's runtime
data rather than spec data.

## Manual acceptance test with a local `kind` cluster

[`kind`](https://kind.sigs.k8s.io/) runs a disposable Kubernetes cluster in Docker, so
you can exercise the CLI end-to-end without touching a real cluster. This procedure
requires Docker, `kind` and `kubectl`; if any are unavailable, this is a manual
procedure to run yourself rather than something CI performs automatically.

```bash
# 1. Install kind and kubectl (macOS example) and create a cluster
brew install kind kubectl
kind create cluster --name guard-demo   # adds a "kind-guard-demo" kubeconfig context

# 2. Deploy one compliant Deployment, one missing resources, and one on `latest`
kubectl create namespace guard-demo-ns
kubectl -n guard-demo-ns create deployment good \
  --image=nginx:1.27.0
kubectl -n guard-demo-ns set resources deployment/good \
  --requests=cpu=100m,memory=64Mi --limits=cpu=200m,memory=128Mi
kubectl -n guard-demo-ns create deployment under-resourced --image=nginx:1.27.0
kubectl -n guard-demo-ns create deployment latest-tag --image=nginx:latest

# 3. Run a cluster-wide audit
cloudops-guard audit kubernetes --context kind-guard-demo --output ./reports-cluster-wide

# 4. Apply the namespace-scoped RBAC example and get a token for it
kubectl apply -f examples/rbac/namespace-scoped/ -n guard-demo-ns \
  --dry-run=client -o yaml | sed 's/my-namespace/guard-demo-ns/' | kubectl apply -f -
TOKEN=$(kubectl create token cloudops-guard-auditor -n guard-demo-ns --duration=1h)

# 5. Build a kubeconfig context for that restricted service account and audit with it
kubectl config set-credentials guard-demo-sa --token="$TOKEN"
kubectl config set-context guard-demo-restricted \
  --cluster=kind-guard-demo --user=guard-demo-sa --namespace=guard-demo-ns
cloudops-guard audit kubernetes --context guard-demo-restricted \
  --namespace guard-demo-ns --output ./reports-namespace-scoped

# 6. Confirm both runs produced JSON + HTML reports
ls reports-cluster-wide reports-namespace-scoped

# 7. Confirm the restricted account genuinely cannot modify anything
kubectl auth can-i create pods --as=system:serviceaccount:guard-demo-ns:cloudops-guard-auditor -n guard-demo-ns   # expects "no"
kubectl auth can-i delete deployments --as=system:serviceaccount:guard-demo-ns:cloudops-guard-auditor -n guard-demo-ns   # expects "no"

# 8. Tear down
kind delete cluster --name guard-demo
```

Expected results: the cluster-wide report should show `under-resourced` and
`latest-tag` findings (missing CPU/memory requests+limits, and the mutable-tag
finding, respectively) but not `good`; the namespace-scoped run should produce an
equivalent report restricted to `guard-demo-ns` while succeeding without any
cluster-wide `list namespaces` permission; and both `kubectl auth can-i` checks in
step 7 must return `no`.

This procedure was **not run** as part of this milestone — Docker/`kind`/`kubectl`
were not available in the development environment used. Treat it as a documented,
reproducible manual test rather than a verified result until someone runs it.

## Selecting a Kubernetes context

CloudOps Guard never reads your "current context" implicitly — you must always pass
`--context` explicitly:

```bash
kubectl config get-contexts   # list available contexts
cloudops-guard audit kubernetes --context <context-name> --output ./reports
```

The tool respects the `KUBECONFIG` environment variable and otherwise falls back to
`~/.kube/config`, exactly like `kubectl`.

## RBAC permissions (least privilege)

CloudOps Guard only ever calls Kubernetes `list` APIs — never `get`, `watch`, or any
write verb. Which APIs it calls depends on whether `--namespace` is given:

**One-namespace auditing** (`--namespace <ns>`) never calls a cluster-wide API. It only
needs a namespace-scoped `Role` granting `list` on `pods`, `deployments` and
`replicasets` *within that namespace* — no permission to list namespaces cluster-wide is
required. See [`examples/rbac/namespace-scoped/`](examples/rbac/namespace-scoped/).

```yaml
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["list"]
```

**Cluster-wide auditing** (no `--namespace`) additionally needs cluster-scoped `list` on
`namespaces`, and `list` on `pods`/`deployments`/`replicasets` across all namespaces —
a `ClusterRole` bound cluster-wide. See
[`examples/rbac/cluster-wide/`](examples/rbac/cluster-wide/).

```yaml
rules:
  - apiGroups: [""]
    resources: ["namespaces", "pods"]
    verbs: ["list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["list"]
```

Neither permission set includes `secrets`, `configmaps`, or any resource beyond what's
listed — consistent with the read-only, no-sensitive-data design described below.

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

(or `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` if using uv)

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
