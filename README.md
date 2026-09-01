# cloudops-guard

Cloud reliability, security and cost control for growing software companies.

CloudOps Guard is (eventually) a commercial, **read-only** auditing platform covering
Kubernetes, GitLab CI/CD, cloud reliability, security and cost optimization. This
repository currently implements the first two milestones: a **Kubernetes audit MVP**
and a **GitLab CI/CD audit MVP**, each its own CLI subcommand and report set.

## Current scope

- Two CLI commands: `cloudops-guard audit kubernetes` and `cloudops-guard audit gitlab`.
- **Kubernetes**: read-only collection of namespace, pod, container, deployment and
  replicaset metadata via the official Kubernetes Python client. `--namespace` runs
  entirely on namespace-scoped APIs (see [RBAC permissions](#rbac-permissions-least-privilege)).
  Five deterministic container checks (missing CPU/memory requests and limits, mutable
  image tags), plus a per-container excessive-restart check.
- **GitLab**: read-only, single-project audit of protected-branch rules, merge-request
  and CI/CD project settings, and CI job/service container image references, via the
  GitLab REST API v4 and CI Lint API. Eleven deterministic checks (see
  [GitLab checks](#gitlab-checks) below).
- Each platform writes its own `report.json` and `report.html` into the `--output`
  directory, and prints a terminal summary of findings by severity. GitLab reports
  contain no Kubernetes-only fields, and vice versa.

## Non-goals (for these milestones)

The following are explicitly **not** implemented yet:

- AKS/EKS-specific integrations.
- A database or any persistent storage.
- SaaS multi-tenancy.
- Authentication or authorization.
- A web dashboard.
- Billing.
- LLM integration.
- Remediation (automatic or suggested fixes are not applied; findings only note whether
  a check *could* eventually be auto-remediated).
- GitLab: multi-project, group-wide, or namespace-wide scoping (one project per audit
  run); pipeline/job execution or simulation; CI/CD variables, job logs, traces, or
  artifacts.

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
uv sync --locked --extra dev   # installs exactly what's in uv.lock, including dev dependencies
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

## Kubernetes CLI usage

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

## GitLab CLI usage

```bash
# Never put the token on the command line, in a config file, or in shell history.
# `read -s` reads it with terminal echo disabled instead.
read -s -p 'GitLab token: ' CLOUDOPS_GUARD_GITLAB_TOKEN
export CLOUDOPS_GUARD_GITLAB_TOKEN
echo

cloudops-guard audit gitlab \
  --gitlab-url https://gitlab.example.com \
  --project group/subgroup/project \
  --job-timeout-threshold-seconds 3600 \
  --output ./reports/gitlab

# Unset the token once you're done — it's no longer needed after the audit runs.
unset CLOUDOPS_GUARD_GITLAB_TOKEN
```

All four flags are required; there is no `--token`, `--config`, or TLS-skip option.

- **Token**: read only from the `CLOUDOPS_GUARD_GITLAB_TOKEN` environment variable —
  CloudOps Guard never accepts it as a CLI argument or a configuration-file value, so
  it never appears in a CloudOps Guard command line or a committed CloudOps Guard
  configuration file. This does not guarantee the variable is safe from every
  operating-system- or user-level exposure path outside CloudOps Guard's control (for
  example, a shell that logs exported environment variables, or a process inspection
  tool) — protecting the environment variable itself remains the operator's
  responsibility. The `read -s` example above hides the value from terminal echo
  while typing; unset the variable once the audit no longer needs it. Required scope:
  `read_api`. The broader `api` scope is never required.
- `--gitlab-url`: the instance's base URL. Use `https://` for any real deployment —
  a plain-HTTP URL was exercised only as a controlled local acceptance-test condition
  against a loopback instance, not as a supported production configuration.
- `--project`: the target project, as either a numeric project ID or its canonical
  full path (e.g. `group/subgroup/project`). Audits exactly one project per run; there
  is no group-wide or namespace-wide scope.
- `--job-timeout-threshold-seconds`: a required positive-integer threshold for
  `GL-REL-001` — no product-level default is guessed. A finding is produced only when
  the project's configured job timeout **strictly exceeds** this threshold; a timeout
  equal to or below it passes.
- `--output`: directory to write `report.json` and `report.html` into, matching the
  `audit kubernetes` convention.

The command exits non-zero if input validation, token setup, collection, or report
generation fails, and `0` regardless of how many findings are reported. A project with
a missing or invalid `.gitlab-ci.yml` currently produces a sanitized collection
failure: collection fails before report generation is ever reached, so the failed run
writes no new report files. Any complete `report.json`/`report.html` already present
in the output directory from an earlier successful run is not removed and may remain
there. This failure must not be interpreted as an empty or clean audit.

### Minimum access

- **Token scope**: `read_api` (tested and required; `api` is never needed).
- **Effective project role**: **Maintainer** is the documented minimum; **Owner**
  remains sufficient. This has been confirmed through controlled live acceptance
  testing on GitLab.com's current hosted version (Maintainer tested via a project
  **service account**) and on self-managed **GitLab Community Edition 18.4.6**
  (Maintainer tested via an ordinary, non-service-account internal user account) —
  see `docs/milestones/v0.2.0-gitlab-audit.md` for the full, precisely bounded
  evidence. **Not yet tested**: any self-managed GitLab release/version other than
  CE 18.4.6; GitLab Enterprise Edition or its Premium/Ultimate tiers; a
  human-operated Maintainer account on GitLab.com; and any token type other than a
  legacy personal access token (project/group access tokens, OAuth tokens,
  fine-grained PATs, administrator tokens).
- **Supported instances**: GitLab.com (current hosted version) and self-managed
  GitLab 18.4+. Live acceptance evidence for self-managed specifically covers CE
  18.4.6.

### GitLab checks

| Check ID      | What it flags                                                          | Severity |
| ------------- | ------------------------------------------------------------------------ | -------- |
| `GL-BR-001`   | Default branch is not protected                                          | High     |
| `GL-BR-002`   | Force-push is allowed on the default branch                              | High     |
| `GL-BR-003`   | Developers can push directly to the default branch                       | Medium   |
| `GL-MR-001`   | Successful pipelines are not required before merge                       | Medium   |
| `GL-SEC-001`  | CI/CD pipeline details have broad visibility                             | High     |
| `GL-SEC-002`  | CI job tokens are permitted to push to the repository                    | High     |
| `GL-SEC-003`  | Pipeline-variable override permissions are more permissive than Maintainer | High   |
| `GL-COST-001` | Redundant pipelines are not automatically cancelled                      | Low      |
| `GL-COST-002` | Git clone depth is unlimited                                             | Low      |
| `GL-REL-001`  | Project job timeout exceeds a configurable threshold                     | Medium   |
| `GL-CI-001`   | CI job or service container image uses a mutable tag or no tag           | High     |

The GitLab audit is read-only: it never executes pipelines or jobs, never mutates the
repository, and never calls a CI/CD variables, job-log, trace, artifact, or
runner-management endpoint. See `docs/milestones/v0.2.0-gitlab-audit.md` for each
check's full condition, evidence, and rationale, and for the detailed controlled
acceptance-test records (GitLab.com and self-managed GitLab CE 18.4.6) behind the
minimum-access conclusions above.

## Manual acceptance test with a local `kind` cluster

[`kind`](https://kind.sigs.k8s.io/) runs a disposable Kubernetes cluster in Docker, so
you can exercise the CLI end-to-end without touching a real cluster. This procedure
requires Docker, `kind` and `kubectl`. It is a manual test: it is not currently run by
CI, and the disposable cluster it creates is intended only for local testing.

### Acceptance test status

This procedure was successfully executed on **July 28, 2026**, using:

```text
Hardware: Apple M4, 24 GB RAM
Architecture: ARM64
Operating system: macOS Tahoe 26.5.2
Docker Desktop: 4.84.0
Docker Engine: 29.6.2
kind: v0.32.0
kubectl client: v1.36.3
Kubernetes node: v1.36.1
```

`report.json` and `report.html` were both generated for every **successful** audit run
below. The deliberately denied cluster-wide restricted audit (see below) failed during
collection, as expected, and did not generate reports. Only JSON findings were parsed
and compared programmatically (see the comparison command in step 10 of the
procedure); `report.html` rendering was **not** visually inspected as part of this test.

#### Fresh kind cluster audit

A cluster-wide audit run against a newly created cluster, before any demo namespace or
Deployments were added, found:

```text
Critical: 0
High: 6
Medium: 14
Low: 0
Total: 20
```

These were all findings against kind's default system workloads (e.g. CoreDNS,
kube-proxy) rather than anything created by this test. One note on interpreting this:
kube-proxy runs as a DaemonSet, and DaemonSet collection/ownership resolution is not
yet implemented, so its pods are currently evaluated individually at the Pod level
rather than deduplicated the way Deployment-managed pods are. This count is specific
to the kind/Kubernetes node version above and the workloads kind happens to ship by
default — it is not asserted to
reproduce exactly on other versions.

#### Controlled namespace audit

Three Deployments were created in `guard-demo-ns`:

- `good`: pinned version tag with CPU/memory requests and limits set.
- `under-resourced`: pinned version tag, no requests or limits.
- `latest-tag`: `nginx:latest`, no requests or limits.

Auditing that namespace (administrator context) found:

```text
Critical: 0
High: 3
Medium: 6
Low: 0
Total: 9
```

By check ID:

```text
K8S-IMG-001: 1
K8S-RES-001: 2
K8S-RES-002: 2
K8S-RES-003: 2
K8S-RES-004: 2
```

By resource:

```text
good: 0
under-resourced: 4
latest-tag: 5
```

#### Restricted service-account audit

Using the namespace-scoped RBAC example (see [RBAC
permissions](#rbac-permissions-least-privilege)), the `cloudops-guard-auditor` service
account in `guard-demo-ns` was confirmed, via `kubectl auth can-i`, to:

- Be able to list Pods, Deployments and ReplicaSets in `guard-demo-ns`.
- Not be able to list namespaces, Secrets or ConfigMaps.
- Not be able to create Pods or delete Deployments.

Auditing `guard-demo-ns` with this restricted identity succeeded and produced findings
identical to the administrator-context run above, compared programmatically from the
two `report.json` files:

```text
Admin findings: 9
Restricted findings: 9
Findings identical: True
```

A deliberately attempted cluster-wide audit using the same restricted context (i.e.
omitting `--namespace`, which requires `list` on namespaces cluster-wide) failed
correctly rather than partially succeeding or crashing:

```text
Collection failed: List namespaces failed (HTTP 403).
Exit code: 1
```

### Reproducing this test

The steps below use a consistent cluster name and context throughout:

```text
kind cluster name:  cloudops-guard
kubectl context:    kind-cloudops-guard
test namespace:     guard-demo-ns
restricted context: guard-demo-restricted
```

They use `uv run cloudops-guard` rather than assuming the CLI is installed globally —
substitute a plain `cloudops-guard` invocation if you've installed it some other way.

**1. Verify tool versions** (install via e.g. `brew install kind kubectl` first if needed):

```bash
docker --version
kind version
kubectl version --client
```

**2. Create the cluster:**

```bash
kind create cluster --name cloudops-guard   # adds kubeconfig context "kind-cloudops-guard"
```

**3. Create the namespace and three Deployments:**

```bash
kubectl --context kind-cloudops-guard create namespace guard-demo-ns

kubectl --context kind-cloudops-guard -n guard-demo-ns \
  create deployment good --image=nginx:1.27.0
kubectl --context kind-cloudops-guard -n guard-demo-ns \
  set resources deployment/good \
  --requests=cpu=100m,memory=64Mi --limits=cpu=200m,memory=128Mi

kubectl --context kind-cloudops-guard -n guard-demo-ns \
  create deployment under-resourced --image=nginx:1.27.0

kubectl --context kind-cloudops-guard -n guard-demo-ns \
  create deployment latest-tag --image=nginx:latest
```

**4. Wait for all three to finish rolling out:**

```bash
kubectl --context kind-cloudops-guard -n guard-demo-ns \
  rollout status deployment/good --timeout=120s
kubectl --context kind-cloudops-guard -n guard-demo-ns \
  rollout status deployment/under-resourced --timeout=120s
kubectl --context kind-cloudops-guard -n guard-demo-ns \
  rollout status deployment/latest-tag --timeout=120s
```

**5. Audit the namespace using the administrator context:**

```bash
uv run cloudops-guard audit kubernetes \
  --context kind-cloudops-guard \
  --namespace guard-demo-ns \
  --output /tmp/cloudops-guard-admin-namespace
```

**6. Apply the namespace-scoped RBAC example.** The checked-in manifests under
`examples/rbac/namespace-scoped/` hardcode `namespace: my-namespace`; passing `-n
guard-demo-ns` to `kubectl apply` would **not** retarget that (an explicit
`metadata.namespace` always wins), so substitute it in memory instead, without editing
the checked-in files:

```bash
for manifest in examples/rbac/namespace-scoped/*.yaml; do
  sed 's/my-namespace/guard-demo-ns/g' "$manifest" |
    kubectl --context kind-cloudops-guard apply -f -
done
```

**7. Check the permission boundary** the RBAC example is meant to enforce:

```bash
SA="system:serviceaccount:guard-demo-ns:cloudops-guard-auditor"

kubectl --context kind-cloudops-guard auth can-i list pods --as="$SA" -n guard-demo-ns
kubectl --context kind-cloudops-guard auth can-i list deployments --as="$SA" -n guard-demo-ns
kubectl --context kind-cloudops-guard auth can-i list replicasets --as="$SA" -n guard-demo-ns
# all three above should print "yes"

kubectl --context kind-cloudops-guard auth can-i list namespaces --as="$SA"
kubectl --context kind-cloudops-guard auth can-i list secrets --as="$SA" -n guard-demo-ns
kubectl --context kind-cloudops-guard auth can-i list configmaps --as="$SA" -n guard-demo-ns
kubectl --context kind-cloudops-guard auth can-i create pods --as="$SA" -n guard-demo-ns
kubectl --context kind-cloudops-guard auth can-i delete deployments --as="$SA" -n guard-demo-ns
# all five above should print "no"
```

**8. Build a temporary kubeconfig for the restricted identity.** Do not add this
service account's token to your normal `~/.kube/config`. Tighten the umask first so the
temporary file is created readable only by you:

```bash
TEST_KUBECONFIG="/tmp/cloudops-guard-restricted.kubeconfig"
ORIGINAL_UMASK=$(umask)
umask 077

kubectl --context kind-cloudops-guard \
  config view --raw --minify > "$TEST_KUBECONFIG"

TOKEN=$(kubectl --context kind-cloudops-guard \
  -n guard-demo-ns \
  create token cloudops-guard-auditor \
  --duration=1h)

kubectl --kubeconfig "$TEST_KUBECONFIG" config set-credentials cloudops-guard-auditor \
  --token="$TOKEN"
unset TOKEN

kubectl --kubeconfig "$TEST_KUBECONFIG" config set-context guard-demo-restricted \
  --cluster=kind-cloudops-guard \
  --user=cloudops-guard-auditor \
  --namespace=guard-demo-ns
```

**9. Audit the namespace using the restricted context** (via the temporary kubeconfig
only — the real `~/.kube/config` is never touched):

```bash
KUBECONFIG="$TEST_KUBECONFIG" uv run cloudops-guard audit kubernetes \
  --context guard-demo-restricted \
  --namespace guard-demo-ns \
  --output /tmp/cloudops-guard-restricted-namespace
```

**10. Compare the administrator and restricted findings programmatically** (this reads
the two `report.json` files; it never prints the token or the kubeconfig contents):

```bash
python3 - /tmp/cloudops-guard-admin-namespace/report.json \
  /tmp/cloudops-guard-restricted-namespace/report.json <<'PY'
import json
import sys


def normalize(findings):
    return sorted(
        (
            f["check_id"],
            f["severity"],
            f["namespace"],
            f["resource_kind"],
            f["resource_name"],
            f.get("container_name"),
        )
        for f in findings
    )


with open(sys.argv[1]) as fh:
    admin_report = json.load(fh)
with open(sys.argv[2]) as fh:
    restricted_report = json.load(fh)

admin_findings = normalize(admin_report["findings"])
restricted_findings = normalize(restricted_report["findings"])

print(f"Admin findings: {len(admin_findings)}")
print(f"Restricted findings: {len(restricted_findings)}")
print(f"Findings identical: {admin_findings == restricted_findings}")
PY
```

**11. Confirm a cluster-wide audit with the restricted context fails as expected**
(omitting `--namespace` requires cluster-wide `list` on namespaces, which this identity
does not have):

```bash
KUBECONFIG="$TEST_KUBECONFIG" uv run cloudops-guard audit kubernetes \
  --context guard-demo-restricted \
  --output /tmp/cloudops-guard-restricted-cluster-wide
RESTRICTED_EXIT=$?
echo "Exit code: $RESTRICTED_EXIT"
```

**12. Clean up**, removing the generated report directories and temporary kubeconfig,
restoring your original umask, and deleting the cluster:

```bash
rm -rf /tmp/cloudops-guard-admin-namespace
rm -rf /tmp/cloudops-guard-restricted-namespace
rm -rf /tmp/cloudops-guard-restricted-cluster-wide
rm -f "$TEST_KUBECONFIG"
unset TOKEN TEST_KUBECONFIG SA RESTRICTED_EXIT
umask "$ORIGINAL_UMASK"
unset ORIGINAL_UMASK

kind delete cluster --name cloudops-guard
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

CloudOps Guard is **read-only** by design on every platform it audits.

### Kubernetes

- It never modifies, creates or deletes any Kubernetes resource — it only calls `list`
  APIs.
- It never reads Kubernetes **Secret** contents.
- It never reads **ConfigMap** contents.
- It never collects container **environment variable values**.
- It never collects **application logs**.
- It never prints kubeconfig credentials, tokens, certificate material or other
  locally-sensitive detail — including in error messages. Collector errors retain only
  safe context: which operation failed, and the HTTP status code when one is available.
  The Kubernetes API server's raw error `reason`, response body and response headers
  are deliberately never displayed, since that text is server- (or proxy-) supplied and
  not trusted content. Expected failures (missing kubeconfig, unknown context, API
  errors, network/TLS problems, an unusable RBAC grant, etc.) produce a concise
  one-line CLI message and exit with a non-zero status — not a raw traceback.
- The Kubernetes API client is injectable, so the test suite never needs a live
  cluster and never talks to a real API server.

### GitLab

- It never calls a write GitLab API operation, executes a pipeline or job, or mutates
  the repository — it only uses read-only endpoints (an explicit, narrow allowlist,
  including `GET /projects/:id`, protected-branch listing, and the CI Lint API).
- It never calls a project, group, or instance **CI/CD variables** endpoint.
- It never collects or reports job traces, logs, artifacts, credentials, or tokens.
- It never persists raw or merged CI YAML in a report; CI configuration is processed
  only in memory, retaining just the normalized, non-sensitive fields a check needs.
- It never prints the `CLOUDOPS_GUARD_GITLAB_TOKEN` value or an authentication header,
  including in error messages.
- `GET /projects/:id` is the one approved endpoint GitLab does not let CloudOps Guard
  field-filter, so it can transiently return unrelated sensitive fields (notably
  `runners_token`); these are discarded immediately at normalization and never
  retained, logged, or reported. See `docs/milestones/v0.2.0-gitlab-audit.md` for the
  full rationale.
- A failure to access required information fails the audit (non-zero exit) rather than
  silently producing a partial report that looks clean.

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

Running `audit kubernetes --output <dir>` or `audit gitlab --output <dir>` writes two
files into `<dir>` (created if it doesn't exist), each platform-specific and with no
fields from the other platform:

- `report.json` — the full audit report (findings, summary counts, metadata) as JSON.
- `report.html` — a static, dependency-free HTML report. No JavaScript, no external
  resources (fonts, scripts, stylesheets) — it renders fully offline in any browser.

## Roadmap

Not implemented yet, planned for future milestones:

- AKS/EKS-specific cloud integrations.
- Broader cloud cost analysis.
- A persistent, multi-tenant web dashboard.

A versioned report-ingestion API and a command-line uploader are specified
in a design document (v0.4.0) — see
`docs/milestones/v0.4.0-ingestion-api.md`. Phase 4A is documentation and
contract design only. Phase 4B (uncommitted, pending review) has since
added local, in-memory reference storage/token interfaces
(`src/cloudops_guard/ingestion/`) — no HTTP API, no authentication, no
uploader, no production storage, and no deployment exist, and nothing is
deployed.
