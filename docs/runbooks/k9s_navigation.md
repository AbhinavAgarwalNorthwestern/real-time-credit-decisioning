# Runbook — k9s Navigation

How to find anything in the cluster fast via k9s. The repo ships its own
k9s config files at `k9s/` — symlink them into `~/.config/k9s/` once and
the hotkeys + aliases + views below all work.

## One-time setup

```bash
# Create the k9s config dir if it doesn't exist
mkdir -p ~/.config/k9s

# Symlink the project's config files into it
ln -sf "$PWD/k9s/aliases.yaml"  ~/.config/k9s/aliases.yaml
ln -sf "$PWD/k9s/hotkeys.yaml"  ~/.config/k9s/hotkeys.yaml
ln -sf "$PWD/k9s/views.yaml"    ~/.config/k9s/views.yaml

# Verify (k9s reads these on startup)
k9s
```

If you prefer copy over symlink:

```bash
cp k9s/*.yaml ~/.config/k9s/
```

## The four namespaces this project uses

| Namespace | What lives here |
|-----------|----------------|
| `real-time-ml` | All finance-domain services (`transactions`, `behavioral_features`, `decisioner`, `drift_monitor`, `outcome_collector`, plus retained `news`/`news-sentiment`) |
| `mlflow` | MLflow tracking server (custom Deployment per ADR 005) |
| `kafka` | Strimzi-managed Kafka cluster |
| `risingwave` | RisingWave frontend, compute, meta, plus bundled MinIO |

## Fastest paths to common things

| Goal | Keystroke / command |
|------|---------------------|
| Jump to finance pods | `Shift+F` |
| Jump to MLflow pods | `Shift+M` |
| Jump to Kafka pods | `Shift+K` |
| Jump to RisingWave pods | `Shift+R` |
| All Services across the cluster | `Shift+S` |
| All Deployments across the cluster | `Shift+D` |
| All Pods (any namespace) | `Shift+L` |
| Only application services (filtered by `tier=service`) | `:svcs<Enter>` |
| Only infra deployments (filtered by `tier=infra`) | `:infra<Enter>` |
| Logs from decisioner pod | `:dec<Enter>` then `l` |
| Logs from transactions producer | `:txn<Enter>` then `l` |
| Logs from behavioral_features | `:feat<Enter>` then `l` |

## In-pod debugging from k9s

| Goal | Keystroke (on the selected pod) |
|------|-------------------------------|
| Stream logs | `l` |
| Follow logs (tail) | `l` then `f` |
| Open a shell inside the container | `s` |
| Port-forward to local | `Shift+F` (after selecting a Service) |
| Edit the resource YAML live | `e` |
| Describe (the kubectl-describe view) | `d` |
| Delete (with confirm) | `Ctrl+D` |

## Inspecting the data path

Common diagnostic flow when the smoke test fails phase 2:

1. `Shift+F` to view finance namespace pods
2. Confirm `transactions-*` and `behavioral-features-*` are `Running` + Ready
3. If a pod is `CrashLoopBackOff`: select it, `l` to read logs, then `d`
   to see recent events
4. `Shift+K` to Kafka namespace; confirm the `kafka-e11b-*` broker pod is Ready
5. `Shift+R` to RisingWave; confirm `frontend`, `compute`, `meta`, `minio` pods are Ready
6. To check the actual data in RisingWave, use `psql` (not k9s):
   `psql -h localhost -p 4567 -U root -d dev` then
   `SELECT count(*) FROM behavioral_features;`

## Custom columns documented

The repo's `views.yaml` adds these custom columns (visible by default):

- **Pods**: `IMAGE` column — full image tag including registry. Useful when
  rolling a new version; you can confirm the running tag matches the
  intended one without `kubectl describe`.
- **Services**: `SELECTOR` column — shows the label selector this Service
  uses to route to Pods. Diagnoses "service has no endpoints" misconfigs.
- **Deployments**: `IMAGE` + `LABELS` columns — confirm what's deployed
  and what labels are present (since our project filters on
  `tier=service` and `app.kubernetes.io/part-of`).

## Labels every resource in this project carries

By convention every Pod / Deployment / Service we ship has at minimum:

- `app=<service-name>` (e.g. `app=decisioner`)
- `tier=service` or `tier=infra`
- `app.kubernetes.io/part-of=realtime-credit-decisioning`

So the k9s alias `:svcs` filters to `tier=service` and shows just our
application Deployments. `:infra` shows the infrastructure side.

## When k9s isn't enough

For multi-namespace cross-cuts (e.g., "all Deployments using image tag
`v1.0.0`") use `kubectl` directly:

```bash
kubectl get deployments -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}={.spec.template.spec.containers[0].image}{"\n"}{end}' \
  | grep v1.0.0
```

For sustained log tailing across multiple pods, use `stern` (a multi-pod
log tailer) — not yet installed in the devcontainer; add when needed.
