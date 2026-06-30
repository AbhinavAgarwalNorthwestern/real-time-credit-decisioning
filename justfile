# Real-Time Credit-Decisioning Platform — task automation.
# Run `just <command>` to execute. Run `just` (no args) to list everything.
# Install just: `winget install Casey.Just`  (Windows)
#               `brew install just`            (macOS)
#               `cargo install just`           (any)

default:
    @just --list

# ---------------------------------------------------------------------------
# Linting, formatting, type-checking
# ---------------------------------------------------------------------------

lint:
    uv run ruff check .

fmt:
    uv run ruff format .

fmt-check:
    uv run ruff format --check .

typecheck:
    uv run mypy --strict services/ infra/

check: lint fmt-check typecheck

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test:
    uv run pytest --import-mode=importlib -q

test-v:
    uv run pytest --import-mode=importlib -v --tb=short

test-integration:
    uv run pytest --import-mode=importlib -m integration -v --tb=short

test-pipeline:
    uv run pytest --import-mode=importlib -m pipeline -v --tb=short

# Run finance-pipeline smoke test: brings up the finance services locally and
# confirms /decide responds with a valid decision under the SLO.
test-finance-smoke:
    bash scripts/smoke_test_finance.sh

# ---------------------------------------------------------------------------
# Local development — single-service hot reload
# ---------------------------------------------------------------------------

# Run a single service locally. Usage: just dev service=transactions
dev service:
    uv run python services/{{service}}/src/{{service}}/main.py

# ---------------------------------------------------------------------------
# Docker / images
# ---------------------------------------------------------------------------

# Build a single service image. Usage: just docker-build image=decisioner env=dev
docker-build image env="dev":
    ./scripts/build-and-push-image.sh {{image}} {{env}}

# Build every active service image.
docker-build-all env="dev":
    ./scripts/build-and-push-image.sh transactions {{env}}
    ./scripts/build-and-push-image.sh decisioner {{env}}
    ./scripts/build-and-push-image.sh drift-monitor {{env}}
    ./scripts/build-and-push-image.sh outcome-collector {{env}}
    ./scripts/build-and-push-image.sh shap-consumer {{env}}
    ./scripts/build-and-push-image.sh retraining-flow {{env}}

# ---------------------------------------------------------------------------
# Kubernetes — local kind cluster
# ---------------------------------------------------------------------------

kind-up:
    bash deployments/dev/kind/create_cluster.sh

kind-down:
    kind delete cluster --name real-time-ml

# Apply the local-kind overlay (base + local patches)
k8s-apply-local:
    kubectl apply -k deployments/overlays/local-kind

# Apply the AWS EKS overlay (requires aws-cli + kubeconfig pointing at EKS)
k8s-apply-aws:
    kubectl apply -k deployments/overlays/aws-eks

# Diff the local-kind overlay against the live cluster (dry-run)
k8s-diff-local:
    kubectl diff -k deployments/overlays/local-kind

# Validate any overlay by rendering it and checking with kubeval
k8s-validate overlay:
    kubectl kustomize deployments/overlays/{{overlay}} | kubeval --strict

# ---------------------------------------------------------------------------
# MLflow operations
# ---------------------------------------------------------------------------

# Apply the mlflow-minio-secret from .env.local credentials (idempotent).
# Run after rotating MinIO credentials in .env.local.
mlflow-secret:
    bash scripts/create-mlflow-secret.sh

# Restart MLflow tracking pod to pick up new env / secrets.
mlflow-restart:
    kubectl -n mlflow rollout restart deployment/mlflow-tracking

# Open MLflow UI in browser (port-forward)
mlflow-ui:
    kubectl -n mlflow port-forward svc/mlflow-tracking 5000:80

# ---------------------------------------------------------------------------
# Terraform — AWS infrastructure
# ---------------------------------------------------------------------------

tf-init:
    cd infra/terraform && terraform init

tf-plan:
    cd infra/terraform && terraform plan -out=tfplan

tf-apply:
    cd infra/terraform && terraform apply tfplan

# Destroy AWS infra (charges $0/h once down — be careful)
tf-destroy:
    cd infra/terraform && terraform destroy

# ---------------------------------------------------------------------------
# Load testing
# ---------------------------------------------------------------------------

# Run the k6 load test against the local decision-api
load-test-local:
    k6 run scripts/load_test.js --env BASE_URL=http://localhost:8080

# Run the k6 load test against a specific URL
load-test target:
    k6 run scripts/load_test.js --env BASE_URL={{target}}

# ---------------------------------------------------------------------------
# Training pipeline
# ---------------------------------------------------------------------------

# Run the Day-2 training pipeline (offline, no cluster needed)
train seed="42" trials="5":
    uv run python -m training_flow --master-seed {{seed}} --backfill-days 7 --skip-backfill --n-optuna-trials {{trials}}

# ---------------------------------------------------------------------------
# Metaflow — batch orchestration
# ---------------------------------------------------------------------------

# Run the retraining flow locally (no K8s scheduling)
flow-retrain-local:
    uv run python -m retraining_flow.flow run

# Run the retraining flow on Kubernetes (cloud-agnostic)
flow-retrain-k8s:
    uv run python -m retraining_flow.flow --with kubernetes run

# Run the retraining flow on AWS Batch (AWS overlay only)
flow-retrain-aws-batch:
    METAFLOW_PROFILE=aws uv run python -m retraining_flow.flow --with batch run

# Register the flow as an Argo Workflow on K8s
flow-register-argo:
    uv run python -m retraining_flow.flow --with kubernetes argo-workflows create

# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------

# Serve the docs locally (assumes mkdocs is installed)
docs-serve:
    uv run mkdocs serve
