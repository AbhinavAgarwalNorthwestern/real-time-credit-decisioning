# Terraform — AWS infrastructure for the realtime credit-decisioning platform

Provisions everything *outside* the K8s cluster. Application workloads
(K8s Deployments, Services, ConfigMaps) are managed via Kustomize
overlays at `deployments/overlays/aws-eks/`, NOT this Terraform.

This split is intentional — see ADR 006
(`docs/decisions/006-base-overlays-kustomize-not-helm.md`) for the
reasoning behind separating *infrastructure provisioning* (Terraform)
from *workload deployment* (Kustomize).

## What this creates

| Module | Purpose |
|--------|---------|
| `modules/vpc/` | VPC + public/private subnets across ≥2 AZs |
| `modules/eks_cluster/` | EKS managed control plane + node group |
| `modules/ecr/` | One ECR repository per containerized service |
| `modules/s3/` | MLflow artifacts bucket + decision audit log bucket |
| `modules/iam_irsa/` | IRSA service-account mappings (pods → S3) |
| `modules/mlflow_server/` | (Optional) MLflow on EC2 if not running in-cluster |

## Status

**Skeleton (Day 0).** Module files contain README-only stubs. Day 8
populates them when the AWS overlay work begins.

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your account-specific values
just tf-init
just tf-plan
just tf-apply   # destructive — creates billable AWS resources
```

## Cost note

Once applied, EKS has a baseline **$0.10/hour ($73/mo)** control-plane
cost plus node-group instance hours. ECR, IAM, S3 at our scale are
effectively free. Use `just tf-destroy` between work sessions to keep
the running bill near zero — S3 buckets and ECR images are tagged for
quick recreate.

## Why Terraform (and not CDK or Pulumi)?

Will be documented in ADR 010 (`010-terraform-not-cdk-not-pulumi.md`)
when Day 8 begins. Short version: same IaC tool as `project_01` for
cross-project consistency; cloud-agnostic to a useful degree (same
patterns reusable for GKE/AKS); state management is well-trodden.
