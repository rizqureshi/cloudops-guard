# Example RBAC manifests

These manifests are illustrative starting points, not something CloudOps Guard applies
automatically — the tool never creates or modifies cluster resources. Apply them
yourself with `kubectl apply -f`, editing the placeholder namespace/names first.

- `namespace-scoped/` — the minimum permissions needed to audit a single namespace
  with `cloudops-guard audit kubernetes --namespace <ns>`. No cluster-wide
  permissions required.
- `cluster-wide/` — the minimum permissions needed to audit every namespace with
  `cloudops-guard audit kubernetes` (no `--namespace`).

Both grant only the `list` verb, only on the resource kinds the collector actually
reads (namespaces, pods, deployments, replicasets) — never Secrets or ConfigMaps, and
never a verb that could modify anything.

To generate a kubeconfig context for the namespace-scoped service account (adjust
names/namespace to match what you applied):

```bash
kubectl apply -f namespace-scoped/
SA_NAMESPACE=my-namespace
SA_NAME=cloudops-guard-auditor
SECRET=$(kubectl create token "$SA_NAME" -n "$SA_NAMESPACE" --duration=1h)
# Use $SECRET as a bearer token, or use `kubectl config set-credentials` /
# your cluster's usual service-account kubeconfig workflow.
```
