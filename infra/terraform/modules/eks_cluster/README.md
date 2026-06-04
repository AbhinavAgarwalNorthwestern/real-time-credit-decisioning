# eks_cluster module — placeholder

EKS managed control plane + at least one node group (Fargate or EC2).

## Day 8 implementation plan

Use `terraform-aws-modules/eks/aws`. Configure:

- managed node group with `m6i.large` (4 vCPU / 16 GB) baseline
- IRSA enabled (OIDC provider auto-created)
- aws-auth ConfigMap rendered with the user's IAM role
- AWS Load Balancer Controller addon
- EBS CSI driver addon (for `gp3` storage class used by RisingWave +
  MLflow Postgres)
- VPC CNI addon

## Inputs (planned)

| Variable | Description |
|----------|-------------|
| `vpc_id` | From the `vpc` module |
| `subnet_ids` | Private subnets only |
| `cluster_name` | Match the env.shared `EKS_CLUSTER_NAME` |
| `cluster_version` | EKS minor version (1.30+) |

## Outputs (planned)

| Output | Description |
|--------|-------------|
| `cluster_endpoint` | For kubeconfig |
| `oidc_provider_arn` | Consumed by `iam_irsa` module |
| `node_group_role_arn` | For aws-auth ConfigMap |
