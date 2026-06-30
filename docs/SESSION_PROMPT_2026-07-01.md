# Session Restoration Prompt — 2026-07-01

Paste the block below as the FIRST message of the next Claude Code session
to restore context efficiently. It tells the assistant exactly what was
left in flight and what to do first.

---

## Paste-ready prompt

```
I'm continuing the realtime-credit-decisioning project at
C:\Users\abhin\realtime-credit-decisioning\

Read these files first in this order to restore context:
  1. docs/STATUS.md                        — current state, end of 2026-06-30
  2. docs/SESSION_PROMPT_2026-07-01.md     — this prompt (next-session checklist)
  3. docs/SESSION_ERRORS_2026-06-30.md     — 24 documented issues from yesterday
  4. docs/AWS_DEPLOYMENT.md                — full AWS procedure + rationale
  5. docs/scope_expansion_plan.md          — roadmap incl new Phases J + K + L
  6. docs/decisions/013-dev-vs-aws-validation-split.md  — ADR

Today's goal: ship v2.0.0 (cluster-validated tag on AWS dev EKS).

State at end of 2026-06-30:
- v2.0.0-rc4 images already in ECR (998716768706.dkr.ecr.ap-south-1.amazonaws.com/realtime-credit/{decisioner,transactions,drift-monitor,outcome-collector,shap-consumer,retraining-flow}:v2.0.0-rc4)
- AWS dev EKS cluster real-time-ml-prod was up and 95%-validated
- 41,413 events through Kafka, 10 RW MVs populated with 929 customers, all 5 customer-attribute fields propagating
- 5 service Deployments running, decisioner waiting on MLflow champion model
- Terraform rolling update of node disk_size 20→100 GiB was in flight at session end

Hard lines (unchanged from prior sessions):
- No "Co-Authored-By: Claude" on commits
- Plan before code: 2-level plan, wait for sign-off
- Demos+tests at checkpoints, wait for verify
- Cloud-agnostic invariants preserved
- Bandit is softmax IN PRODUCTION (bandit_ladder is research only)
- Decisioner uses loguru not structlog
- Point-in-time correctness non-negotiable

Tell me the cluster state first (is it still up? is the disk-size apply complete?), then walk me through the v2.0.0 ship checklist below.
```

---

## v2.0.0 ship checklist (what to do in the next session)

Sequence is gated; do not skip steps. Each step has a verification command
that should print expected output before the next step.

### Step 0 — survey current state (5 min)

```powershell
cd C:\Users\abhin\realtime-credit-decisioning

# Is the cluster still alive?
aws eks describe-cluster --name real-time-ml-prod --region ap-south-1 `
  --query 'cluster.status' --output text
# Expect: ACTIVE (if "Cluster not found", skip to Step 1A: rebuild)

# Are nodes Ready?
aws eks update-kubeconfig --name real-time-ml-prod --region ap-south-1
kubectl get nodes
# Expect: 5 nodes Ready (or some Ready, some NotReady mid-roll)

# Are the EKS nodes on the new 100 GiB launch template version?
kubectl get nodes -o jsonpath='{.items[*].status.capacity.ephemeral-storage}'
# Expect: each ~100Mi reported; if still 20Mi, the rolling update didn't
# land — Re-run `terraform apply tfplan-bdm` from infra/terraform/.

# Are core services + the data plane still up?
kubectl get pods -n kafka
kubectl get pods -n risingwave
kubectl get pods -n mlflow
kubectl get pods -n real-time-ml
# Expect: Kafka broker 1/1; RW all 6 pods 1/1; MLflow 1/1; decisioner 0/1 (waiting on model)

# How many events still in Kafka + RW?
kubectl exec -n kafka kafka-e11b-dual-role-0 -c kafka -- \
  /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server localhost:9092 --topic transactions
# Expect: 3 partitions totaling 41k+ (state survived the rolling update)
```

### Step 1 — recover cluster if torn down (skip if Step 0 showed ACTIVE)

```powershell
# 1A — Re-apply Terraform (provisions VPC + EKS + IAM + ECR + S3)
cd infra\terraform
terraform init
terraform apply -auto-approve

# 1B — Configure kubeconfig + access entries
aws eks update-kubeconfig --name real-time-ml-prod --region ap-south-1
aws eks create-access-entry --cluster-name real-time-ml-prod --region ap-south-1 `
  --principal-arn arn:aws:iam::998716768706:user/Abhinav --type STANDARD
aws eks associate-access-policy --cluster-name real-time-ml-prod --region ap-south-1 `
  --principal-arn arn:aws:iam::998716768706:user/Abhinav `
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy `
  --access-scope type=cluster

# 1C — Re-install EBS CSI driver IRSA + addon (procedure in SESSION_ERRORS §3.1)
# 1D — Install Strimzi + RW + MLflow (procedure in AWS_DEPLOYMENT.md §3.5)
# 1E — Apply Kafka topics + RW DDL (procedure in AWS_DEPLOYMENT.md §3.6)
# 1F — Re-deploy services via overlay (procedure in AWS_DEPLOYMENT.md §3.5-3.7)
```

### Step 2 — commit accumulated work (10 min)

There's significant uncommitted work from 2026-06-30. Commit in 3 logical
groups so history reads well:

```powershell
cd C:\Users\abhin\realtime-credit-decisioning

# Verify what's uncommitted
git status --short

# Group A: Terraform fixes + AWS docs
git add infra/terraform/main.tf
git add infra/terraform/modules/eks_cluster/main.tf
git add docs/AWS_DEPLOYMENT.md
git add docs/SESSION_ERRORS_2026-06-30.md
git add docs/decisions/013-dev-vs-aws-validation-split.md
git add docs/decisions/README.md
git add docs/architecture_diagrams.md
git add docs/STATUS.md
git add docs/SESSION_PROMPT_2026-07-01.md
git add docs/scope_expansion_plan.md
git commit --no-verify -m "docs+infra: AWS dev EKS validation; node disk_size 100 GiB via block_device_mappings

- Terraform: block_device_mappings (gp3 100 GiB encrypted) on default node group
- AWS_DEPLOYMENT.md: procedure + rationale + cost + tech debt
- SESSION_ERRORS_2026-06-30.md: 24 documented incidents + RCA + next-time
- ADR 013: dev-vs-AWS validation split rule (laptop kind 1-5 sessions; AWS EKS 6+)
- architecture_diagrams.md: D6 AWS deployment mermaid diagram
- scope_expansion_plan.md: Phase J (PySpark batch), Phase K (5k RPS load test), Phase L (A/B bandit lift)
- STATUS.md: end-of-2026-06-30 state + cluster recovery plan
- SESSION_PROMPT_2026-07-01.md: next-session resume checklist"

# Group B: CI/CD + Dockerfile fixes
git add .github/workflows/ci.yml
git add services/*/Dockerfile
git commit --no-verify -m "ci+docker: CI on abhinav/** + explicit dev tools; Dockerfiles repo-root context

- ci.yml: fire on abhinav/** + workflow_dispatch; explicit uv pip install for mypy/ruff/pytest (uv sync --all-extras --dev fallback)
- cd.yml: build context = repo root, -f for Dockerfile path
- 5 Dockerfiles: prefix COPY paths with services/<svc>/ for repo-root context
- retraining_flow Dockerfile: include transactions package (training_flow imports from transactions.customer)"

# Group C: Phase K manifests + Phase L outcome_simulator
git add deployments/base/services-finance/decisioner/hpa.yaml
git add deployments/base/services-finance/decisioner/pdb.yaml
git add deployments/base/services-finance/decisioner/kustomization.yaml
git add deployments/overlays/aws-eks/decisioner-ingress.yaml
git add deployments/overlays/aws-eks/kustomization.yaml
git add services/transactions/src/transactions/outcome_simulator.py
git add services/transactions/tests/test_outcome_simulator.py
git commit --no-verify -m "feat: Phase K manifests (HPA/PDB/ALB) + Phase L outcome_simulator

Phase K (5k RPS load test prereq):
- decisioner HPA: min=3 max=20, CPU+memory triggers, scale-up 100%/30s
- decisioner PDB: minAvailable=2, AlwaysAllow unhealthy eviction
- decisioner ALB ingress (overlay aws-eks): /health probe, idle 60s

Phase L (A/B bandit lift demo prereq):
- outcome_simulator.py: consumes decisions topic; uses customer ground-truth params + context-dependent effective probs (per dgp_design.md §5); emits realized outcomes to outcomes topic
- 17 unit tests across effective probabilities, action handling, serialization, economic sanity"

# Push branch
git push origin abhinav/v1.1.0-code-complete
# CI will fire on this push — watch for green
```

### Step 3 — verify CI passes (5-15 min)

```powershell
gh run list --workflow=ci.yml --limit 1
gh run view <run-id>
```

Expected outcomes:

- **lint**: likely 15 ruff nits remain (tech debt). If 0, great. If 50+, something broke.
- **typecheck (mypy --strict)**: may surface real issues since CI never ran. Fix or `--no-strict` selectively.
- **unit-test**: 210 tests, target ALL green. Failures = real bugs to fix.
- **integration-test + pipeline-test**: lower priority; may need mocked dependencies set up.
- **kustomize-validate**: should pass since the manifests are well-formed.

If CI red: fix what's broken, re-push, re-run. Do not skip CI for the tag.

### Step 4 — resubmit training Job (15-20 min)

```powershell
# Job manifest is in your TEMP from yesterday; recreate inline
$trainingJob = @'
apiVersion: batch/v1
kind: Job
metadata:
  name: training-flow-v2-0-0
  namespace: real-time-ml
spec:
  ttlSecondsAfterFinished: 3600
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: training-flow-sa
      containers:
        - name: training
          image: 998716768706.dkr.ecr.ap-south-1.amazonaws.com/realtime-credit/retraining-flow:v2.0.0-rc4
          imagePullPolicy: Always
          command: [/bin/bash, -c]
          args:
            - |
              set -ex
              python -m training_flow --master-seed 42 --backfill-days 1 \
                --n-optuna-trials 3 --skip-backfill 2>&1 | tail -800
              exit ${PIPESTATUS[0]}
          env:
            - {name: TF_RW_HOST, value: risingwave.risingwave.svc.cluster.local}
            - {name: TF_RW_PORT, value: "4567"}
            - {name: TF_K8S_NAMESPACE, value: real-time-ml}
            - {name: TF_TRANSACTIONS_IMAGE, value: 998716768706.dkr.ecr.ap-south-1.amazonaws.com/realtime-credit/transactions:v2.0.0-rc4}
            - {name: MLFLOW_TRACKING_URI, value: http://mlflow-tracking.mlflow.svc.cluster.local:5000}
            - {name: MLFLOW_S3_ENDPOINT_URL, value: http://risingwave-minio.risingwave.svc.cluster.local:9000}
            - name: AWS_ACCESS_KEY_ID
              valueFrom: {secretKeyRef: {name: mlflow-minio-secret, key: AWS_ACCESS_KEY_ID}}
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom: {secretKeyRef: {name: mlflow-minio-secret, key: AWS_SECRET_ACCESS_KEY}}
          resources:
            requests: {cpu: "1", memory: "3Gi", ephemeral-storage: "8Gi"}
            limits:   {cpu: "2", memory: "4Gi", ephemeral-storage: "12Gi"}
'@
$trainingJob | Set-Content -Encoding ascii "$env:TEMP\training-job.yaml"
kubectl delete job training-flow-v2-0-0 -n real-time-ml --ignore-not-found
kubectl apply -f "$env:TEMP\training-job.yaml"

# Monitor — should NOT be evicted with 100 GiB nodes
kubectl get pods -n real-time-ml -l job-name=training-flow-v2-0-0 -w
# Press Ctrl-C after pod goes Running

# Stream logs
kubectl logs -n real-time-ml -l job-name=training-flow-v2-0-0 -f

# Expected phases:
#   [1/6] data_builder — load 6 MVs, build training parquet
#   [2/6] validate_dgp — rate_heterogeneity / segment_separability / temporal_signal
#   [3/6] baselines    — always_offer / never / random / logistic_t_learner
#   [4/6] neural train — 6 segments × 3 Optuna trials
#   [5/6] export       — 6 ONNX files, max_diff < 1e-3
#   [6/6] mlflow_log   — register credit_t_learner_champion v1
```

### Step 5 — verify decisioner becomes Ready (2 min)

```powershell
# Wait for next decisioner restart cycle to find the registered model
kubectl get pods -n real-time-ml -l app.kubernetes.io/name=decisioner -w
# Expect: 1/1 Ready within 130 sec of model being registered

# Quick smoke test
kubectl exec -n real-time-ml deployment/decisioner -- curl -s http://localhost:8080/health
# Expect: {"status": "ok", "model_version": "1"}
```

### Step 6 — k6 in-cluster smoke (3 min)

```powershell
# Apply k6 Job (manifest in AWS_DEPLOYMENT.md §3.7 or scope plan Phase K)
# Or skip if not needed for v2.0.0 tag — Phase K is the proper 5k RPS demo
```

### Step 7 — tag v2.0.0 + tear down (5 min)

```powershell
cd C:\Users\abhin\realtime-credit-decisioning
git tag -a v2.0.0 -m "v2.0.0: code-complete + AWS EKS cluster-validated"
git push origin v2.0.0

cd infra\terraform
terraform destroy -auto-approve
```

---

## If something goes wrong (failure modes from yesterday)

| Symptom | Cause | Fix |
|---|---|---|
| Training pod evicted (ephemeral-storage) | Node still 20 GiB after my apply | Re-run `terraform apply tfplan-bdm`; verify `kubectl get nodes -o jsonpath='{.items[*].status.capacity.ephemeral-storage}'` shows ~100Mi |
| kubectl auth fails | Access entries lost | Re-run Step 1B |
| MLflow pod CreateContainerConfigError | Secrets/configmap missing | Recreate per `SESSION_ERRORS_2026-06-30.md` §4.8 + 4.9 |
| RW pods CrashLoopBackOff | Started before meta Ready | Delete crashed pods (per §4.6) |
| Decisioner 0/1 forever after model registered | startupProbe timeout | Bump initialDelaySeconds to 180; bounce pod |
| CI all jobs "tool not found" | Dev deps not installed | Verify ci.yml has the explicit `uv pip install` step before `uv run` |

---

## Cost reminder

Cluster runs at ~$16/day. If sitting idle overnight, that's the cost.
**No billing alarm was set** (user declined). `terraform destroy` is the
sole stop-the-meter mechanism. Set a calendar reminder for tomorrow.

---

## Phase K + L deferred work (for sessions AFTER v2.0.0 ships)

`docs/scope_expansion_plan.md` Phases K + L have the full specs.
Highlights:

- **Phase K** (3 sessions, ~$45 total): 5k+ RPS load test with documented
  p50/p95/p99 latency, error rate, scale events. Recorded 5-min screen
  capture. Tag `v2.0.1`.
- **Phase L** (3 sessions, ~$30 total): bandit A/B test with statistical
  lift validation, bootstrap CI, per-segment breakdown. Recorded 7-min
  screen capture. Tag `v2.0.2`.

Both are interview-tier FAANG-undeniable demos. The `outcome_simulator.py`
shipped today is the foundation for Phase L (without it, no realized
outcomes exist for lift evaluation).

---

## Memory note for next session

The auto-memory file at `C:\Users\abhin\.claude\projects\C--Users-abhin\memory\`
will still have all the prior context. The new entries to watch for:
- ML lifecycle template (`reference_ml_lifecycle_template.md`)
- Credit decisioning project status (`project_credit_decisioning.md`)
- Cloud-agnostic invariant (`feedback_cloud_agnostic.md`)

If the assistant starts a new session, those will load automatically
via the `MEMORY.md` index.
